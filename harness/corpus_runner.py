"""コーパス実行ランナ（P0-11 #11）。

マニフェスト（`corpus/manifest.json`）の全エントリに対して指標算出
（`harness.metrics.compute_metrics_vector`）を実行し、音源1件につき1つのJSONと、
全件を束ねるインデックスJSONを出力する。`docs/01-architecture.md` の
「ハーネス（スクリプト）」が担う「コーパス実行」の実体であり、CIとフィッティング
ループの両方から呼ばれる唯一の入口になる。

## 比較対象（candidate）の扱い

candidate のパスはマニフェストの `candidate_path` フィールドとして与えられる。
本ランナはそのパスがどう作られたかを一切知らない。

**フェーズ0にはレンダラが存在しない**（`docs/00-vision.md` フェーズ1）。そのため
現在の `corpus/manifest.json` の `candidate_path` は、各エントリ自身の音声パスと
同じ値（自己比較）に設定された **seam** である。フェーズ1でレンダラが実装され次第、
この値をレンダラ出力のパスに差し替える（Issue #15 の既知の判断）。本モジュール自体は
この前提に依存しない。

## 失敗・欠測の扱い（Issue #11 完了条件）

- target または candidate の音声ファイルが手元に存在しない場合、そのエントリを
  **欠測**として記録し、残りのエントリの処理を継続する（同梱不可の音源が取得できない
  環境でも完走する）。欠測は `docs/04-metrics.schema.json` の欠測表現を使い、全指標が
  欠測の指標ベクトルとして出力する（`harness.metrics.build_missing_metrics_vector`）。
- 指標算出そのもの（`compute_metrics_vector`）が例外を送出した場合は**失敗**として
  記録し、同様に残りのエントリの処理を継続する。1件の失敗が全体を止めることはない。
- 終了コードの意味（`harness/cli.py` の `run-corpus` サブコマンド）：
  - `0`：全エントリが欠測を含めて完走した（欠測は正常系として扱う。音源が同梱・取得
    できるかどうかの判定は本ランナのスコープ外）
  - `1`：1件以上のエントリで指標算出が例外により失敗した（一部失敗）
  - `2`：マニフェスト自体が読めない・エントリが1件もない等、実行そのものが成立しな
    かった（実行不能。`ManifestError`）
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path

from harness.metrics import (
    DEFAULT_ATTACK_END_S,
    DEFAULT_BAND_EDGES_HZ,
    DEFAULT_FFT_SIZES,
    build_missing_metrics_vector,
    compute_metrics_vector,
)

#: インデックスJSON自体の構造のバージョン（`docs/04-metrics.schema.json` の
#: 指標ベクトルとは別物。将来インデックスの構造を変える際に上げる）。
INDEX_SCHEMA_VERSION = "1.0.0"


class ManifestError(RuntimeError):
    """マニフェストが読めない、または実行できる形式でないときに送出する（実行不能）。"""


def _resolve_local_path(entry: dict, repo_root: Path) -> Path:
    """マニフェストの1エントリから、target音声のローカルパスを解決する。

    `corpus/fetch_and_verify.py` の `local_path_for` と同じ規則（bundled ならその
    `bundled_path`、そうでなければ `corpus/.cache/<id>.<format>`）を使う。規則の
    実体は取得スクリプト側にあり、ここでは同じ規則を踏襲するだけに留める。
    """
    if entry.get("bundled"):
        return repo_root / entry["bundled_path"]
    return repo_root / "corpus" / ".cache" / f"{entry['id']}.{entry['format']}"


def _resolve_candidate_path(entry: dict, repo_root: Path) -> Path:
    """マニフェストの `candidate_path` フィールド（seam）をリポジトリルート基準で解決する。"""
    return repo_root / entry["candidate_path"]


def _run_one(entry: dict, repo_root: Path, metric_kwargs: dict) -> tuple[dict, str]:
    """1エントリを処理し、`(指標ベクトル, status)` を返す。

    `status` は `"ok"`（実際に算出できた）/ `"missing"`（音声ファイルが手元にない）/
    `"error"`（算出処理が例外で失敗した）のいずれか。
    """
    entry_id = entry["id"]
    target_path = _resolve_local_path(entry, repo_root)
    candidate_path = _resolve_candidate_path(entry, repo_root)

    if not target_path.exists():
        retrieval = entry.get("retrieval") or "取得手順は corpus/manifest.json を参照"
        reason = f"target音源が手元に存在しない: {target_path}（{retrieval}）"
        return build_missing_metrics_vector(entry_id, reason, **metric_kwargs), "missing"
    if not candidate_path.exists():
        reason = f"candidate音源が手元に存在しない: {candidate_path}"
        return build_missing_metrics_vector(entry_id, reason, **metric_kwargs), "missing"

    try:
        vector = compute_metrics_vector(target_path, candidate_path, **metric_kwargs)
    except Exception as exc:  # noqa: BLE001 - 1件の失敗で全体を止めないため捕捉して記録する
        reason = f"指標算出が例外で失敗: {type(exc).__name__}: {exc}"
        return build_missing_metrics_vector(entry_id, reason, **metric_kwargs), "error"

    # target フィールドはファイル名ではなくマニフェストのidに揃える。複数エントリを
    # 束ねるインデックスと突き合わせるときの一意なキーにするため。
    vector["target"] = entry_id
    return vector, "ok"


def run_corpus(
    manifest_path: str | Path,
    out_dir: str | Path,
    attack_end_s: float = DEFAULT_ATTACK_END_S,
    fft_sizes: Sequence[int] = DEFAULT_FFT_SIZES,
    band_edges_hz: Sequence[float] = DEFAULT_BAND_EDGES_HZ,
) -> dict:
    """マニフェストの全エントリに対して指標算出を実行し、結果一式を `out_dir` に書き出す。

    `<out_dir>/<id>.json`（音源1件につき1つ）と `<out_dir>/index.json`（全件を束ねる）を
    出力する。戻り値は `index.json` と同じ内容の辞書。

    マニフェストパスの親の親をリポジトリルートとみなし、`bundled_path` /
    `candidate_path` はそこからの相対パスとして解決する（`corpus/manifest.json` の
    既存の配置規則に合わせる）。
    """
    manifest_path = Path(manifest_path)
    out_dir = Path(out_dir)

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"マニフェストを読み込めない: {manifest_path}: {exc}") from exc

    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ManifestError(f"マニフェストにエントリが1件もない: {manifest_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    repo_root = manifest_path.resolve().parent.parent
    metric_kwargs = {
        "attack_end_s": attack_end_s,
        "fft_sizes": fft_sizes,
        "band_edges_hz": band_edges_hz,
    }

    index_entries = []
    for entry in entries:
        entry_id = entry["id"]
        start = time.perf_counter()
        vector, status = _run_one(entry, repo_root, metric_kwargs)
        elapsed_s = time.perf_counter() - start

        output_filename = f"{entry_id}.json"
        (out_dir / output_filename).write_text(
            json.dumps(vector, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        index_entries.append(
            {
                "id": entry_id,
                "status": status,
                "output_file": output_filename,
                "elapsed_s": elapsed_s,
            }
        )

    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "manifest_path": str(manifest_path),
        "entry_count": len(index_entries),
        "entries": index_entries,
        "summary": {
            "ok": sum(1 for e in index_entries if e["status"] == "ok"),
            "missing": sum(1 for e in index_entries if e["status"] == "missing"),
            "error": sum(1 for e in index_entries if e["status"] == "error"),
        },
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return index


__all__ = ["INDEX_SCHEMA_VERSION", "ManifestError", "run_corpus"]
