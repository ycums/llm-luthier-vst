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

from harness.fixture_gen import GAIN_DB, ONSET_SHIFT_S, generate_all
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


def test_out_of_scope_sections_are_reported_as_missing(fixtures) -> None:
    """帯域別・軌跡指標は本Issue(#6)のスコープ外（P0-07/P0-08）であり、欠測として出力する。"""
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    vector = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])

    for band in vector["bands"]:
        assert band["error"]["value"] is None
        assert band["error"]["missing_reason"]

    for metric in vector["trajectories"].values():
        assert metric["value"] is None
        assert metric["missing_reason"]


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
            check=True,
        )
        return result.stdout

    first = _run()
    second = _run()

    assert first == second
