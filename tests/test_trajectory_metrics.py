"""harness.metrics の軌跡指標（トランジェント包絡相関 / f0軌跡距離）のテスト。

Issue #8 の完了条件を検証する：
- トランジェント包絡相関とf0軌跡距離が #3 のスキーマに沿って出力される
- 完全に同一のペアに対し、トランジェント包絡相関が 1.0 になる（許容誤差 1e-9）
- アタック位置が既知の時間だけずれたフィクスチャ（`e_attack_shift`）に対し、
  トランジェント包絡相関が同一ペア（`a_identical`）より小さい
- 基本周波数が既知の合成フィクスチャ（`f_f0_glide`：固定 / 既知区間でのグライド）に対し、
  推定f0が既知値に対して以下の許容誤差以内である（実測最大誤差に対し十分な余裕を取った値。
  実測値は本テストのコメントに明記する）：
    - 固定区間：絶対誤差 5.0 Hz 以内
    - グライド区間・前後の定常区間：絶対誤差 10.0 Hz 以内
- f0が存在しない入力（ノイズのみ・無音）に対して、f0軌跡距離が欠測（value=None かつ
  missing_reason 付き）として出力され、でたらめな値を返さない
- 使用したf0推定アルゴリズムの名称とバージョンが calc_conditions.estimation_algorithms に記録される
"""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
import pytest

from harness.fixture_gen import (
    F0_FIXED_HZ,
    F0_GLIDE_FROM_HZ,
    F0_GLIDE_TO_HZ,
    generate_all,
)
from harness.metrics import (
    F0_ALGORITHM_NAME,
    compute_metrics_vector,
    estimate_f0_contour,
    f0_trajectory_distance,
    transient_envelope_correlation,
)


def _pair_meta(meta: dict, name: str) -> dict:
    for p in meta["pairs"]:
        if p["name"] == name:
            return p
    raise AssertionError(f"pair {name} not found")


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("trajectory_fixtures")
    meta = generate_all(out_dir, seed=0)
    return out_dir, meta


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
    from harness.audio_io import read_wav, to_mono

    buf = to_mono(read_wav(path))
    return buf.data[:, 0], buf.sample_rate


# --- トランジェント包絡相関 ---


def test_identical_pair_has_transient_env_corr_of_one(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")
    target, _sr = _read_mono(out_dir / p["target"])
    candidate, _sr = _read_mono(out_dir / p["candidate"])

    result = transient_envelope_correlation(target, candidate)

    assert result["missing_reason"] is None
    assert result["value"] == pytest.approx(1.0, abs=1e-9)


def test_attack_shifted_pair_has_smaller_transient_env_corr_than_identical(
    fixtures,
) -> None:
    out_dir, meta = fixtures
    identical = _pair_meta(meta, "a_identical")
    shifted = _pair_meta(meta, "e_attack_shift")

    id_target, _sr = _read_mono(out_dir / identical["target"])
    id_candidate, _sr = _read_mono(out_dir / identical["candidate"])
    sh_target, _sr = _read_mono(out_dir / shifted["target"])
    sh_candidate, _sr = _read_mono(out_dir / shifted["candidate"])

    identical_corr = transient_envelope_correlation(id_target, id_candidate)["value"]
    shifted_corr = transient_envelope_correlation(sh_target, sh_candidate)["value"]

    assert shifted_corr < identical_corr


# --- f0軌跡（推定精度そのものの検証） ---


def test_fixed_f0_is_estimated_within_tolerance(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "f_f0_glide")
    target, sr = _read_mono(out_dir / p["target"])

    f0, voiced = estimate_f0_contour(target, sr)

    assert np.all(voiced), "固定f0の合成音は全フレーム有声であるべき"
    # 実測最大誤差 ~0.08Hz（本コードのプロトタイプ検証時点）に対し十分な余裕を持たせる。
    assert np.max(np.abs(f0[voiced] - F0_FIXED_HZ)) < 5.0


def test_glide_f0_is_estimated_within_tolerance(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "f_f0_glide")
    candidate, sr = _read_mono(out_dir / p["candidate"])

    known = p["known"]
    assert known["f0_glide_from_hz"] == F0_GLIDE_FROM_HZ
    assert known["f0_glide_to_hz"] == F0_GLIDE_TO_HZ
    glide_from_s, glide_to_s = known["glide_interval_s"]

    f0, voiced = estimate_f0_contour(candidate, sr)
    times = librosa.times_like(f0, sr=sr, hop_length=256)

    # 定常区間（グライド開始前 / 終了後）。境界付近20msは除外する。
    before = (times < glide_from_s - 0.02) & voiced
    after = (times > glide_to_s + 0.02) & voiced
    assert np.max(np.abs(f0[before] - F0_GLIDE_FROM_HZ)) < 10.0
    assert np.max(np.abs(f0[after] - F0_GLIDE_TO_HZ)) < 10.0

    # グライド区間（両端10msは推定の縁効果を避けるため除外する）
    during = (times >= glide_from_s + 0.01) & (times <= glide_to_s - 0.01) & voiced
    expected = F0_GLIDE_FROM_HZ + (F0_GLIDE_TO_HZ - F0_GLIDE_FROM_HZ) * (
        (times[during] - glide_from_s) / (glide_to_s - glide_from_s)
    )
    assert np.max(np.abs(f0[during] - expected)) < 10.0


def test_noise_only_input_has_no_voiced_frames() -> None:
    """f0が存在しない入力（ノイズのみ）は有声フレームを持たない（でたらめな値を返さない）。"""
    rng = np.random.default_rng(0)
    noise = rng.normal(size=8000)

    _f0, voiced = estimate_f0_contour(noise, sample_rate=16000)

    assert not np.any(voiced)


def test_f0_trajectory_distance_is_missing_for_noise_only_input() -> None:
    rng = np.random.default_rng(0)
    target_noise = rng.normal(size=8000)
    candidate_noise = rng.normal(size=8000) * 2.0

    result = f0_trajectory_distance(target_noise, candidate_noise, sample_rate=16000)

    assert result["value"] is None
    assert result["missing_reason"]


def test_f0_trajectory_distance_is_missing_for_silence_input() -> None:
    silence = np.zeros(8000)

    result = f0_trajectory_distance(silence, silence.copy(), sample_rate=16000)

    assert result["value"] is None
    assert result["missing_reason"]


def test_f0_trajectory_distance_is_near_zero_for_identical_fixed_tone(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")
    target, sr = _read_mono(out_dir / p["target"])
    candidate, sr = _read_mono(out_dir / p["candidate"])

    result = f0_trajectory_distance(target, candidate, sr)

    assert result["missing_reason"] is None
    assert result["value"] == pytest.approx(0.0, abs=1e-6)


# --- compute_metrics_vector との結合 ---


def test_compute_metrics_vector_reports_trajectory_metrics(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    vector = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])

    trajectories = vector["trajectories"]
    assert trajectories["transient_env_corr"]["value"] == pytest.approx(1.0, abs=1e-9)
    assert trajectories["f0_dist"]["value"] == pytest.approx(0.0, abs=1e-6)
    # フォルマント軌跡距離はP0-09のスコープであり、本Issueでは引き続き欠測
    assert trajectories["formant_dist"]["value"] is None
    assert trajectories["formant_dist"]["missing_reason"]


def test_calc_conditions_records_f0_estimation_algorithm(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")

    vector = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])

    algorithms = vector["calc_conditions"]["estimation_algorithms"]
    names = [a["name"] for a in algorithms]
    assert F0_ALGORITHM_NAME in names
    for algo in algorithms:
        assert algo["version"]
