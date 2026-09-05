"""harness.corpus_runner のテスト（P0-11 #11）。

Issue #11 の完了条件を検証する：
- マニフェストと出力先を引数に取るCLIがあり、全エントリに対して指標算出を実行する
- 音源1件につき1つのJSONと、全件を束ねるインデックスJSONが出力される
- 比較対象（candidate）のパスがマニフェストのフィールドとして与えられ、算出処理は
  それがどう作られたかを知らない
- 1件の失敗が全体を止めるか継続するかの規約が決まっており、終了コードの意味
  （正常 / 一部失敗 / 実行不能）がドキュメントに書かれている
- 同梱不可の音源が手元に存在しない場合、そのエントリを欠測として記録し、残りの音源の
  処理を完走する
- 同一入力に対して2回実行した出力ファイル群がビット単位で一致する
- 各音源の実行所要時間がインデックスJSONに記録される
- 出力されたすべてのJSONが #3 のスキーマに valid であることを検証するテストがある
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import numpy as np
import pytest
import soundfile as sf

from harness.cli import main as cli_main
from harness.corpus_runner import ManifestError, run_corpus

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "04-metrics.schema.json"
REAL_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "corpus" / "manifest.json"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _write_wav(path: Path, sample_rate: int = 44100, num_samples: int = 4410) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = 0.1 * np.sin(2 * np.pi * 440 * np.arange(num_samples) / sample_rate)
    sf.write(str(path), data.reshape(-1, 1), sample_rate, subtype="PCM_16")


@pytest.fixture
def mixed_manifest(tmp_path: Path) -> Path:
    """ok / missing / error の3種のステータスを再現する最小マニフェストを組み立てる。"""
    repo_root = tmp_path
    ok_path = repo_root / "corpus" / "audio" / "ok_source.wav"
    _write_wav(ok_path, sample_rate=44100)

    error_target_path = repo_root / "corpus" / "audio" / "error_target.wav"
    error_candidate_path = repo_root / "corpus" / "audio" / "error_candidate.wav"
    _write_wav(error_target_path, sample_rate=44100)
    _write_wav(error_candidate_path, sample_rate=22050)  # サンプルレート不一致 -> 例外

    manifest = {
        "schema_version": "1.1.0",
        "entries": [
            {
                "id": "ok_entry",
                "format": "wav",
                "bundled": True,
                "bundled_path": "corpus/audio/ok_source.wav",
                "retrieval": None,
                "candidate_path": "corpus/audio/ok_source.wav",
            },
            {
                "id": "missing_entry",
                "format": "ogg",
                "bundled": False,
                "bundled_path": None,
                "retrieval": "テスト用: 意図的に取得していない音源",
                "candidate_path": "corpus/.cache/missing_entry.ogg",
            },
            {
                "id": "error_entry",
                "format": "wav",
                "bundled": True,
                "bundled_path": "corpus/audio/error_target.wav",
                "retrieval": None,
                "candidate_path": "corpus/audio/error_candidate.wav",
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

    index = run_corpus(mixed_manifest, out_dir)

    assert index["entry_count"] == 3
    for entry_id in ("ok_entry", "missing_entry", "error_entry"):
        assert (out_dir / f"{entry_id}.json").exists()
    assert (out_dir / "index.json").exists()


def test_run_corpus_reports_status_per_entry_and_continues_past_failures(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    index = run_corpus(mixed_manifest, tmp_path / "out")

    statuses = {e["id"]: e["status"] for e in index["entries"]}
    assert statuses == {
        "ok_entry": "ok",
        "missing_entry": "missing",
        "error_entry": "error",
    }
    assert index["summary"] == {"ok": 1, "missing": 1, "error": 1}


def test_ok_entry_uses_candidate_path_field_and_computes_real_metrics(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    run_corpus(mixed_manifest, out_dir)

    vector = json.loads((out_dir / "ok_entry.json").read_text(encoding="utf-8"))
    # target と candidate_path が同一音源（自己比較の seam）を指しているため、全体指標は0。
    assert vector["overall"]["msstft"]["value"] == pytest.approx(0.0, abs=1e-9)
    assert vector["overall"]["mfcc"]["value"] == pytest.approx(0.0, abs=1e-9)


def test_missing_entry_is_recorded_as_fully_missing_metrics_vector(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    run_corpus(mixed_manifest, out_dir)

    vector = json.loads((out_dir / "missing_entry.json").read_text(encoding="utf-8"))
    assert vector["overall"]["msstft"]["value"] is None
    assert "手元に存在しない" in vector["overall"]["msstft"]["missing_reason"]


def test_error_entry_is_recorded_with_exception_in_missing_reason(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    run_corpus(mixed_manifest, out_dir)

    vector = json.loads((out_dir / "error_entry.json").read_text(encoding="utf-8"))
    assert vector["overall"]["msstft"]["value"] is None
    assert "SampleRateMismatchError" in vector["overall"]["msstft"]["missing_reason"]


def test_all_output_jsons_are_valid_against_metrics_schema(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    index = run_corpus(mixed_manifest, out_dir)
    schema = _load_schema()

    for entry in index["entries"]:
        vector = json.loads((out_dir / entry["output_file"]).read_text(encoding="utf-8"))
        jsonschema.validate(vector, schema)


def test_index_records_elapsed_time_per_entry(mixed_manifest: Path, tmp_path: Path) -> None:
    index = run_corpus(mixed_manifest, tmp_path / "out")

    for entry in index["entries"]:
        assert isinstance(entry["elapsed_s"], float)
        assert entry["elapsed_s"] >= 0.0


def test_per_entry_output_files_are_bit_exact_across_repeated_runs(
    mixed_manifest: Path, tmp_path: Path
) -> None:
    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"

    index_a = run_corpus(mixed_manifest, out_a)
    index_b = run_corpus(mixed_manifest, out_b)

    # 音源ごとのJSON（指標ベクトルそのもの）はビット単位で一致する（決定論、完了条件）。
    for entry in index_a["entries"]:
        bytes_a = (out_a / entry["output_file"]).read_bytes()
        bytes_b = (out_b / entry["output_file"]).read_bytes()
        assert bytes_a == bytes_b

    # index.json は elapsed_s（実測の所要時間）を含むため、この項目だけは実行ごとに
    # 変わってよい。それ以外（件数・ステータス・出力ファイル名）は一致する。
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
    exit_code = cli_main(
        ["run-corpus", "--manifest", str(mixed_manifest), "--out", str(tmp_path / "out")]
    )

    assert exit_code == 1


def test_cli_run_corpus_exit_code_is_zero_when_only_ok_and_missing(tmp_path: Path) -> None:
    ok_path = tmp_path / "corpus" / "audio" / "ok_source.wav"
    _write_wav(ok_path)
    manifest = {
        "entries": [
            {
                "id": "ok_entry",
                "format": "wav",
                "bundled": True,
                "bundled_path": "corpus/audio/ok_source.wav",
                "retrieval": None,
                "candidate_path": "corpus/audio/ok_source.wav",
            },
            {
                "id": "missing_entry",
                "format": "ogg",
                "bundled": False,
                "bundled_path": None,
                "retrieval": "テスト用: 意図的に取得していない音源",
                "candidate_path": "corpus/.cache/missing_entry.ogg",
            },
        ]
    }
    manifest_path = tmp_path / "corpus" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

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
    tmp_path: Path,
) -> None:
    """実際の `corpus/manifest.json`（Issue #10）を通しで実行し、フェーズ0の完了条件
    （同梱不可の音源が手元に無い環境でも欠測として記録し完走する）を確認する。
    同梱済みエントリ（sine_440hz / white_noise / spoken_hello）は自己比較でokになる。
    """
    index = run_corpus(REAL_MANIFEST_PATH, tmp_path / "out")

    assert index["entry_count"] == 11
    assert index["summary"]["error"] == 0
    assert index["summary"]["ok"] >= 3
    assert index["summary"]["ok"] + index["summary"]["missing"] == 11
