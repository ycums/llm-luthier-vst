"""指標差分ツール：機械可読な差分JSONの算出（P0-12b-1 #39）。

`harness.corpus_runner.run_corpus` が出力する2組の結果ディレクトリ（基準 / 今回。それぞれ
`index.json` と `<id>.json` 群）を受け取り、指標ごとに「前 / 後 / 差」を算出する。
`AGENTS.md` 第4節が要求する「変更前 / 変更後 / 差」の3列出力の実体であり、ここでは
機械可読な差分JSONの算出までを扱う。

## 人間向けの整形出力はスコープ外（P0-12b-2 #40）

`docs/04-metrics.md`「出力形式」の「人間向けの整形出力は別途スクリプトで行う」に従い、
本モジュールは表形式などの整形出力を持たない。実装中の実測（テスト・生成物を除く
変更行数が400行を超過）によりIssue #38 のゲート2に該当し、機械可読な差分JSON（本モジュール、
#39）と人間向けの整形出力（#40）に分割した。

## 基準（baseline）の由来は関知しない

`baseline_dir` がどう作られたか（`corpus/baseline/` にコミットされたものか、他の経路で
用意されたものか）は本モジュールの関知するところではない。`docs/04-metrics.md`
「基準（baseline）指標JSONの取得方法」（Issue #37 / P0-12a）の決定に従い、2つのディレクトリ
パスを受け取るだけの道具として設計する（`harness/corpus_runner.py` がcandidateパスの由来を
知らないのと同じ設計）。

## 悪化の向きは指標ごとに異なる（`AGENTS.md` 第4節「悪化を隠さない」への対応）

指標の値は「小さいほど良い（誤差・距離）」と「大きいほど良い（相関）」の2種類が混在する
（`harness/metrics.py` のdocstring参照）。同じ「値が増えた」でも指標によって改善・悪化が
逆転するため、指標名ごとに向きを固定の対応表（`_METRIC_DIRECTIONS`）で持つ。

- `msstft` / `mfcc` / `f0_dist` / `formant_dist` / 帯域別誤差（`bands[].error`）：非負の誤差・
  距離。値が増えた（`after > before`）ときに悪化とする
- `loudness_diff_db`：符号付き（`docs/04-metrics.md` の定義どおり、正ならcandidateが
  targetより大きい）。0からの絶対距離が増えた（`abs(after) > abs(before)`）ときに悪化とする
- `transient_env_corr`：ピアソン相関係数（-1〜1、1が完全一致）。値が減った
  （`after < before`）ときに悪化とする

一方または両方が欠測（`value: null`）の指標は、悪化の判定対象にしない（判定不能であって
「悪化していない」わけでもない。差を捏造しない）。

## 終了コードは指標の値に依存しない（Issue #39 完了条件、Q-006 暫定の扱い）

CIで指標悪化を自動ブロックするかどうかは未決（`docs/06-open-questions.md` Q-006）。暫定の
扱い（ブロックせず明示のみ）に従い、悪化がいくつあっても `run_metrics_diff` は例外を送出せず、
`harness/cli.py` の `diff-corpus` サブコマンドも常に終了コード0を返す。0以外を返すのは、
基準/今回のいずれかの結果ディレクトリ自体が読めない・エントリが1件もない等、算出そのものが
成立しない場合（`MetricsDiffError`、`ManifestError` と同じ「実行不能」の位置づけ）に限る。
"""

from __future__ import annotations

import json
from pathlib import Path

#: 差分JSON自体の構造のバージョン（指標ベクトルのスキーマバージョンとは別物）。
DIFF_SCHEMA_VERSION = "1.0.0"

#: `overall` / `segments.*` が共通で持つ3指標。
_OVERALL_METRIC_NAMES = ("msstft", "mfcc", "loudness_diff_db")
_SEGMENT_NAMES = ("attack", "transition", "sustain", "release")
_TRAJECTORY_METRIC_NAMES = ("transient_env_corr", "f0_dist", "formant_dist")

#: 指標ごとの悪化判定の向き。モジュールdocstring「悪化の向きは指標ごとに異なる」参照。
_LOWER_IS_BETTER = "lower_is_better"
_HIGHER_IS_BETTER = "higher_is_better"
_ABS_LOWER_IS_BETTER = "abs_lower_is_better"

_METRIC_DIRECTIONS: dict[str, str] = {
    "msstft": _LOWER_IS_BETTER,
    "mfcc": _LOWER_IS_BETTER,
    "loudness_diff_db": _ABS_LOWER_IS_BETTER,
    "transient_env_corr": _HIGHER_IS_BETTER,
    "f0_dist": _LOWER_IS_BETTER,
    "formant_dist": _LOWER_IS_BETTER,
    "bands.error": _LOWER_IS_BETTER,
}


class MetricsDiffError(RuntimeError):
    """結果ディレクトリが読めない、または比較できる対象が1件もないときに送出する（実行不能）。"""


def _load_index(result_dir: Path) -> dict:
    index_path = result_dir / "index.json"
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetricsDiffError(f"index.jsonを読み込めない: {index_path}: {exc}") from exc


def _load_vector(result_dir: Path, entry_id: str) -> dict:
    vector_path = result_dir / f"{entry_id}.json"
    try:
        return json.loads(vector_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetricsDiffError(f"指標ベクトルを読み込めない: {vector_path}: {exc}") from exc


def _diff_metric(before: dict, after: dict, direction: str) -> dict:
    """指標1件分の `{value, missing_reason}` の組から「前/後/差/悪化」を組み立てる。

    どちらかが欠測なら、差を捏造せず `diff=None` とし、悪化判定もしない
    （モジュールdocstring「一方または両方が欠測の指標」参照）。
    """
    before_value = before["value"]
    after_value = after["value"]

    if before_value is None or after_value is None:
        reasons = [r for r in (before["missing_reason"], after["missing_reason"]) if r]
        return {
            "before": before_value,
            "after": after_value,
            "diff": None,
            "missing_reason": " / ".join(reasons) if reasons else "前後いずれかの値が欠測",
            "worsened": False,
        }

    diff = after_value - before_value
    if direction == _HIGHER_IS_BETTER:
        worsened = after_value < before_value
    elif direction == _ABS_LOWER_IS_BETTER:
        worsened = abs(after_value) > abs(before_value)
    else:
        worsened = after_value > before_value

    return {
        "before": before_value,
        "after": after_value,
        "diff": diff,
        "missing_reason": None,
        "worsened": worsened,
    }


def _diff_overall_set(before: dict, after: dict) -> dict:
    return {
        name: _diff_metric(before[name], after[name], _METRIC_DIRECTIONS[name])
        for name in _OVERALL_METRIC_NAMES
    }


def _diff_bands(before_bands: list[dict], after_bands: list[dict]) -> list[dict]:
    """帯域配列を通し番号で対応付けて差分を取る。

    帯域構成（`lo_hz`/`hi_hz`の並び）そのものが基準/今回で異なる場合は、値の比較自体が
    無意味なので欠測として扱う（差を捏造しない）。帯域数が異なる場合も同様に、対応する
    帯域を持たない側を欠測として扱う。
    """
    diffs = []
    for i in range(max(len(before_bands), len(after_bands))):
        if i >= len(before_bands):
            b = after_bands[i]
            diffs.append(
                {
                    "lo_hz": b["lo_hz"],
                    "hi_hz": b["hi_hz"],
                    "error": _diff_metric(
                        {"value": None, "missing_reason": "基準側に対応する帯域がない"},
                        b["error"],
                        _METRIC_DIRECTIONS["bands.error"],
                    ),
                }
            )
            continue
        if i >= len(after_bands):
            b = before_bands[i]
            diffs.append(
                {
                    "lo_hz": b["lo_hz"],
                    "hi_hz": b["hi_hz"],
                    "error": _diff_metric(
                        b["error"],
                        {"value": None, "missing_reason": "今回側に対応する帯域がない"},
                        _METRIC_DIRECTIONS["bands.error"],
                    ),
                }
            )
            continue

        before_band, after_band = before_bands[i], after_bands[i]
        if (before_band["lo_hz"], before_band["hi_hz"]) != (
            after_band["lo_hz"],
            after_band["hi_hz"],
        ):
            reason = (
                f"帯域境界が基準/今回で異なるため比較不能"
                f"（基準: [{before_band['lo_hz']}, {before_band['hi_hz']}), "
                f"今回: [{after_band['lo_hz']}, {after_band['hi_hz']})）"
            )
            diffs.append(
                {
                    "lo_hz": before_band["lo_hz"],
                    "hi_hz": before_band["hi_hz"],
                    "error": _diff_metric(
                        {"value": None, "missing_reason": reason},
                        {"value": None, "missing_reason": reason},
                        _METRIC_DIRECTIONS["bands.error"],
                    ),
                }
            )
            continue

        diffs.append(
            {
                "lo_hz": before_band["lo_hz"],
                "hi_hz": before_band["hi_hz"],
                "error": _diff_metric(
                    before_band["error"], after_band["error"], _METRIC_DIRECTIONS["bands.error"]
                ),
            }
        )
    return diffs


def diff_metrics_vector(before: dict, after: dict) -> dict:
    """指標ベクトル1組（基準 / 今回）から、指標1件ごとの前/後/差/悪化を組み立てる。

    `total_abs_diff`（差の絶対値の合計。欠測は0扱い、音源の並べ替えに使う）と
    `worsened_metrics`（悪化した指標のドット区切りパスの一覧）を併せて返す。
    """
    overall = _diff_overall_set(before["overall"], after["overall"])
    segments = {
        seg: _diff_overall_set(before["segments"][seg], after["segments"][seg])
        for seg in _SEGMENT_NAMES
    }
    bands = _diff_bands(before["bands"], after["bands"])
    trajectories = {
        name: _diff_metric(
            before["trajectories"][name], after["trajectories"][name], _METRIC_DIRECTIONS[name]
        )
        for name in _TRAJECTORY_METRIC_NAMES
    }

    total_abs_diff = 0.0
    worsened_metrics: list[str] = []
    for name, d in overall.items():
        total_abs_diff += abs(d["diff"]) if d["diff"] is not None else 0.0
        if d["worsened"]:
            worsened_metrics.append(f"overall.{name}")
    for seg, metrics in segments.items():
        for name, d in metrics.items():
            total_abs_diff += abs(d["diff"]) if d["diff"] is not None else 0.0
            if d["worsened"]:
                worsened_metrics.append(f"segments.{seg}.{name}")
    for i, band in enumerate(bands):
        d = band["error"]
        total_abs_diff += abs(d["diff"]) if d["diff"] is not None else 0.0
        if d["worsened"]:
            worsened_metrics.append(f"bands[{i}].error")
    for name, d in trajectories.items():
        total_abs_diff += abs(d["diff"]) if d["diff"] is not None else 0.0
        if d["worsened"]:
            worsened_metrics.append(f"trajectories.{name}")

    return {
        "overall": overall,
        "segments": segments,
        "bands": bands,
        "trajectories": trajectories,
        "total_abs_diff": total_abs_diff,
        "worsened_metrics": worsened_metrics,
    }


def run_metrics_diff(baseline_dir: str | Path, current_dir: str | Path) -> dict:
    """基準/今回の結果ディレクトリ2組から、コーパス全体の差分を算出する。

    `<baseline_dir>`・`<current_dir>` はいずれも `harness.corpus_runner.run_corpus` の出力形式
    （`index.json` と `<id>.json` 群）を持つディレクトリ。両方に共通するidだけを比較し、
    差の絶対値の合計（`total_abs_diff`）が大きい順に並べた `targets` 一覧を返す
    （#14 で図示する音源の選定に使う）。片方にしか存在しないidは `only_in_baseline` /
    `only_in_current` に記録し、差の捏造を避けるため比較対象に含めない。
    """
    baseline_dir = Path(baseline_dir)
    current_dir = Path(current_dir)

    baseline_index = _load_index(baseline_dir)
    current_index = _load_index(current_dir)

    baseline_ids = {e["id"] for e in baseline_index.get("entries", [])}
    current_ids = {e["id"] for e in current_index.get("entries", [])}
    common_ids = sorted(baseline_ids & current_ids)
    only_in_baseline = sorted(baseline_ids - current_ids)
    only_in_current = sorted(current_ids - baseline_ids)

    if not common_ids:
        raise MetricsDiffError(
            f"基準と今回に共通するidが1件もない（基準: {sorted(baseline_ids)}, "
            f"今回: {sorted(current_ids)}）"
        )

    targets = []
    for entry_id in common_ids:
        before = _load_vector(baseline_dir, entry_id)
        after = _load_vector(current_dir, entry_id)
        diff = diff_metrics_vector(before, after)
        diff["id"] = entry_id
        targets.append(diff)

    # 差の絶対値が大きい順（同点はid昇順で安定させる。#14の音源選定に使う一覧）。
    targets.sort(key=lambda t: (-t["total_abs_diff"], t["id"]))

    return {
        "schema_version": DIFF_SCHEMA_VERSION,
        "baseline_dir": str(baseline_dir),
        "current_dir": str(current_dir),
        "targets": targets,
        "only_in_baseline": only_in_baseline,
        "only_in_current": only_in_current,
        "summary": {
            "compared_count": len(targets),
            "worsened_target_count": sum(1 for t in targets if t["worsened_metrics"]),
            "worsened_metric_count": sum(len(t["worsened_metrics"]) for t in targets),
        },
    }


__all__ = [
    "DIFF_SCHEMA_VERSION",
    "MetricsDiffError",
    "diff_metrics_vector",
    "run_metrics_diff",
]
