"""harness.metrics（全体指標・区間別指標: msstft / mfcc / loudness_diff_db）のテスト。

Issue #5 の完了条件を検証する：
- 2つのWAVパスから #3 のスキーマに valid な指標ベクトルJSONを組み立てられる
- 完全に同一のペアに対し、3指標すべてが0になる（許容誤差 1e-9）
- 既知のゲイン差ペアに対し、ラウドネス差がその値と一致する（許容誤差 1e-6）。
  符号の向き（candidate 基準）は harness/metrics.py のdocstringに明記されている
- 既知のローパス済みペアに対し、マルチスケールスペクトル距離が同一ペアより大きい
- 使用したFFTサイズ集合が calc_conditions.fft_sizes に記録される
- 同一入力に対して2回実行した出力JSONがビット単位で一致する

Issue #6 の完了条件を検証する：
- 区間境界を外部入力として受け取り、指定された各区間について全体指標と同じ3指標を算出する
- アタック区間の既定値（0〜20ms）で、注釈なしでもアタックは算出される
- 遷移部/定常部/リリースの境界が注釈として与えられていない場合、該当区間は欠測（理由付き）
  として出力され、エラーにならない
- 使用した区間境界の実値が calc_conditions.segment_boundaries_s に記録される
- アタック位置が既知の時間だけずれたフィクスチャに対し、アタック区間の誤差が定常部の誤差より
  大きい
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from harness.fixture_gen import GAIN_DB, LOWPASS_CUTOFF_HZ, ONSET_SHIFT_S, generate_all
from harness.metrics import (
    DEFAULT_ATTACK_END_S,
    DEFAULT_FFT_SIZES,
    compute_metrics_vector,
    loudness_diff_db,
    mfcc_distance,
    multiscale_spectral_distance,
)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "04-metrics.schema.json"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _pair_meta(meta: dict, name: str) -> dict:
    for p in meta["pairs"]:
        if p["name"] == name:
            return p
    raise AssertionError(f"pair {name} not found")


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("metrics_fixtures")
    meta = generate_all(out_dir, seed=0)
    return out_dir, meta


def test_compute_metrics_vector_is_valid_against_schema(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    vector = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])

    jsonschema.validate(instance=vector, schema=_load_schema())


def test_identical_pair_has_all_zero_overall_metrics(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    vector = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])

    overall = vector["overall"]
    assert overall["msstft"]["value"] == pytest.approx(0.0, abs=1e-9)
    assert overall["mfcc"]["value"] == pytest.approx(0.0, abs=1e-9)
    assert overall["loudness_diff_db"]["value"] == pytest.approx(0.0, abs=1e-9)
    for metric in overall.values():
        assert metric["missing_reason"] is None


def test_gain_pair_loudness_diff_matches_known_gain_db(fixtures) -> None:
    """符号の向き：candidate が target より大きい場合、正の値になる。"""
    out_dir, meta = fixtures
    p = _pair_meta(meta, "b_gain")

    vector = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])

    assert p["known"]["gain_db"] == GAIN_DB
    assert vector["overall"]["loudness_diff_db"]["value"] == pytest.approx(
        GAIN_DB, abs=1e-6
    )


def test_lowpass_pair_msstft_is_larger_than_identical_pair(fixtures) -> None:
    out_dir, meta = fixtures
    identical = _pair_meta(meta, "a_identical")
    lowpass = _pair_meta(meta, "c_lowpass")

    identical_vector = compute_metrics_vector(
        out_dir / identical["target"], out_dir / identical["candidate"]
    )
    lowpass_vector = compute_metrics_vector(
        out_dir / lowpass["target"], out_dir / lowpass["candidate"]
    )

    assert (
        lowpass_vector["overall"]["msstft"]["value"]
        > identical_vector["overall"]["msstft"]["value"]
    )


def test_calc_conditions_records_fft_sizes_used(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    vector = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])

    assert vector["calc_conditions"]["fft_sizes"] == list(DEFAULT_FFT_SIZES)


def test_attack_segment_is_computed_with_default_boundary_and_no_annotation(
    fixtures,
) -> None:
    """アタックは既定境界（0〜20ms）を持つため、注釈なしでも算出される（Issue #6）。"""
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    vector = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])

    attack = vector["segments"]["attack"]
    for metric in attack.values():
        assert metric["value"] == pytest.approx(0.0, abs=1e-9)
        assert metric["missing_reason"] is None


def test_transition_sustain_release_are_missing_without_boundary_annotations(
    fixtures,
) -> None:
    """遷移部/定常部/リリースは境界の注釈がなければ欠測（理由付き）になり、落ちない（Issue #6）。"""
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    vector = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])

    for segment_name in ("transition", "sustain", "release"):
        segment = vector["segments"][segment_name]
        for metric in segment.values():
            assert metric["value"] is None
            assert metric["missing_reason"]


def test_calc_conditions_records_actual_segment_boundaries_used(fixtures) -> None:
    """使用した区間境界の実値が calc_conditions に記録される（Issue #6 完了条件）。"""
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    default_vector = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])
    boundaries = default_vector["calc_conditions"]["segment_boundaries_s"]
    assert boundaries["attack_end_s"] == pytest.approx(DEFAULT_ATTACK_END_S)
    assert boundaries["transition_end_s"] is None
    assert boundaries["sustain_end_s"] is None

    given_vector = compute_metrics_vector(
        out_dir / p["target"],
        out_dir / p["candidate"],
        attack_end_s=0.03,
        transition_end_s=0.1,
        sustain_end_s=0.4,
    )
    given_boundaries = given_vector["calc_conditions"]["segment_boundaries_s"]
    assert given_boundaries == {
        "attack_end_s": pytest.approx(0.03),
        "transition_end_s": pytest.approx(0.1),
        "sustain_end_s": pytest.approx(0.4),
    }
    for segment_name in ("attack", "transition", "sustain", "release"):
        for metric in given_vector["segments"][segment_name].values():
            assert metric["missing_reason"] is None


def test_metrics_vector_with_all_boundaries_given_is_valid_against_schema(
    fixtures,
) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    vector = compute_metrics_vector(
        out_dir / p["target"],
        out_dir / p["candidate"],
        transition_end_s=0.1,
        sustain_end_s=0.4,
    )

    jsonschema.validate(instance=vector, schema=_load_schema())


def test_attack_segment_error_is_larger_than_sustain_segment_error_for_shifted_onset(
    fixtures,
) -> None:
    """アタック位置が既知の時間だけずれたフィクスチャで、アタック区間の誤差が定常部より大きい

    （Issue #6 完了条件）。onset_shift_s の間 candidate は無音であり、既定のアタック境界
    （20ms）は無音区間の内側に収まる（20ms < ONSET_SHIFT_S）ため、アタック区間には
    ずれの影響がそのまま現れる。
    """
    out_dir, meta = fixtures
    p = _pair_meta(meta, "e_attack_shift")
    assert DEFAULT_ATTACK_END_S < ONSET_SHIFT_S

    vector = compute_metrics_vector(
        out_dir / p["target"],
        out_dir / p["candidate"],
        transition_end_s=0.1,
        sustain_end_s=0.45,
    )

    attack_msstft = vector["segments"]["attack"]["msstft"]["value"]
    sustain_msstft = vector["segments"]["sustain"]["msstft"]["value"]
    attack_mfcc = vector["segments"]["attack"]["mfcc"]["value"]
    sustain_mfcc = vector["segments"]["sustain"]["mfcc"]["value"]

    assert attack_msstft > sustain_msstft
    assert attack_mfcc > sustain_mfcc


def test_segment_boundary_beyond_source_length_is_missing_not_an_error(fixtures) -> None:
    """波形長を超える境界（区間長0）でも例外にならず、欠測として出力される（Issue #6）。"""
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    vector = compute_metrics_vector(
        out_dir / p["target"],
        out_dir / p["candidate"],
        transition_end_s=10.0,
        sustain_end_s=10.0,
    )

    for metric in vector["segments"]["sustain"].values():
        assert metric["value"] is None
        assert metric["missing_reason"]


def test_compute_metrics_vector_with_band_edges_produces_actual_errors(fixtures) -> None:
    """帯域端リスト（設定データ）を渡すと、各帯域の誤差が実際の値として出力される。

    各要素は実際に使用した lo_hz / hi_hz を実値で含む（docs/04-metrics.md の例示形式）。
    """
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")
    band_edges_hz = [0.0, 1000.0, 4000.0, 8000.0]

    vector = compute_metrics_vector(
        out_dir / p["target"], out_dir / p["candidate"], band_edges_hz=band_edges_hz
    )

    assert len(vector["bands"]) == len(band_edges_hz) - 1
    for band, lo, hi in zip(vector["bands"], band_edges_hz[:-1], band_edges_hz[1:]):
        assert band["lo_hz"] == pytest.approx(lo)
        assert band["hi_hz"] == pytest.approx(hi)
        assert band["error"]["value"] == pytest.approx(0.0, abs=1e-9)
        assert band["error"]["missing_reason"] is None
    assert vector["calc_conditions"]["band_edges_hz"] == band_edges_hz


def test_single_full_band_error_matches_overall_msstft(fixtures) -> None:
    """帯域を1つ（0〜ナイキスト）に設定したときの値は、対応する全体指標(msstft)の値と一致する。

    許容誤差は 1e-9（同一の算出式をビン制限なしで通した場合と数値的に一致するはずのため）。
    実際のサンプルレートを知らなくても、ナイキストを確実に超える上限を与えれば全ビンを含む
    （帯域端のHz値は実サンプルレートの物理的なナイキストで自然にクリップされる）。
    """
    out_dir, meta = fixtures
    p = _pair_meta(meta, "d_noise")
    band_edges_hz = [0.0, 1.0e9]

    vector = compute_metrics_vector(
        out_dir / p["target"], out_dir / p["candidate"], band_edges_hz=band_edges_hz
    )

    assert len(vector["bands"]) == 1
    assert vector["bands"][0]["error"]["value"] == pytest.approx(
        vector["overall"]["msstft"]["value"], abs=1e-9
    )


def test_lowpass_pair_stopband_error_is_larger_than_passband_error(fixtures) -> None:
    """既知のカットオフでローパス済みのフィクスチャに対し、遮断帯域の誤差が通過帯域の誤差より大きい。"""
    out_dir, meta = fixtures
    p = _pair_meta(meta, "c_lowpass")
    assert p["known"]["cutoff_hz"] == LOWPASS_CUTOFF_HZ  # 2000Hz。以下の帯域端はこれを跨ぐ

    vector = compute_metrics_vector(
        out_dir / p["target"],
        out_dir / p["candidate"],
        band_edges_hz=[0.0, 800.0, 2000.0, 5000.0, 8000.0],
    )

    passband_error = vector["bands"][0]["error"]["value"]  # [0, 800) Hz: 通過帯域
    stopband_error = vector["bands"][-1]["error"]["value"]  # [5000, 8000] Hz: 遮断帯域

    assert stopband_error > passband_error


def test_band_edges_configurable_as_equal_mel_or_bark_without_code_change(fixtures) -> None:
    """等間隔・メル・バークいずれの帯域端リストも、実装を変更せず設定（引数）だけで指定できる。

    - equal: 0〜ナイキストの線形等間隔
    - mel: librosa.mel_frequencies によるメル尺度上の等間隔
    - bark: Zwicker & Terhardt の24臨界帯域表（Hz、上限値）から実サンプルレートの
      ナイキスト(8000Hz)以下の部分を切り出した既知の値（公表値であり推測ではない）
    """
    import librosa
    import numpy as np

    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")
    nyquist = 8000.0
    n_bands = 4

    equal_edges = [float(e) for e in np.linspace(0.0, nyquist, n_bands + 1)]
    mel_edges = [
        float(e) for e in librosa.mel_frequencies(n_mels=n_bands + 1, fmin=0.0, fmax=nyquist)
    ]
    bark_table_hz = [
        100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270,
        1480, 1720, 2000, 2320, 2700, 3150, 3700, 4400, 5300, 6400, 7700,
    ]
    bark_edges = [0.0] + [float(e) for e in bark_table_hz if e <= nyquist]

    for edges in (equal_edges, mel_edges, bark_edges):
        vector = compute_metrics_vector(
            out_dir / p["target"], out_dir / p["candidate"], band_edges_hz=edges
        )
        assert len(vector["bands"]) == len(edges) - 1
        for metric in vector["bands"]:
            assert metric["error"]["missing_reason"] is None


def test_default_band_edges_hz_is_loaded_from_a_bundled_config_file() -> None:
    """既定の帯域端リストは設定ファイルとして1つ同梱され、暫定でありQ-004の解決対象だと明記されている。"""
    import json

    from harness.metrics import DEFAULT_BAND_EDGES_HZ

    config_path = (
        Path(__file__).resolve().parent.parent / "harness" / "band_edges_default.json"
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert tuple(float(e) for e in config["band_edges_hz"]) == DEFAULT_BAND_EDGES_HZ
    assert "Q-004" in config["$comment"]
    assert "暫定" in config["$comment"]


def test_compute_metrics_vector_is_deterministic_in_process(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "d_noise")

    first = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])
    second = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_multiscale_spectral_distance_zero_for_identical_arrays() -> None:
    import numpy as np

    rng = np.random.default_rng(0)
    signal = rng.normal(size=8000)

    assert multiscale_spectral_distance(signal, signal.copy()) == pytest.approx(
        0.0, abs=1e-9
    )


def test_mfcc_distance_zero_for_identical_arrays() -> None:
    import numpy as np

    rng = np.random.default_rng(0)
    signal = rng.normal(size=8000)

    assert mfcc_distance(signal, signal.copy(), sample_rate=16000) == pytest.approx(
        0.0, abs=1e-9
    )


def test_loudness_diff_db_matches_known_gain() -> None:
    import numpy as np

    rng = np.random.default_rng(0)
    target = rng.normal(size=4000)
    scale = 10.0 ** (GAIN_DB / 20.0)
    candidate = target * scale

    assert loudness_diff_db(target, candidate) == pytest.approx(GAIN_DB, abs=1e-9)


def test_cli_metrics_subcommand_outputs_valid_json(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness",
            "metrics",
            str(out_dir / p["target"]),
            str(out_dir / p["candidate"]),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    vector = json.loads(result.stdout)
    jsonschema.validate(instance=vector, schema=_load_schema())


def test_cli_metrics_subcommand_accepts_segment_boundary_flags(fixtures) -> None:
    """--attack-end-s/--transition-end-s/--sustain-end-s がそのまま算出条件に反映される。"""
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness",
            "metrics",
            str(out_dir / p["target"]),
            str(out_dir / p["candidate"]),
            "--attack-end-s",
            "0.03",
            "--transition-end-s",
            "0.1",
            "--sustain-end-s",
            "0.4",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    vector = json.loads(result.stdout)
    jsonschema.validate(instance=vector, schema=_load_schema())
    boundaries = vector["calc_conditions"]["segment_boundaries_s"]
    assert boundaries == {
        "attack_end_s": pytest.approx(0.03),
        "transition_end_s": pytest.approx(0.1),
        "sustain_end_s": pytest.approx(0.4),
    }
    for segment in vector["segments"].values():
        for metric in segment.values():
            assert metric["missing_reason"] is None


def test_cli_metrics_subcommand_is_bit_exact_across_processes(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "d_noise")

    def _run() -> str:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "harness",
                "metrics",
                str(out_dir / p["target"]),
                str(out_dir / p["candidate"]),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        return result.stdout

    first = _run()
    second = _run()

    assert first == second
