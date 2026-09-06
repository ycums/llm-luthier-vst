"""`corpus/baseline/` の鮮度検証（Issue #88、Issue #86決定の実装）。

`docs/04-metrics.md`「更新手順（Issue #86 で決定）」が定める不変条件——
`ubuntu-latest`（`docs/adr/0008` の基準環境）でのCI実行に限り、`corpus/baseline/<id>.json` は、
そのPRの現在のエンジンで実行した `run-corpus` の対応する出力 `<id>.json` と一致する——を検証する。
`.github/workflows/metrics.yml` の `ubuntu-latest` ジョブからのみ呼ばれる。Windows（MSVC）
ジョブでは呼ばない（`docs/06-open-questions.md` Q-016「コンパイラ間のビット一致は保証しない」により、
恒常的な誤検出になるため）。

## 一致の判定方法：ビット完全一致（許容誤差なし）

Issue #88 実装時、この判定方法を実測してから決める必要があった
（`AGENTS.md` 第6節「未確定事項を推測で埋めない」、`docs/04-metrics.md`）。
ubuntu-latestに近いLinux/gcc環境でエンジンをビルドし `run-corpus` を実行して
`corpus/baseline/` と比較したところ、鮮度が保たれていた10音源（`tibetan_singing_bowl` を除く
全音源）は、全指標値が浮動小数点の等価比較（`==`）でビット単位一致した。唯一の不一致だった
`tibetan_singing_bowl` は許容誤差の問題ではなく、実際の陳腐化（PR #85 でのWikimediaレート制限に
よる音源再取得失敗時に、既存baselineをやむを得ずそのまま保持した既知の限界。同PR本文参照）
だった。許容誤差を設けるべき実測上の根拠が確認できなかったため、許容誤差は設けずビット完全一致を
要求する。

## status: "ok" 以外（missing / error）の扱い

**今回の実行（`current_dir`）** でstatus: "missing"になったエントリは比較対象から除外する
（非同梱音源の取得失敗を「baselineが古い」と誤検出しないため。取得失敗は
`corpus/fetch_and_verify.py` のレート制限で実際に起きている、PR #85）。既存の `diff-corpus` が
欠測指標を悪化判定の対象外にするのと同じ設計（`harness/metrics_diff.py`）。

status: "error"（レンダ・指標算出が例外で失敗）も同じ理由で除外する。missing・errorのいずれも
出力される指標ベクトルは全欠測（`harness.metrics.build_missing_metrics_vector`、同じ形）であり、
除外しなければ「基準は実値、今回は全欠測」という不一致を必ず作り、陳腐化と誤検出する。errorは
`run-corpus` 自体の終了コード1で別途検出されるべきものであり（`harness/corpus_runner.py`）、
本検証の責務ではない。したがって「今回の実行の status」は "ok" 以外を一律で比較対象外とする。

基準側（`baseline_dir`）のstatusは問わない。基準がmissing（または不在）で今回okの場合は、
むしろ本検証が検出すべき陳腐化そのものとして扱う。

## 比較ロジックの再利用

指標1件ごとの前/後差分の抽出は `harness.metrics_diff.diff_metrics_vector` をそのまま使う
（二重実装を避ける、`docs/01-architecture.md`「二度作らないこと」）。ただし判定基準は異なる：
`metrics_diff` は「悪化したか」（指標ごとに向きがある）を判定するのに対し、本モジュールは
「一致したか」（向きに関係なく値が変わっていないか）を判定する。
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.metrics_diff import diff_metrics_vector

#: 鮮度検証結果JSON自体の構造のバージョン（指標ベクトル・差分JSONのスキーマバージョンとは別物）。
FRESHNESS_SCHEMA_VERSION = "1.0.0"

#: `corpus/baseline/` の更新手順（不一致検出時のメッセージに含める、Issue #88完了条件）。
_FIX_COMMAND = "python -m harness run-corpus --manifest corpus/manifest.json --out corpus/baseline/"


class BaselineFreshnessError(RuntimeError):
    """基準/今回のディレクトリ自体が読めない、または今回の実行にエントリが1件もないときに送出する。"""


def _load_index(result_dir: Path) -> dict:
    index_path = result_dir / "index.json"
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineFreshnessError(f"index.jsonを読み込めない: {index_path}: {exc}") from exc


def _load_vector(result_dir: Path, entry_id: str) -> dict | None:
    vector_path = result_dir / f"{entry_id}.json"
    if not vector_path.exists():
        return None
    try:
        return json.loads(vector_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineFreshnessError(f"指標ベクトルを読み込めない: {vector_path}: {exc}") from exc


def _iter_leaf_diffs(node: object, path: str = ""):
    """`diff_metrics_vector` の出力を辿り、指標1件分の差分辞書を `(パス, 差分)` で列挙する。

    `_diff_metric` が返す辞書（`before` / `after` / `diff` を持つ）を葉として扱う。
    """
    if isinstance(node, dict):
        if {"before", "after", "diff"} <= node.keys():
            yield path, node
            return
        for key, value in node.items():
            yield from _iter_leaf_diffs(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _iter_leaf_diffs(value, f"{path}[{i}]")


def _mismatches(diff: dict) -> list[dict]:
    """`diff_metrics_vector` の出力から、基準/今回で値が一致しない指標のパス一覧を抽出する。

    不一致とするのは次のいずれか（モジュールdocstring「一致の判定方法」参照。ビット完全一致）：

    - 両方が値を持ち、値そのものが異なる（`diff["diff"] != 0.0`）
    - 片方だけが欠測（値の有無が基準/今回で食い違う）。両方欠測（区間境界が未注釈の音源など、
      既知の欠測が両側で一致している場合）は、差を捏造しないため不一致に含めない
    """
    compared = {k: v for k, v in diff.items() if k in ("overall", "segments", "bands", "trajectories")}
    mismatches = []
    for path, leaf in _iter_leaf_diffs(compared):
        before, after = leaf["before"], leaf["after"]
        if before is None and after is None:
            continue
        if before is None or after is None or leaf["diff"] != 0.0:
            mismatches.append({"path": path, "baseline": before, "current": after})
    return mismatches


def check_baseline_freshness(baseline_dir: str | Path, current_dir: str | Path) -> dict:
    """`corpus/baseline/` が今回の `run-corpus` 出力と一致しているかを検証する。

    比較対象は今回の実行（`current_dir`）の全エントリを起点にする。status: "ok" 以外
    （missing / error）のエントリは比較対象から除外する（モジュールdocstring
    「status: "ok" 以外（missing / error）の扱い」参照）。基準（`baseline_dir`）に対応する
    エントリが存在しないもの、または一つでも指標値が食い違うものは「陳腐（stale）」として報告する。

    Returns:
        以下を持つ辞書：
        - `fresh`（bool）：陳腐なエントリが1件もなければTrue
        - `compared_ids`：実際に比較し、一致したidの一覧
        - `skipped`：`status: "ok"` 以外のため除外したidと理由の一覧
        - `stale_entries`：陳腐と判定したid・不一致箇所（`mismatches`）・理由（`reason`、
          基準側にエントリが無い場合のみ）の一覧
    """
    baseline_dir = Path(baseline_dir)
    current_dir = Path(current_dir)

    baseline_index = _load_index(baseline_dir)
    current_index = _load_index(current_dir)
    baseline_statuses = {e["id"]: e["status"] for e in baseline_index.get("entries", [])}

    current_entries = current_index.get("entries", [])
    if not current_entries:
        raise BaselineFreshnessError(f"今回の実行に1件もエントリがない: {current_dir}")

    compared_ids: list[str] = []
    skipped: list[dict] = []
    stale_entries: list[dict] = []

    for entry in current_entries:
        entry_id = entry["id"]
        status = entry["status"]
        if status != "ok":
            # missing（非同梱音源の取得失敗等）・error（レンダ・指標算出が例外で失敗）の
            # いずれも、値は全欠測（`build_missing_metrics_vector`、同じ形）で出力される。
            # どちらも「今回の実行がこの音源について実値を持たない」ことに変わりなく、
            # 比較すれば必ず「基準は実値、今回は欠測」という不一致になり、baselineが
            # 古いかのように誤検出する。missingを除外する理由（モジュールdocstring
            # 「status: "missing" の扱い」）はerrorにも同様に当てはまるため、
            # "ok" 以外は一律で比較対象から除外する。errorはrun-corpus自体の終了コード1で
            # 別途検出されるべきものであり、本チェックの責務ではない。
            skipped.append(
                {"id": entry_id, "reason": f"今回の実行でstatus: {status}のため比較対象外"}
            )
            continue

        current_vector = _load_vector(current_dir, entry_id)
        if current_vector is None:
            raise BaselineFreshnessError(
                f"今回の指標ベクトルJSONが見つからない: {current_dir / (entry_id + '.json')}"
            )

        baseline_vector = _load_vector(baseline_dir, entry_id)
        if baseline_vector is None:
            reason = (
                "corpus/baseline/にこのエントリが存在しない"
                f"（基準のstatus: {baseline_statuses.get(entry_id, '未収録')}）"
            )
            stale_entries.append({"id": entry_id, "mismatches": [], "reason": reason})
            continue

        diff = diff_metrics_vector(baseline_vector, current_vector)
        mismatches = _mismatches(diff)
        if mismatches:
            stale_entries.append({"id": entry_id, "mismatches": mismatches, "reason": None})
        else:
            compared_ids.append(entry_id)

    return {
        "schema_version": FRESHNESS_SCHEMA_VERSION,
        "fresh": len(stale_entries) == 0,
        "compared_ids": compared_ids,
        "skipped": skipped,
        "stale_entries": stale_entries,
    }


def render_freshness_report(result: dict) -> str:
    """`check_baseline_freshness` の結果を、CIの失敗メッセージ向けテキストに整形する。

    どの音源のどの指標が不一致だったかと、直し方（再実行してコミットする手順）を含める
    （Issue #88完了条件「失敗メッセージには...直ることが分かるようにする」）。
    """
    if result["fresh"]:
        return "corpus/baseline/ is fresh(今回のrun-corpus出力と一致している)。"

    lines = [
        "corpus/baseline/ が現在のエンジンでのrun-corpus出力と一致しない（陳腐化を検出した）。",
        "",
    ]
    for entry in result["stale_entries"]:
        lines.append(f"- {entry['id']}:")
        if entry["reason"]:
            lines.append(f"    {entry['reason']}")
        for m in entry["mismatches"]:
            lines.append(f"    {m['path']}: baseline={m['baseline']!r} != current={m['current']!r}")
    lines.append("")
    lines.append(
        "直し方: 以下を再実行し、corpus/baseline/ の差分をコミットしてください。"
        f"\n    {_FIX_COMMAND}"
    )
    return "\n".join(lines)


__all__ = [
    "FRESHNESS_SCHEMA_VERSION",
    "BaselineFreshnessError",
    "check_baseline_freshness",
    "render_freshness_report",
]
