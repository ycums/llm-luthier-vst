"""harness.metrics_diff のテスト（P0-12b-1 #39）。

Issue #39 の完了条件を検証する：
- 2組の指標JSON（基準 / 今回）を受け取り、指標ごとに「前 / 後 / 差」の3値を出力する
- 悪化した指標が機械可読な差分JSON上で明示的に区別される
- 差の絶対値が大きい順に音源を並べた一覧が出力される
- 一方または両方が欠測である指標について、差を捏造せず欠測として扱う
- 同一の指標JSONを2つ与えたとき、全差分が0になり、悪化の指摘が0件になる
- 指標の値に関わらず終了コードが0である

人間向けの表形式出力（P0-12b-2 #40）は `tests/test_metrics_diff_report.py` で検証する。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from harness.cli import main as cli_main
from harness.metrics import build_missing_metrics_vector
from harness.metrics_diff import (
    MetricsDiffError,
    diff_metrics_vector,
    run_metrics_diff,
)


def _base_vector(target: str) -> dict:
    """全指標が欠測の骨格（#3のスキーマにvalid）を作り、テストで値を差し替えられるようにする。"""
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


# --- diff_metrics_vector（1音源分）のテスト ---


def test_diff_reports_before_after_and_diff_for_each_metric() -> None:
    before = _make_ok_vector("a", msstft=0.10)
    after = _make_ok_vector("a", msstft=0.14)

    diff = diff_metrics_vector(before, after)

    d = diff["overall"]["msstft"]
    assert d["before"] == pytest.approx(0.10)
    assert d["after"] == pytest.approx(0.14)
    assert d["diff"] == pytest.approx(0.04)


def test_identical_vectors_produce_zero_diff_and_no_worsening() -> None:
    vector = _make_ok_vector("a", msstft=0.2, corr=0.9, loudness=-1.5)

    diff = diff_metrics_vector(vector, copy.deepcopy(vector))

    assert diff["total_abs_diff"] == pytest.approx(0.0, abs=1e-12)
    assert diff["worsened_metrics"] == []
    assert diff["overall"]["msstft"]["diff"] == pytest.approx(0.0)


def test_increase_in_error_metric_is_worsened() -> None:
    """msstft/mfcc/f0_dist/formant_dist/帯域誤差は非負の誤差。増えたら悪化。"""
    before = _make_ok_vector("a", msstft=0.10)
    after = _make_ok_vector("a", msstft=0.20)

    diff = diff_metrics_vector(before, after)

    assert diff["overall"]["msstft"]["worsened"] is True
    assert "overall.msstft" in diff["worsened_metrics"]


def test_decrease_in_error_metric_is_not_worsened() -> None:
    before = _make_ok_vector("a", msstft=0.20)
    after = _make_ok_vector("a", msstft=0.10)

    diff = diff_metrics_vector(before, after)

    assert diff["overall"]["msstft"]["worsened"] is False
    assert "overall.msstft" not in diff["worsened_metrics"]


def test_correlation_decrease_is_worsened_but_increase_is_not() -> None:
    """transient_env_corr は相関係数。1に近いほど良いので、減ると悪化・増えると改善。"""
    before = _make_ok_vector("a", corr=0.9)
    worse_after = _make_ok_vector("a", corr=0.5)
    better_after = _make_ok_vector("a", corr=0.95)

    worse_diff = diff_metrics_vector(before, worse_after)
    better_diff = diff_metrics_vector(before, better_after)

    assert worse_diff["trajectories"]["transient_env_corr"]["worsened"] is True
    assert better_diff["trajectories"]["transient_env_corr"]["worsened"] is False


def test_loudness_diff_worsened_by_absolute_distance_from_zero() -> None:
    """loudness_diff_dbは符号付き。0からの絶対距離が増えたら悪化（符号が反転しても悪化しうる）。"""
    before = _make_ok_vector("a", loudness=1.0)
    farther_after = _make_ok_vector("a", loudness=2.0)
    flipped_but_farther_after = _make_ok_vector("a", loudness=-3.0)
    closer_after = _make_ok_vector("a", loudness=0.2)

    assert diff_metrics_vector(before, farther_after)["overall"]["loudness_diff_db"]["worsened"]
    assert diff_metrics_vector(before, flipped_but_farther_after)["overall"]["loudness_diff_db"][
        "worsened"
    ]
    assert not diff_metrics_vector(before, closer_after)["overall"]["loudness_diff_db"]["worsened"]


def test_missing_on_either_side_yields_null_diff_without_fabrication() -> None:
    before = _base_vector("a")  # 全欠測
    after = _make_ok_vector("a", msstft=0.1)

    diff = diff_metrics_vector(before, after)

    d = diff["overall"]["msstft"]
    assert d["diff"] is None
    assert d["missing_reason"] is not None
    assert d["worsened"] is False
    assert "overall.msstft" not in diff["worsened_metrics"]
    # 欠測は差の絶対値合計にも寄与しない（捏造しない）。
    assert diff["total_abs_diff"] == pytest.approx(0.0, abs=1e-12)


def test_missing_on_both_sides_is_still_missing_not_fabricated() -> None:
    before = _base_vector("a")
    after = _base_vector("a")

    diff = diff_metrics_vector(before, after)

    assert diff["overall"]["msstft"]["diff"] is None
    assert diff["overall"]["msstft"]["missing_reason"] is not None


def test_band_boundary_mismatch_is_treated_as_missing_not_a_fabricated_diff() -> None:
    before = _make_ok_vector("a")
    after = _make_ok_vector("a")
    after["bands"][0]["hi_hz"] = before["bands"][0]["hi_hz"] + 1.0  # 帯域端をずらす

    diff = diff_metrics_vector(before, after)

    assert diff["bands"][0]["error"]["diff"] is None
    assert "異なる" in diff["bands"][0]["error"]["missing_reason"]


# --- run_metrics_diff（コーパス全体）のテスト ---


def test_run_metrics_diff_sorts_targets_by_absolute_diff_descending(tmp_path: Path) -> None:
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

    result = run_metrics_diff(baseline_dir, current_dir)

    assert [t["id"] for t in result["targets"]] == ["big", "small"]


def test_run_metrics_diff_identical_inputs_zero_diff_and_no_worsening(tmp_path: Path) -> None:
    result_dir = tmp_path / "result"
    _write_result_dir(
        result_dir,
        {"a": _make_ok_vector("a", msstft=0.3, corr=0.8), "b": _make_ok_vector("b", msstft=0.1)},
    )

    result = run_metrics_diff(result_dir, result_dir)

    assert result["summary"]["worsened_target_count"] == 0
    assert result["summary"]["worsened_metric_count"] == 0
    for target in result["targets"]:
        assert target["total_abs_diff"] == pytest.approx(0.0, abs=1e-12)


def test_run_metrics_diff_marks_only_common_ids_and_lists_the_rest(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(baseline_dir, {"a": _make_ok_vector("a"), "removed": _make_ok_vector("removed")})
    _write_result_dir(current_dir, {"a": _make_ok_vector("a"), "added": _make_ok_vector("added")})

    result = run_metrics_diff(baseline_dir, current_dir)

    assert [t["id"] for t in result["targets"]] == ["a"]
    assert result["only_in_baseline"] == ["removed"]
    assert result["only_in_current"] == ["added"]


def test_run_metrics_diff_raises_for_unreadable_index(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(current_dir, {"a": _make_ok_vector("a")})

    with pytest.raises(MetricsDiffError):
        run_metrics_diff(baseline_dir, current_dir)


def test_run_metrics_diff_raises_when_no_common_ids(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(baseline_dir, {"a": _make_ok_vector("a")})
    _write_result_dir(current_dir, {"b": _make_ok_vector("b")})

    with pytest.raises(MetricsDiffError):
        run_metrics_diff(baseline_dir, current_dir)


# --- CLI ---


def test_cli_diff_corpus_writes_json_and_exits_zero_even_with_worsening(
    tmp_path: Path,
) -> None:
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
    diff_json = json.loads((out_dir / "diff.json").read_text(encoding="utf-8"))
    assert diff_json["summary"]["worsened_target_count"] == 1
    assert diff_json["targets"][0]["overall"]["msstft"]["worsened"] is True


def test_cli_diff_corpus_exit_code_is_two_when_unreadable(tmp_path: Path) -> None:
    exit_code = cli_main(
        [
            "diff-corpus",
            "--baseline",
            str(tmp_path / "does_not_exist"),
            "--current",
            str(tmp_path / "also_does_not_exist"),
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 2
