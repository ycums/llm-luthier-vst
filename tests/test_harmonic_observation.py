"""harness.harmonic_observation のテスト（P1-02 #47）。

Issue #47 の完了条件を検証する：
- ゴールデン音源セット全音源に対し、倍音構造の時間変化を定量化した観測結果を出力できる
- 観測項目に「倍音の振幅比が時間的にどれだけ動くか」（harmonic_amplitude_motion）と
  「非整数次成分の割合」（non_integer_partial_ratio）が含まれる
- 音高を持たない音源（打楽器・ノイズ）が観測から除外されず、倍音構造を定義できない音源
  として明示的に記録される（status="no_pitch"、欠測はvalidな状態）
- 観測結果が機械可読な形式（JSON、スキーマ）で出力され、同一入力からは同一出力が出る
- 算出条件（フレーム長・ホップ長・倍音の同定方法等）が出力に記録されている
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import numpy as np
import pytest
import soundfile as sf

from harness.cli import main as cli_main
from harness.fixture_gen import SAMPLE_RATE, generate_all
from harness.harmonic_observation import (
    ManifestError,
    estimate_partial_amplitudes,
    observe_corpus,
    observe_harmonic_structure,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "harness" / "harmonic_observation.schema.json"
)
REAL_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "corpus" / "manifest.json"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _pair_meta(meta: dict, name: str) -> dict:
    for p in meta["pairs"]:
        if p["name"] == name:
            return p
    raise AssertionError(f"pair {name} not found")


def _stft_mag(signal: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
    import librosa

    return np.abs(librosa.stft(signal, n_fft=frame_length, hop_length=hop_length))


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("harmonic_fixtures")
    meta = generate_all(out_dir, seed=0)
    return out_dir, meta


@pytest.fixture
def white_noise_wav(tmp_path: Path) -> Path:
    """音高を持たない音源（ノイズ）の合成フィクスチャ。"""
    rng = np.random.default_rng(1)
    data = rng.normal(0.0, 0.1, SAMPLE_RATE).reshape(-1, 1)  # 1秒
    path = tmp_path / "white_noise.wav"
    sf.write(str(path), data, SAMPLE_RATE, subtype="FLOAT")
    return path


@pytest.fixture
def dynamic_ratio_wav(tmp_path: Path) -> Path:
    """基音は一定振幅、2次倍音の振幅が時間で0→1にランプする既知信号。

    倍音の振幅比（2次/基音）が時間的に大きく動く音の代表として使う。フィクスチャ生成器
    （harness/fixture_gen.py）は全ペアで倍音比を時間一定にしか生成しないため、この
    テスト専用の信号をここで直接合成する（AGENTS.md 第3節：期待する入出力を先に定める）。
    """
    f0 = 220.0
    duration_s = 0.5
    num_s = int(SAMPLE_RATE * duration_s)
    t = np.arange(num_s) / SAMPLE_RATE
    ramp = np.linspace(0.0, 1.0, num_s)
    signal = np.sin(2.0 * np.pi * f0 * t) + ramp * np.sin(2.0 * np.pi * 2.0 * f0 * t)
    path = tmp_path / "dynamic_ratio.wav"
    sf.write(str(path), signal.reshape(-1, 1), SAMPLE_RATE, subtype="FLOAT")
    return path


def test_partial_amplitudes_recover_static_harmonic_profile(fixtures) -> None:
    """静的な倍音プロファイルを持つ既知信号で、抽出した倍音振幅比がプロファイルに近い。"""
    out_dir, meta = fixtures
    p = _pair_meta(meta, "f_f0_glide")
    from harness.audio_io import read_wav, to_mono
    from harness.metrics import estimate_f0_contour

    buffer = to_mono(read_wav(out_dir / p["target"]))
    signal = buffer.data[:, 0]
    f0, voiced = estimate_f0_contour(signal, buffer.sample_rate)
    mag = _stft_mag(signal, 2048, 256)

    amps = estimate_partial_amplitudes(mag, buffer.sample_rate, 2048, f0, voiced, n_harmonics=3)

    assert amps.shape == (3, min(mag.shape[1], len(f0)))
    voiced_cols = np.where(voiced[: amps.shape[1]])[0]
    assert len(voiced_cols) > 0
    # _HARMONIC_PROFILE = [1.00, 0.70, 0.50, ...] -> 2次/基音 ~0.70, 3次/基音 ~0.50
    ratio_h2 = np.nanmean(amps[1, voiced_cols] / amps[0, voiced_cols])
    ratio_h3 = np.nanmean(amps[2, voiced_cols] / amps[0, voiced_cols])
    assert ratio_h2 == pytest.approx(0.70, abs=0.15)
    assert ratio_h3 == pytest.approx(0.50, abs=0.15)


@pytest.fixture
def bin_misaligned_pure_tone_wav(tmp_path: Path) -> Path:
    """単一倍音のみを持つ純音で、基本周波数がFFTビン境界に一致しないもの。

    440Hz @ 44100Hz・frame_length=2048（既定）ではビン幅が約21.5Hzのため、440Hzは
    ちょうどビン中心に乗らず、真の信号エネルギーが隣接ビンに分散する（スペクトル漏れ）。
    ここでビンを1本だけ拾うと本来の倍音振幅を過小評価し、非整数次成分の割合を
    過大評価してしまう（`corpus/audio/sine_440hz.ogg` の実測で発覚したバグの再現）。
    """
    duration_s = 2.0
    num_s = int(44100 * duration_s)
    t = np.arange(num_s) / 44100
    signal = np.sin(2.0 * np.pi * 440.0 * t)
    path = tmp_path / "pure_tone_440.wav"
    sf.write(str(path), signal.reshape(-1, 1), 44100, subtype="FLOAT")
    return path


def test_non_integer_partial_ratio_is_near_zero_for_bin_misaligned_pure_tone(
    bin_misaligned_pure_tone_wav: Path,
) -> None:
    """単一倍音の純音は、ビン境界に整列していなくても非整数次成分の割合がほぼ0になる。

    探索窓内の最大値1本だけを倍音振幅とすると、隣接ビンに漏れた分のエネルギーが
    「非整数次成分」として誤ってカウントされる（過大評価）。振幅は窓内のエネルギー
    合計（二乗和の平方根）から求めること。
    """
    record = observe_harmonic_structure(bin_misaligned_pure_tone_wav)

    assert record["status"] == "ok"
    ratio = record["non_integer_partial_ratio"]["value"]
    assert ratio is not None
    assert ratio == pytest.approx(0.0, abs=0.05)


def test_harmonic_amplitude_motion_is_missing_for_single_partial_tone(
    bin_misaligned_pure_tone_wav: Path,
) -> None:
    """基音しか持たない音源では、倍音振幅比の変動は欠測として出力される。

    2次以降の倍音位置に実体の信号がない場合、拾われるのはノイズフロアのみであり、
    その振幅比はほぼ0付近でわずかな変動係数（分母が極小のため見かけ上巨大になりうる）を
    示す。これを実在する倍音の「動き」として数値化すると、単一倍音の音源（例：
    corpus/audio/sine_440hz.ogg）でノイズフロアの揺らぎだけから桁外れの値が出る
    （実測で発覚したバグの再現）。基音に対する平均振幅比が閾値未満の倍音は
    「観測されていない」として変動係数の平均対象から除外すること。
    """
    record = observe_harmonic_structure(bin_misaligned_pure_tone_wav)

    assert record["status"] == "ok"
    motion = record["harmonic_amplitude_motion"]
    assert motion["value"] is None
    assert motion["missing_reason"] is not None


def test_harmonic_amplitude_motion_is_higher_for_dynamic_ratio_signal(
    fixtures, dynamic_ratio_wav: Path
) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "f_f0_glide")

    static_record = observe_harmonic_structure(out_dir / p["target"])
    dynamic_record = observe_harmonic_structure(dynamic_ratio_wav)

    assert static_record["status"] == "ok"
    assert dynamic_record["status"] == "ok"
    static_motion = static_record["harmonic_amplitude_motion"]["value"]
    dynamic_motion = dynamic_record["harmonic_amplitude_motion"]["value"]
    assert static_motion is not None
    assert dynamic_motion is not None
    assert dynamic_motion > static_motion


def test_non_integer_partial_ratio_is_higher_for_noisy_tone_than_pure_tone(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "d_noise")

    pure_record = observe_harmonic_structure(out_dir / p["target"])
    noisy_record = observe_harmonic_structure(out_dir / p["candidate"])

    assert pure_record["status"] == "ok"
    assert noisy_record["status"] == "ok"
    pure_ratio = pure_record["non_integer_partial_ratio"]["value"]
    noisy_ratio = noisy_record["non_integer_partial_ratio"]["value"]
    assert pure_ratio is not None
    assert noisy_ratio is not None
    assert noisy_ratio > pure_ratio


def test_pitchless_source_is_recorded_explicitly_not_excluded(white_noise_wav: Path) -> None:
    record = observe_harmonic_structure(white_noise_wav)

    assert record["status"] == "no_pitch"
    assert record["voiced_frame_ratio"] < 0.15
    assert record["harmonic_amplitude_motion"]["value"] is None
    assert record["harmonic_amplitude_motion"]["missing_reason"] is not None
    assert record["non_integer_partial_ratio"]["value"] is None


def test_observe_harmonic_structure_output_is_valid_against_schema(
    fixtures, white_noise_wav: Path
) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")
    schema = _load_schema()

    ok_record = observe_harmonic_structure(out_dir / p["target"])
    no_pitch_record = observe_harmonic_structure(white_noise_wav)

    jsonschema.validate(instance=ok_record, schema=schema)
    jsonschema.validate(instance=no_pitch_record, schema=schema)


def test_calc_conditions_records_frame_hop_and_harmonic_count(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    record = observe_harmonic_structure(out_dir / p["target"], n_harmonics=5)

    conditions = record["calc_conditions"]
    assert conditions["n_harmonics"] == 5
    assert conditions["f0_estimation"]["frame_length"] > 0
    assert conditions["f0_estimation"]["hop_length"] > 0
    assert conditions["f0_estimation"]["name"] == "pyin"


def test_repeated_calls_are_bit_exact(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "f_f0_glide")

    a = observe_harmonic_structure(out_dir / p["target"])
    b = observe_harmonic_structure(out_dir / p["target"])

    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _write_wav(path: Path, sample_rate: int = 44100, num_samples: int = 4410) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = 0.1 * np.sin(2 * np.pi * 440 * np.arange(num_samples) / sample_rate)
    sf.write(str(path), data.reshape(-1, 1), sample_rate, subtype="PCM_16")


@pytest.fixture
def mixed_manifest(tmp_path: Path) -> Path:
    """ok / missing_audio / error の3種のステータスを再現する最小マニフェスト。"""
    repo_root = tmp_path
    ok_path = repo_root / "corpus" / "audio" / "ok_source.wav"
    _write_wav(ok_path, sample_rate=44100, num_samples=44100)

    error_path = repo_root / "corpus" / "audio" / "error_source.wav"
    # WAVとして読めない壊れたファイルを置き、観測処理そのものが例外で失敗するケースを
    # 再現する（意図的に error ステータスを再現する）。
    error_path.parent.mkdir(parents=True, exist_ok=True)
    error_path.write_bytes(b"not a real wav file")

    manifest = {
        "entries": [
            {
                "id": "ok_entry",
                "format": "wav",
                "bundled": True,
                "bundled_path": "corpus/audio/ok_source.wav",
                "retrieval": None,
            },
            {
                "id": "missing_entry",
                "format": "ogg",
                "bundled": False,
                "bundled_path": None,
                "retrieval": "テスト用: 意図的に取得していない音源",
            },
            {
                "id": "error_entry",
                "format": "wav",
                "bundled": True,
                "bundled_path": "corpus/audio/error_source.wav",
                "retrieval": None,
            },
        ]
    }
    manifest_path = repo_root / "corpus" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def test_observe_corpus_writes_one_json_per_entry_and_an_index(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"

    index = observe_corpus(mixed_manifest, out_dir)

    assert index["entry_count"] == 3
    for entry_id in ("ok_entry", "missing_entry", "error_entry"):
        assert (out_dir / f"{entry_id}.json").exists()
    assert (out_dir / "index.json").exists()


def test_observe_corpus_reports_status_per_entry_and_continues_past_failures(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    index = observe_corpus(mixed_manifest, tmp_path / "out")

    statuses = {e["id"]: e["status"] for e in index["entries"]}
    assert statuses == {
        "ok_entry": "ok",
        "missing_entry": "missing_audio",
        "error_entry": "error",
    }
    assert index["summary"] == {"ok": 1, "no_pitch": 0, "missing_audio": 1, "error": 1}


def test_observe_corpus_all_outputs_are_valid_against_schema(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    index = observe_corpus(mixed_manifest, out_dir)
    schema = _load_schema()

    for entry in index["entries"]:
        record = json.loads((out_dir / entry["output_file"]).read_text(encoding="utf-8"))
        jsonschema.validate(instance=record, schema=schema)


def test_observe_corpus_per_entry_output_is_bit_exact_across_runs(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"

    index_a = observe_corpus(mixed_manifest, out_a)
    observe_corpus(mixed_manifest, out_b)

    for entry in index_a["entries"]:
        assert (out_a / entry["output_file"]).read_bytes() == (
            out_b / entry["output_file"]
        ).read_bytes()


def test_observe_corpus_raises_manifest_error_for_missing_manifest_file(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        observe_corpus(tmp_path / "does_not_exist.json", tmp_path / "out")


def test_cli_observe_harmonics_exit_code_is_nonzero_when_an_entry_errors(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    exit_code = cli_main(
        ["observe-harmonics", "--manifest", str(mixed_manifest), "--out", str(tmp_path / "out")]
    )

    assert exit_code == 1


def test_cli_observe_harmonics_exit_code_is_two_for_unreadable_manifest(tmp_path: Path) -> None:
    exit_code = cli_main(
        [
            "observe-harmonics",
            "--manifest",
            str(tmp_path / "does_not_exist.json"),
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 2


def test_real_corpus_manifest_runs_to_completion(tmp_path: Path) -> None:
    """実際の `corpus/manifest.json` を通しで実行し、完了条件（全音源に対する観測、
    音高を持たない音源が除外されず記録される、失敗が全体を止めない）を確認する。

    同梱不可の音源（8件）が手元にない実行環境では missing_audio になる。これは
    `harness.corpus_runner` と同じ「欠測は正常系」の扱いであり、失敗ではない。
    """
    index = observe_corpus(REAL_MANIFEST_PATH, tmp_path / "out")

    assert index["entry_count"] == 11
    assert index["summary"]["error"] == 0
    summary = index["summary"]
    assert summary["ok"] + summary["no_pitch"] + summary["missing_audio"] == 11
    # white_noise は音高を持たない音源として明示的に記録される（同梱済みのため実測できる）。
    statuses = {e["id"]: e["status"] for e in index["entries"]}
    assert statuses["white_noise"] == "no_pitch"
