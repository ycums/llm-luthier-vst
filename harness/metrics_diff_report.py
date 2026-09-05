"""指標差分ツール：人間向けの整形出力（P0-12b-2 #40）。

`harness.metrics_diff.run_metrics_diff` が出力する機械可読な差分JSON（`diff.json`）を、
`AGENTS.md` 第4節が要求する「変更前 / 変更後 / 差」3列の人間向け表形式（Markdown）に整形する。
`docs/04-metrics.md`「出力形式」の「人間向けの整形出力は別途スクリプトで行う」に対応する。

## 算出は行わない

前/後/差の値そのもの・悪化の判定・音源の並び順は、すべて `harness.metrics_diff` が
既に算出済みの値をそのまま表示する。本モジュールはそれをMarkdownに整形するだけで、
新たな計算（差の再算出や向きの再判定）は一切行わない（二重実装による乖離を避ける）。

## 欠測は欠測のまま表示する（差を捏造しない）

`harness.metrics_diff._diff_metric` が `diff: null` を返した指標は、表側でも数値を
でっち上げず「欠測」と表示する。前/後どちらかの値が欠測でも、他方の値だけを表示して
差があるかのように見せることはしない。

## 音源の並び順は #39 の出力をそのまま使う

`diff["targets"]` は既に差の絶対値合計（`total_abs_diff`）が大きい順に並んでいる
（`harness.metrics_diff.run_metrics_diff` docstring参照）。本モジュールは並べ替えを
行わず、与えられた順序のまま音源ごとのセクションを出力する。
"""

from __future__ import annotations

#: 指標1件分の差分辞書（`{before, after, diff, missing_reason, worsened}`）を
#: 表の1行に変換する際の列見出し。
_TABLE_HEADER = "| 指標 | 変更前 | 変更後 | 差 | 判定 |\n|---|---|---|---|---|"

_MISSING = "欠測"


def _fmt(value: float | None, *, signed: bool = False) -> str:
    """浮動小数を表示用の短い文字列にする。欠測（None）は数値をでっち上げず`欠測`とする。"""
    if value is None:
        return _MISSING
    rounded = round(value, 6)
    return f"{rounded:+g}" if signed else f"{rounded:g}"


def _iter_metric_rows(target: dict) -> list[tuple[str, dict]]:
    """1音源分の差分から `(指標名, 差分辞書)` を、metrics_diffと同じ順序で列挙する。

    順序は `harness.metrics_diff.diff_metrics_vector` が `worsened_metrics` を組み立てる
    順序（overall → segments → bands → trajectories）に合わせる。指標名の書式も
    `worsened_metrics` の要素と一致させ、同じ文字列で相互参照できるようにする。
    """
    rows: list[tuple[str, dict]] = []
    for name, d in target["overall"].items():
        rows.append((f"overall.{name}", d))
    for seg, metrics in target["segments"].items():
        for name, d in metrics.items():
            rows.append((f"segments.{seg}.{name}", d))
    for i, band in enumerate(target["bands"]):
        rows.append((f"bands[{i}].error", band["error"]))
    for name, d in target["trajectories"].items():
        rows.append((f"trajectories.{name}", d))
    return rows


def _render_metric_row(name: str, d: dict) -> str:
    if d["diff"] is None:
        verdict = _MISSING
    elif d["worsened"]:
        verdict = "⚠ 悪化"
    else:
        verdict = ""
    diff_cell = _fmt(d["diff"], signed=True) if d["diff"] is not None else _MISSING
    return f"| {name} | {_fmt(d['before'])} | {_fmt(d['after'])} | {diff_cell} | {verdict} |"


def _render_target_section(target: dict) -> str:
    heading = f"### {target['id']}"
    if target["worsened_metrics"]:
        heading += f"（悪化した指標: {len(target['worsened_metrics'])}件）"
    rows = "\n".join(_render_metric_row(name, d) for name, d in _iter_metric_rows(target))
    return f"{heading}\n\n{_TABLE_HEADER}\n{rows}"


def render_diff_report(diff: dict) -> str:
    """`run_metrics_diff` の出力（差分JSON）を、人間向けのMarkdownレポートに整形する。

    音源ごとのセクション（`diff["targets"]` の順序どおり。差の絶対値合計が大きい順）に、
    指標ごとの「変更前 / 変更後 / 差 / 判定」表を並べる。悪化した指標は判定列に
    `⚠ 悪化` と明示し、欠測の指標は前後・差のいずれも `欠測` と表示して数値を捏造しない。
    """
    summary = diff["summary"]
    header = "\n".join(
        [
            "# 指標差分レポート",
            "",
            f"- 比較対象: {summary['compared_count']}件",
            f"- 悪化した音源: {summary['worsened_target_count']}件",
            f"- 悪化した指標（延べ）: {summary['worsened_metric_count']}件",
            f"- 基準のみに存在: {', '.join(diff['only_in_baseline']) or 'なし'}",
            f"- 今回のみに存在: {', '.join(diff['only_in_current']) or 'なし'}",
        ]
    )
    sections = [header] + [_render_target_section(target) for target in diff["targets"]]
    return "\n\n".join(sections)


__all__ = ["render_diff_report"]
