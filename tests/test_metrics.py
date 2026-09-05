"""harness.metrics（全体指標: msstft / mfcc / loudness_diff_db）のテスト。

Issue #5 の完了条件を検証する：
- 2つのWAVパスから #3 のスキーマに valid な指標ベクトルJSONを組み立てられる
- 完全に同一のペアに対し、3指標すべてが0になる（許容誤差 1e-9）
- 既知のゲイン差ペアに対し、ラウドネス差がその値と一致する（許容誤差 1e-6）。
  符号の向き（candidate 基準）は harness/metrics.py のdocstringに明記されている
- 既知のローパス済みペアに対し、マルチスケールスペクトル距離が同一ペアより大きい
- 使用したFFTサイズ集合が calc_conditions.fft_sizes に記録される
- 同一入力に対して2回実行した出力JSONがビット単位で一致する
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from harness.fixture_gen import GAIN_DB, generate_all
from harness.metrics import (
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
    """区間別・帯域別指標は本Issue(#5)のスコープ外であり、欠測として出力する。

    軌跡指標のうちトランジェント包絡相関・f0軌跡距離はP0-08（#8）、フォルマント軌跡距離は
    P0-09（#9）で実装済みのため、ここでは検証しない（`tests/test_trajectory_metrics.py` /
    `tests/test_formant_metrics.py` を参照）。
    """
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    vector = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])

    for segment in vector["segments"].values():
        for metric in segment.values():
            assert metric["value"] is None
            assert metric["missing_reason"]

    for band in vector["bands"]:
        assert band["error"]["value"] is None
        assert band["error"]["missing_reason"]

    # フォルマント軌跡距離はP0-09で実装済み。欠測でないことは test_formant_metrics.py が検証する。


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
