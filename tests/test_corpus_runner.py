"""harness.corpus_runner のテスト（P0-11 #11 / P1-07 #52）。

Issue #11 + #52 の完了条件を検証する：
- マニフェストと出力先を引数に取るCLIがあり、全エントリに対して「プリセット→レンダ→
  指標算出」を実行する
- 音源1件につき1つのJSONと、全件を束ねるインデックスJSONが出力される
- candidate はマニフェストの `preset_path`（プリセットJSON）をレンダラが生成したWAVであり、
  算出処理はその由来をレンダラ起動経路に持つ
- #52：レンダのサンプルレートはターゲットに合わせる（`render_at_target_sample_rate`）ことを
  出力JSONの `calc_conditions.render` に記録する
- 1件の失敗が全体を止めるか継続するかの規約が決まっており、終了コードの意味がドキュメントに
  書かれている
- 同梱不可の音源が手元に存在しない場合、およびレンダラが利用できない環境では、そのエントリを
  欠測として記録し、残りの処理を完走する
- レンダが非0で終了したエントリは失敗として記録し、継続する
- 同一入力に対して2回実行した出力ファイル群がビット単位で一致する
- 各音源の実行所要時間がインデックスJSONに記録される
- 出力されたすべてのJSONが #3 のスキーマに valid であることを検証するテストがある
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import numpy as np
import pytest
import soundfile as sf

from harness.cli import main as cli_main
from harness.corpus_runner import (
    RENDER_BIN_ENV,
    SR_MISMATCH_POLICY,
    ManifestError,
    run_corpus,
)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "04-metrics.schema.json"
REAL_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "corpus" / "manifest.json"
FAKE_RENDER = Path(__file__).resolve().parent / "fixtures" / "fake_render.py"

# テスト用の暫定プリセット。fake_render が読む（本物の luthier-render は対象外）。
# `__fail__` を true にすると fake_render が非0で終了する（エンジンがスキーマ違反で
# 停止する挙動の再現、docs/03）。
PROVISIONAL_PRESET = {
    "format_version": "0.1.0",
    "engine_spec_version": "0.1.0",
    "layers": {
        "transient": {
            "enabled": True,
            "gain": 0.0,
            "duration": 2000.0,
            "seed": 0,
            "spectral_envelope": [{"unit": "db", "interp": "linear", "points": [{"t": 0.0, "v": 0.0}]}],
        },
        "harmonic": {
            "enabled": True,
            "f0": {"unit": "hz", "interp": "linear", "points": [{"t": 0.0, "v": 440.0}]},
            "partial_amplitudes": [{"unit": "linear", "interp": "linear", "points": [{"t": 0.0, "v": 1.0}]}],
            "inharmonicity": 0.0,
        },
        "formant": {
            "enabled": True,
            "bands": [
                {
                    "freq": {"unit": "hz", "interp": "linear", "points": [{"t": 0.0, "v": 500.0}]},
                    "q": {"unit": "linear", "interp": "linear", "points": [{"t": 0.0, "v": 5.0}]},
                    "gain": {"unit": "db", "interp": "linear", "points": [{"t": 0.0, "v": 0.0}]},
                }
                for _ in range(4)
            ],
        },
    },
}


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _write_wav(path: Path, sample_rate: int = 44100, num_samples: int = 4410) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = 0.1 * np.sin(2 * np.pi * 440 * np.arange(num_samples) / sample_rate)
    sf.write(str(path), data.reshape(-1, 1), sample_rate, subtype="PCM_16")


def _write_preset(repo_root: Path, preset: dict | None = None, *, make_fail: bool = False) -> Path:
    preset_path = repo_root / "corpus" / "presets" / "preset.json"
    preset_path.parent.mkdir(parents=True, exist_ok=True)
    preset_data = dict(preset) if preset is not None else dict(PROVISIONAL_PRESET)
    if make_fail:
        preset_data["__fail__"] = True
    preset_path.write_text(json.dumps(preset_data, ensure_ascii=False), encoding="utf-8")
    return preset_path


def _render_cmd() -> list[str]:
    """fake_render を subprocess から起動するためのコマンド（先頭はpythonインタプリタ）。"""
    return [sys.executable, str(FAKE_RENDER)]


@pytest.fixture
def mixed_manifest(tmp_path: Path) -> Path:
    """ok / missing / error の3種のステータスを再現する最小マニフェストを組み立てる。"""
    repo_root = tmp_path
    ok_target = repo_root / "corpus" / "audio" / "ok_source.wav"
    _write_wav(ok_target, sample_rate=44100)
    _write_preset(repo_root)

    # error：プリセットに __fail__ を入れてレンダを非0終了させる。
    error_preset = repo_root / "corpus" / "presets" / "error_preset.json"
    error_preset.parent.mkdir(parents=True, exist_ok=True)
    _write_preset(repo_root)  # 既定の正常プリセットを置いておく（パスはerror_presetに差し替え）
    json.dump({**PROVISIONAL_PRESET, "__fail__": True}, error_preset.open("w", encoding="utf-8"))

    manifest = {
        "schema_version": "1.2.0",
        "entries": [
            {
                "id": "ok_entry",
                "format": "wav",
                "bundled": True,
                "bundled_path": "corpus/audio/ok_source.wav",
                "retrieval": None,
                "preset_path": "corpus/presets/preset.json",
            },
            {
                "id": "missing_entry",
                "format": "ogg",
                "bundled": False,
                "bundled_path": None,
                "retrieval": "テスト用: 意図的に取得していない音源",
                "preset_path": "corpus/presets/preset.json",
            },
            {
                "id": "error_entry",
                "format": "wav",
                "bundled": True,
                "bundled_path": "corpus/audio/ok_source.wav",
                "retrieval": None,
                "preset_path": "corpus/presets/error_preset.json",
            },
        ],
    }
    manifest_path = repo_root / "corpus" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def test_run_corpus_writes_one_json_per_entry_and_an_index(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"

    index = run_corpus(mixed_manifest, out_dir, render_cmd=_render_cmd())

    assert index["entry_count"] == 3
    for entry_id in ("ok_entry", "missing_entry", "error_entry"):
        assert (out_dir / f"{entry_id}.json").exists()
    assert (out_dir / "index.json").exists()


def test_run_corpus_reports_status_per_entry_and_continues_past_failures(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    index = run_corpus(mixed_manifest, tmp_path / "out", render_cmd=_render_cmd())

    statuses = {e["id"]: e["status"] for e in index["entries"]}
    assert statuses == {
        "ok_entry": "ok",
        "missing_entry": "missing",
        "error_entry": "error",
    }
    assert index["summary"] == {"ok": 1, "missing": 1, "error": 1}


def test_ok_entry_renders_preset_and_computes_metrics_against_rendered_wav(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    run_corpus(mixed_manifest, out_dir, render_cmd=_render_cmd())

    vector = json.loads((out_dir / "ok_entry.json").read_text(encoding="utf-8"))
    # candidate は無音レンダ（fake_render が書いた無音WAV）。ターゲットは正弦波なので
    # 自己比較（0）ではなく、無音との有意な距離になる（#52 seam 差し替えの検証）。
    assert vector["overall"]["msstft"]["value"] > 0.0
    # レンダの中間WAVが出力ディレクトリに置かれている。
    assert (out_dir / "renders" / "ok_entry.wav").exists()


def test_ok_entry_records_render_info_with_target_sample_rate(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    run_corpus(mixed_manifest, out_dir, render_cmd=_render_cmd())

    vector = json.loads((out_dir / "ok_entry.json").read_text(encoding="utf-8"))
    render = vector["calc_conditions"]["render"]
    # ターゲット（ok_source.wav）は44100Hz。レンダSRがこれに一致し、方針が記録されている。
    assert render["renderer"] == "luthier-render"
    assert render["preset_path"] == "corpus/presets/preset.json"
    assert render["sample_rate_hz"] == 44100
    assert render["sr_mismatch_policy"] == SR_MISMATCH_POLICY


def test_render_is_done_at_target_sample_rate(tmp_path: Path) -> None:
    """ターゲットが48kHzの場合、レンダも48kHzで実行される（#52 SR方針）。"""
    repo_root = tmp_path
    target = repo_root / "corpus" / "audio" / "target48k.wav"
    _write_wav(target, sample_rate=48000)
    _write_preset(repo_root)
    manifest = {
        "schema_version": "1.2.0",
        "entries": [
            {
                "id": "t48k",
                "format": "wav",
                "bundled": True,
                "bundled_path": "corpus/audio/target48k.wav",
                "retrieval": None,
                "preset_path": "corpus/presets/preset.json",
            }
        ],
    }
    manifest_path = repo_root / "corpus" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    out_dir = tmp_path / "out"
    index = run_corpus(manifest_path, out_dir, render_cmd=_render_cmd())

    assert index["summary"] == {"ok": 1, "missing": 0, "error": 0}
    vector = json.loads((out_dir / "t48k.json").read_text(encoding="utf-8"))
    assert vector["calc_conditions"]["render"]["sample_rate_hz"] == 48000
    # レンダされたcandidateが48kHzであることも確認する（SR一致でrequire_same_sample_rateを通過）。
    buf = sf.info(str(out_dir / "renders" / "t48k.wav"))
    assert buf.samplerate == 48000


def test_missing_entry_is_recorded_as_fully_missing_metrics_vector(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    run_corpus(mixed_manifest, out_dir, render_cmd=_render_cmd())

    vector = json.loads((out_dir / "missing_entry.json").read_text(encoding="utf-8"))
    assert vector["overall"]["msstft"]["value"] is None
    assert "手元に存在しない" in vector["overall"]["msstft"]["missing_reason"]


def test_error_entry_is_recorded_with_render_failure_in_missing_reason(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    run_corpus(mixed_manifest, out_dir, render_cmd=_render_cmd())

    vector = json.loads((out_dir / "error_entry.json").read_text(encoding="utf-8"))
    assert vector["overall"]["msstft"]["value"] is None
    assert "レンダ失敗" in vector["overall"]["msstft"]["missing_reason"]


def test_renderer_unavailable_records_all_entries_as_missing(
    mixed_manifest: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """レンダラ未指定（環境変数も無い）→ 全エントリを欠測として記録し、完走する（#52）。"""
    # 環境変数を確実に消してから実行する（monkeypatchで後始末し、他テストへ漏らさない）。
    monkeypatch.delenv(RENDER_BIN_ENV, raising=False)
    out_dir = tmp_path / "out"
    index = run_corpus(mixed_manifest, out_dir)  # render_cmd省略

    statuses = {e["id"]: e["status"] for e in index["entries"]}
    assert statuses == {
        "ok_entry": "missing",
        "missing_entry": "missing",
        "error_entry": "missing",
    }
    assert index["summary"] == {"ok": 0, "missing": 3, "error": 0}
    vector = json.loads((out_dir / "ok_entry.json").read_text(encoding="utf-8"))
    assert "レンダラが利用できない" in vector["overall"]["msstft"]["missing_reason"]
    # レンダ不能だがpreset_pathと方針は記録される。
    assert vector["calc_conditions"]["render"]["preset_path"] == "corpus/presets/preset.json"
    assert vector["calc_conditions"]["render"]["sample_rate_hz"] is None


def test_renderer_unavailable_still_completes_with_returncode_zero(
    mixed_manifest: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(RENDER_BIN_ENV, raising=False)
    exit_code = cli_main(
        ["run-corpus", "--manifest", str(mixed_manifest), "--out", str(tmp_path / "out")]
    )
    assert exit_code == 0


def test_all_output_jsons_are_valid_against_metrics_schema(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    index = run_corpus(mixed_manifest, out_dir, render_cmd=_render_cmd())
    schema = _load_schema()

    for entry in index["entries"]:
        vector = json.loads((out_dir / entry["output_file"]).read_text(encoding="utf-8"))
        jsonschema.validate(vector, schema)


def test_index_records_elapsed_time_per_entry(mixed_manifest: Path, tmp_path: Path) -> None:
    index = run_corpus(mixed_manifest, tmp_path / "out", render_cmd=_render_cmd())

    for entry in index["entries"]:
        assert isinstance(entry["elapsed_s"], float)
        assert entry["elapsed_s"] >= 0.0


def test_per_entry_output_files_are_bit_exact_across_repeated_runs(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"

    index_a = run_corpus(mixed_manifest, out_a, render_cmd=_render_cmd())
    index_b = run_corpus(mixed_manifest, out_b, render_cmd=_render_cmd())

    # 音源ごとのJSON（指標ベクトルそのもの）はビット単位で一致する（決定論、完了条件）。
    for entry in index_a["entries"]:
        bytes_a = (out_a / entry["output_file"]).read_bytes()
        bytes_b = (out_b / entry["output_file"]).read_bytes()
        assert bytes_a == bytes_b

    # index.json は elapsed_s（実測の所要時間）を含むため、この項目だけは実行ごとに
    # 変わってよい。それ以外は一致する。
    assert index_a["summary"] == index_b["summary"]
    assert index_a["entry_count"] == index_b["entry_count"]
    for entry_a, entry_b in zip(index_a["entries"], index_b["entries"], strict=True):
        assert entry_a["id"] == entry_b["id"]
        assert entry_a["status"] == entry_b["status"]
        assert entry_a["output_file"] == entry_b["output_file"]


def test_run_corpus_raises_manifest_error_for_missing_manifest_file(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        run_corpus(tmp_path / "does_not_exist.json", tmp_path / "out")


def test_run_corpus_raises_manifest_error_for_empty_entries(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"entries": []}), encoding="utf-8")

    with pytest.raises(ManifestError):
        run_corpus(manifest_path, tmp_path / "out")


def test_cli_run_corpus_exit_code_is_nonzero_when_an_entry_errors(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    """`--render-bin` が立ち上がらない（存在しないバイナリ等）→ レンダ失敗 → 一部失敗で1。

    `--render-bin` を実際にフォワードできていることも同時に検証する：フォワードせず
    render_cmd=None 扱いになれば全件missingで0になるはずだが、ここでは1になることを
    期待するため、「レンダを試みて失敗」の経路を測っている。
    """
    exit_code = cli_main(
        [
            "run-corpus",
            "--manifest",
            str(mixed_manifest),
            "--out",
            str(tmp_path / "out"),
            "--render-bin",
            str(tmp_path / "no_such_renderer_binary"),
        ]
    )

    assert exit_code == 1


def test_cli_run_corpus_exit_code_is_zero_when_only_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """レンダラ未指定（環境変数も無い）→ 全エントリ欠測で完走 → 0（#52 完了条件）。

    レンダ不能な環境でも run-corpus は正常終了する（欠測を error 扱いにしない）。
    """
    repo_root = tmp_path
    _write_wav(repo_root / "corpus" / "audio" / "ok_source.wav")
    _write_preset(repo_root)
    manifest = {
        "entries": [
            {
                "id": "ok_entry",
                "format": "wav",
                "bundled": True,
                "bundled_path": "corpus/audio/ok_source.wav",
                "retrieval": None,
                "preset_path": "corpus/presets/preset.json",
            },
            {
                "id": "missing_entry",
                "format": "ogg",
                "bundled": False,
                "bundled_path": None,
                "retrieval": "テスト用: 意図的に取得していない音源",
                "preset_path": "corpus/presets/preset.json",
            },
        ]
    }
    manifest_path = repo_root / "corpus" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    monkeypatch.delenv(RENDER_BIN_ENV, raising=False)
    exit_code = cli_main(
        ["run-corpus", "--manifest", str(manifest_path), "--out", str(tmp_path / "out")]
    )

    assert exit_code == 0


def test_cli_run_corpus_exit_code_is_two_for_unreadable_manifest(tmp_path: Path) -> None:
    exit_code = cli_main(
        [
            "run-corpus",
            "--manifest",
            str(tmp_path / "does_not_exist.json"),
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 2


def test_real_corpus_manifest_runs_to_completion_with_bundled_entries_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """実際の `corpus/manifest.json`（Issue #10 / #52）を通しで実行し、欠測・失敗を
    記録しつつ完走することを確認する。同梱済みエントリ（sine_440hz / white_noise /
    spoken_hello）はレンダ可能でok、非同梱（取得していない）は欠測になる。"""
    monkeypatch.delenv(RENDER_BIN_ENV, raising=False)
    index = run_corpus(REAL_MANIFEST_PATH, tmp_path / "out", render_cmd=_render_cmd())

    assert index["entry_count"] == 11
    assert index["summary"]["error"] == 0
    assert index["summary"]["ok"] >= 3
    assert index["summary"]["ok"] + index["summary"]["missing"] == 11