"""harness.metrics_diff_report のテスト（P0-12b-2 #40）。

Issue #40 の完了条件を検証する：
- #39 が出力する差分JSONから、指標ごとの「前 / 後 / 差」を含む人間向けの表形式出力を生成できる
- 悪化した指標が出力上で明示的に区別される（`AGENTS.md` 第4節「悪化を隠さない」）
- 一方または両方が欠測である指標は、差を捏造せず欠測として表示される
- 音源は差の絶対値が大きい順に並ぶ（#39 の順序をそのまま使う）
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.cli import main as cli_main
from harness.metrics import build_missing_metrics_vector
from harness.metrics_diff import run_metrics_diff
from harness.metrics_diff_report import render_diff_report


def _base_vector(target: str) -> dict:
    return build_missing_metrics_vector(target, "test: 未設定")


def _set(value: float) -> dict:
    return {"value": value, "missing_reason": None}


def _make_ok_vector(target: str, *, msstft=0.0, corr=0.0, loudness=0.0) -> dict:
    v = _base_vector(target)
    v["overall"]["msstft"] = _set(msstft)
    v["overall"]["mfcc"] = _set(0.0)
    v["overall"]["loudness_diff_db"] = _set(loudness)
    for seg in ("attack", "transition", "sustain", "release"):
        v["segments"][seg]["msstft"] = _set(0.0)
        v["segments"][seg]["mfcc"] = _set(0.0)
        v["segments"][seg]["loudness_diff_db"] = _set(0.0)
    for band in v["bands"]:
        band["error"] = _set(0.0)
    v["trajectories"]["transient_env_corr"] = _set(corr)
    v["trajectories"]["f0_dist"] = _set(0.0)
    v["trajectories"]["formant_dist"] = _set(0.0)
    return v


def _write_result_dir(out_dir: Path, vectors: dict[str, dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for entry_id, vector in vectors.items():
        (out_dir / f"{entry_id}.json").write_text(
            json.dumps(vector, ensure_ascii=False), encoding="utf-8"
        )
        entries.append(
            {"id": entry_id, "status": "ok", "output_file": f"{entry_id}.json", "elapsed_s": 0.0}
        )
    index = {"schema_version": "1.0.0", "entry_count": len(entries), "entries": entries}
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")


def _diff_for_single_metric(**kwargs) -> dict:
    before = _make_ok_vector("a", **{k: v[0] for k, v in kwargs.items()})
    after = _make_ok_vector("a", **{k: v[1] for k, v in kwargs.items()})
    from harness.metrics_diff import diff_metrics_vector

    d = diff_metrics_vector(before, after)
    d["id"] = "a"
    return {
        "schema_version": "1.0.0",
        "baseline_dir": "baseline",
        "current_dir": "current",
        "targets": [d],
        "only_in_baseline": [],
        "only_in_current": [],
        "summary": {
            "compared_count": 1,
            "worsened_target_count": 1 if d["worsened_metrics"] else 0,
            "worsened_metric_count": len(d["worsened_metrics"]),
        },
    }


def test_report_contains_before_after_diff_for_a_metric() -> None:
    diff = _diff_for_single_metric(msstft=(0.10, 0.14))

    report = render_diff_report(diff)

    assert "overall.msstft" in report
    assert "0.1" in report
    assert "0.14" in report
    assert "+0.04" in report


def test_worsened_metric_is_marked_distinctly_from_improved_one() -> None:
    diff = _diff_for_single_metric(msstft=(0.10, 0.20))  # 誤差指標: 増加 = 悪化

    report = render_diff_report(diff)

    lines = [line for line in report.splitlines() if "overall.msstft" in line]
    assert len(lines) == 1
    assert "悪化" in lines[0]


def test_improved_metric_is_not_marked_as_worsened() -> None:
    diff = _diff_for_single_metric(msstft=(0.20, 0.10))  # 誤差指標: 減少 = 改善

    report = render_diff_report(diff)

    lines = [line for line in report.splitlines() if "overall.msstft" in line]
    assert len(lines) == 1
    assert "悪化" not in lines[0]


def test_missing_metric_is_shown_as_missing_not_a_fabricated_diff() -> None:
    before = _base_vector("a")  # 全欠測
    after = _make_ok_vector("a", msstft=0.1)
    from harness.metrics_diff import diff_metrics_vector

    d = diff_metrics_vector(before, after)
    d["id"] = "a"
    diff = {
        "schema_version": "1.0.0",
        "baseline_dir": "baseline",
        "current_dir": "current",
        "targets": [d],
        "only_in_baseline": [],
        "only_in_current": [],
        "summary": {"compared_count": 1, "worsened_target_count": 0, "worsened_metric_count": 0},
    }

    report = render_diff_report(diff)

    lines = [line for line in report.splitlines() if "overall.msstft" in line]
    assert len(lines) == 1
    cells = [c.strip() for c in lines[0].strip("|").split("|")]
    _name, before_cell, after_cell, diff_cell, verdict_cell = cells
    # beforeは欠測（値がないので欠測のまま）。afterは実測値の0.1をそのまま表示してよいが、
    # 差（diff）は算出できないので欠測とし、0.1をそのまま差として捏造しない。
    assert before_cell == "欠測"
    assert after_cell == "0.1"
    assert diff_cell == "欠測"
    assert verdict_cell == "欠測"


def test_targets_are_rendered_in_the_order_given_by_diff_json(tmp_path: Path) -> None:
    """音源の並び順は#39（run_metrics_diffの`targets`）の並びをそのまま使う。"""
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(
        baseline_dir,
        {"small": _make_ok_vector("small", msstft=0.10), "big": _make_ok_vector("big", msstft=0.10)},
    )
    _write_result_dir(
        current_dir,
        {
            "small": _make_ok_vector("small", msstft=0.11),  # diff 0.01
            "big": _make_ok_vector("big", msstft=0.50),  # diff 0.40
        },
    )
    diff = run_metrics_diff(baseline_dir, current_dir)
    assert [t["id"] for t in diff["targets"]] == ["big", "small"]  # #39が既に降順に並べている

    report = render_diff_report(diff)

    assert report.index("big") < report.index("small")


def test_report_includes_only_in_baseline_and_only_in_current() -> None:
    diff = {
        "schema_version": "1.0.0",
        "baseline_dir": "baseline",
        "current_dir": "current",
        "targets": [],
        "only_in_baseline": ["removed"],
        "only_in_current": ["added"],
        "summary": {"compared_count": 0, "worsened_target_count": 0, "worsened_metric_count": 0},
    }

    report = render_diff_report(diff)

    assert "removed" in report
    assert "added" in report


# --- CLI ---


def test_cli_diff_corpus_writes_report_md(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    out_dir = tmp_path / "out"
    _write_result_dir(baseline_dir, {"a": _make_ok_vector("a", msstft=0.1)})
    _write_result_dir(current_dir, {"a": _make_ok_vector("a", msstft=0.9)})  # 大きく悪化

    exit_code = cli_main(
        [
            "diff-corpus",
            "--baseline",
            str(baseline_dir),
            "--current",
            str(current_dir),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    report = (out_dir / "report.md").read_text(encoding="utf-8")
    assert "overall.msstft" in report
    assert "悪化" in report
