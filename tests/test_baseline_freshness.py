"""harness.baseline_freshness のテスト（Issue #88、#86決定の実装）。

Issue #88 完了条件を検証する：
- `corpus/baseline/<id>.json` が今回の `run-corpus` 出力 `<id>.json` と一致するかを判定する
- 今回の実行で `status: "missing"` になったエントリは比較対象から除外され、
  値がどれだけ食い違っていても不一致として扱わない
- 基準側に対応するエントリが存在しない場合は陳腐（不一致）として報告する
- 両側とも欠測（`value: null`）の指標は不一致に含めない（区間境界未注釈などの既知の欠測）
- 片側だけが欠測の指標は不一致として報告する
- 一致した場合は `fresh: True` を返す
- 比較対象ディレクトリが読めない・今回の実行にエントリが1件もない場合は例外を送出する

一致の判定方法（ビット完全一致、許容誤差なし）の採用理由は `harness/baseline_freshness.py`
のモジュールdocstring、および `docs/04-metrics.md`「一致の判定方法」を参照。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.baseline_freshness import (
    BaselineFreshnessError,
    check_baseline_freshness,
    render_freshness_report,
)
from harness.cli import main as cli_main
from harness.metrics import build_missing_metrics_vector


def _base_vector(target: str) -> dict:
    return build_missing_metrics_vector(target, "test: 未設定")


def _set(value: float) -> dict:
    return {"value": value, "missing_reason": None}


def _make_ok_vector(target: str, *, msstft: float = 0.0) -> dict:
    v = _base_vector(target)
    v["overall"]["msstft"] = _set(msstft)
    v["overall"]["mfcc"] = _set(0.0)
    v["overall"]["loudness_diff_db"] = _set(0.0)
    for seg in ("attack", "transition", "sustain", "release"):
        v["segments"][seg]["msstft"] = _set(0.0)
        v["segments"][seg]["mfcc"] = _set(0.0)
        v["segments"][seg]["loudness_diff_db"] = _set(0.0)
    for band in v["bands"]:
        band["error"] = _set(0.0)
    v["trajectories"]["transient_env_corr"] = _set(0.0)
    v["trajectories"]["f0_dist"] = _set(0.0)
    v["trajectories"]["formant_dist"] = _set(0.0)
    return v


def _write_result_dir(out_dir: Path, entries: dict[str, tuple[dict | None, str]]) -> None:
    """`entries`: id -> (指標ベクトル or None, status)。run-corpusの出力形式を模す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    index_entries = []
    for entry_id, (vector, status) in entries.items():
        if vector is not None:
            (out_dir / f"{entry_id}.json").write_text(
                json.dumps(vector, ensure_ascii=False), encoding="utf-8"
            )
        index_entries.append(
            {"id": entry_id, "status": status, "output_file": f"{entry_id}.json", "elapsed_s": 0.0}
        )
    index = {
        "schema_version": "1.0.0",
        "entry_count": len(index_entries),
        "entries": index_entries,
        "summary": {
            "ok": sum(1 for _, s in entries.values() if s == "ok"),
            "missing": sum(1 for _, s in entries.values() if s == "missing"),
            "error": sum(1 for _, s in entries.values() if s == "error"),
        },
    }
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")


def test_fresh_when_baseline_and_current_match_exactly(tmp_path: Path) -> None:
    vector = _make_ok_vector("a", msstft=0.2)
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(baseline_dir, {"a": (vector, "ok")})
    _write_result_dir(current_dir, {"a": (vector, "ok")})

    result = check_baseline_freshness(baseline_dir, current_dir)

    assert result["fresh"] is True
    assert result["compared_ids"] == ["a"]
    assert result["stale_entries"] == []
    assert result["skipped"] == []


def test_stale_when_a_value_differs(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(baseline_dir, {"a": (_make_ok_vector("a", msstft=0.1), "ok")})
    _write_result_dir(current_dir, {"a": (_make_ok_vector("a", msstft=0.9), "ok")})

    result = check_baseline_freshness(baseline_dir, current_dir)

    assert result["fresh"] is False
    assert result["compared_ids"] == []
    assert len(result["stale_entries"]) == 1
    stale = result["stale_entries"][0]
    assert stale["id"] == "a"
    paths = {m["path"] for m in stale["mismatches"]}
    assert "overall.msstft" in paths


def test_missing_status_in_current_is_excluded_even_if_values_would_differ(
    tmp_path: Path,
) -> None:
    """非同梱音源の取得失敗（status: missing）を「baselineが古い」と誤検出しない。"""
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(baseline_dir, {"a": (_make_ok_vector("a", msstft=0.1), "ok")})
    # 今回はmissing（取得失敗）。ベクトルJSON自体は欠測ベクトルとして残っていても比較しない。
    _write_result_dir(current_dir, {"a": (_base_vector("a"), "missing")})

    result = check_baseline_freshness(baseline_dir, current_dir)

    assert result["fresh"] is True
    assert result["compared_ids"] == []
    assert result["stale_entries"] == []
    assert result["skipped"] == [
        {"id": "a", "reason": "今回の実行でstatus: missingのため比較対象外"}
    ]


def test_error_status_in_current_is_excluded_even_if_values_would_differ(tmp_path: Path) -> None:
    """レンダ・指標算出が例外で失敗した（status: error）エントリも、missingと同じ理由で除外する。

    error のベクトルはmissingと同じ形（全指標欠測、`build_missing_metrics_vector`）で
    出力されるため、除外しないと「基準は実値を持つが今回は全欠測」という一時的な失敗が
    毎回「baselineが古い」という紛らわしい誤検出になる（一時的な失敗はrun-corpus自体の
    終了コード1で別途検出されるべきものであり、本チェックの責務ではない）。
    """
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(baseline_dir, {"a": (_make_ok_vector("a", msstft=0.1), "ok")})
    _write_result_dir(current_dir, {"a": (_base_vector("a"), "error")})

    result = check_baseline_freshness(baseline_dir, current_dir)

    assert result["fresh"] is True
    assert result["compared_ids"] == []
    assert result["stale_entries"] == []
    assert result["skipped"] == [
        {"id": "a", "reason": "今回の実行でstatus: errorのため比較対象外"}
    ]


def test_stale_when_baseline_has_no_matching_entry(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(baseline_dir, {})
    _write_result_dir(current_dir, {"new_entry": (_make_ok_vector("new_entry"), "ok")})

    result = check_baseline_freshness(baseline_dir, current_dir)

    assert result["fresh"] is False
    assert result["stale_entries"][0]["id"] == "new_entry"
    assert result["stale_entries"][0]["mismatches"] == []
    assert "存在しない" in result["stale_entries"][0]["reason"]


def test_both_sides_missing_same_field_is_not_a_mismatch(tmp_path: Path) -> None:
    """区間境界が未注釈の音源は、両側とも同じ理由で欠測になる。これは陳腐化ではない。"""
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    # transition/sustain/releaseが両側とも欠測のまま（_base_vectorの初期状態を流用）。
    vector = _make_ok_vector("a")
    for seg in ("transition", "sustain", "release"):
        vector["segments"][seg] = _base_vector("a")["segments"][seg]
    _write_result_dir(baseline_dir, {"a": (vector, "ok")})
    _write_result_dir(current_dir, {"a": (vector, "ok")})

    result = check_baseline_freshness(baseline_dir, current_dir)

    assert result["fresh"] is True


def test_one_side_missing_the_other_has_a_value_is_a_mismatch(tmp_path: Path) -> None:
    """片側だけ欠測から実値に変わった場合（例: エンジンが鳴るようになった）は不一致として検出する。"""
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    stale_vector = _make_ok_vector("a")
    stale_vector["trajectories"]["f0_dist"] = {
        "value": None,
        "missing_reason": "test: 当時は無音レンダのため欠測",
    }
    fresh_vector = _make_ok_vector("a")
    fresh_vector["trajectories"]["f0_dist"] = _set(12.3)
    _write_result_dir(baseline_dir, {"a": (stale_vector, "ok")})
    _write_result_dir(current_dir, {"a": (fresh_vector, "ok")})

    result = check_baseline_freshness(baseline_dir, current_dir)

    assert result["fresh"] is False
    paths = {m["path"] for m in result["stale_entries"][0]["mismatches"]}
    assert "trajectories.f0_dist" in paths


def test_raises_when_current_index_unreadable(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(baseline_dir, {"a": (_make_ok_vector("a"), "ok")})

    with pytest.raises(BaselineFreshnessError):
        check_baseline_freshness(baseline_dir, current_dir)


def test_raises_when_current_has_no_entries(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(baseline_dir, {"a": (_make_ok_vector("a"), "ok")})
    _write_result_dir(current_dir, {})

    with pytest.raises(BaselineFreshnessError):
        check_baseline_freshness(baseline_dir, current_dir)


def test_report_mentions_stale_ids_metric_paths_and_the_fix_command(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(baseline_dir, {"violin": (_make_ok_vector("violin", msstft=0.1), "ok")})
    _write_result_dir(current_dir, {"violin": (_make_ok_vector("violin", msstft=0.9), "ok")})
    result = check_baseline_freshness(baseline_dir, current_dir)

    report = render_freshness_report(result)

    assert "violin" in report
    assert "overall.msstft" in report
    assert "python -m harness run-corpus" in report
    assert "corpus/baseline" in report


# --- CLI ---


def test_cli_check_baseline_freshness_exits_zero_when_fresh(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    vector = _make_ok_vector("a")
    _write_result_dir(baseline_dir, {"a": (vector, "ok")})
    _write_result_dir(current_dir, {"a": (vector, "ok")})

    exit_code = cli_main(
        [
            "check-baseline-freshness",
            "--baseline",
            str(baseline_dir),
            "--current",
            str(current_dir),
        ]
    )

    assert exit_code == 0


def test_cli_check_baseline_freshness_exits_one_when_stale(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    _write_result_dir(baseline_dir, {"a": (_make_ok_vector("a", msstft=0.1), "ok")})
    _write_result_dir(current_dir, {"a": (_make_ok_vector("a", msstft=0.9), "ok")})

    exit_code = cli_main(
        [
            "check-baseline-freshness",
            "--baseline",
            str(baseline_dir),
            "--current",
            str(current_dir),
        ]
    )

    assert exit_code == 1


def test_cli_check_baseline_freshness_exits_two_when_unreadable(tmp_path: Path) -> None:
    exit_code = cli_main(
        [
            "check-baseline-freshness",
            "--baseline",
            str(tmp_path / "does_not_exist"),
            "--current",
            str(tmp_path / "also_does_not_exist"),
        ]
    )

    assert exit_code == 2
