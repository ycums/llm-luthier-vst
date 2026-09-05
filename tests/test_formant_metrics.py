"""harness.metrics のフォルマント軌跡距離（P0-09）のテスト。

Issue #9 の完了条件を検証する：
- 共振周波数が既知の合成フィクスチャ（`g_formant_shift`、既知の周波数・帯域幅・ゲイン）が
  #4 の生成器に追加され、その共振周波数が実際に再現される（推定誤差が許容値以内）
- 共振周波数が既知の位置で移動するフィクスチャ（`h_formant_glide`、第2フォルマントが既知区間で
  1500→1800Hz にグライド）に対し、フレーム間の対応付け規則（Q-010：周波数近接で継続）どおりの
  追跡結果になることを確認する
- フォルマント軌跡距離（formant_dist）が #3 のスキーマに沿って出力され、同一ペアで 0 に、
  既知のフォルマント差を持つペアで正の値になる
- 使用したフォルマント推定方式（名前とパラメータ）が calc_conditions.estimation_algorithms に記録される
- 同一入力に対して2回実行した出力が完全一致する（決定論）
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from harness.fixture_gen import (
    FORMANT_GLIDE_FROM_HZ,
    FORMANT_GLIDE_TO_HZ,
    FORMANT_SHIFT_HZ,
    generate_all,
)
from harness.metrics import (
    DEFAULT_FORMANT_FRAME_LENGTH,
    DEFAULT_FORMANT_HOP_LENGTH,
    DEFAULT_N_FORMANTS,
    FORMANT_ALGORITHM_NAME,
    compute_metrics_vector,
    estimate_formant_tracks,
    formant_trajectory_distance,
)


def _pair_meta(meta: dict, name: str) -> dict:
    for p in meta["pairs"]:
        if p["name"] == name:
            return p
    raise AssertionError(f"pair {name} not found")


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("formant_fixtures")
    meta = generate_all(out_dir, seed=0)
    return out_dir, meta


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
    from harness.audio_io import read_wav, to_mono

    buf = to_mono(read_wav(path))
    return buf.data[:, 0], buf.sample_rate


def test_fixture_generates_known_resonance_pair(fixtures) -> None:
    """`g_formant_shift` ペアが既知のフォルマント（周波数・帯域幅・ゲイン）を持つ。"""
    _out_dir, meta = fixtures
    p = _pair_meta(meta, "g_formant_shift")

    known = p["known"]
    target_freqs = known["formant_freqs_target_hz"]
    candidate_freqs = known["formant_freqs_candidate_hz"]

    assert len(target_freqs) == DEFAULT_N_FORMANTS
    # 第2フォルマントのみが既知の量（FORMANT_SHIFT_HZ）だけずれる
    assert candidate_freqs[1] - target_freqs[1] == FORMANT_SHIFT_HZ
    assert len(known["formant_bw_hz"]) == DEFAULT_N_FORMANTS
    assert len(known["formant_gain"]) == DEFAULT_N_FORMANTS


def test_estimated_resonance_freqs_within_tolerance(fixtures) -> None:
    """既知の共振周波数が推定誤差の許容値以内で再現される。

    実測：全シードで最大誤差 ~4.5Hz（スパイクで確認）。余裕を持つ許容値 30Hz を用いる。
    """
    out_dir, meta = fixtures
    p = _pair_meta(meta, "g_formant_shift")
    target, sr = _read_mono(out_dir / p["target"])
    candidate, sr = _read_mono(out_dir / p["candidate"])

    known = p["known"]
    TOL = 15.0

    for signal, expected in (
        (target, known["formant_freqs_target_hz"]),
        (candidate, known["formant_freqs_candidate_hz"]),
    ):
        tracks = estimate_formant_tracks(signal, sr)
        means = np.nanmean(tracks, axis=1)
        err = np.abs(means - np.array(expected))
        assert np.all(err < TOL), f"expected {expected}, got {means}, err {err}"


def test_glide_tracking_follows_known_trajectory_rule(fixtures) -> None:
    """`h_formant_glide` の第2フォルマントが、対応付け規則どおり既知の軌跡を追う。

    Q-010 の規則（周波数近接で最も近いピークを同一フォルマントとみなす）により、グライド
    区間外（定常部）では 1500 / 1800、区間内では線形補間値に追従するはずである。
    フレーム窓（100ms）が境界を跨ぐ縁効果を避けるため、窓が区間内に完全に収まるフレームのみ
    比較する。
    """
    out_dir, meta = fixtures
    p = _pair_meta(meta, "h_formant_glide")
    candidate, sr = _read_mono(out_dir / p["candidate"])
    track = estimate_formant_tracks(candidate, sr)
    f2 = track[1]

    n = f2.shape[0]
    times = np.arange(n) * DEFAULT_FORMANT_HOP_LENGTH / sr
    glide_from_s, glide_to_s = p["known"]["glide_interval_s"]
    frame_len_s = DEFAULT_FORMANT_FRAME_LENGTH / sr

    # 窓全体（[t, t+frame]）がグライド区間外 / 内にあるフレーム
    fully_before = (times + frame_len_s) <= glide_from_s
    fully_after = times >= glide_to_s

    assert np.all(f2[fully_before] > 0), "定常部フレームが欠測になってはならない"

    # 定常部は 1500 / 1800 に近い
    assert np.max(np.abs(f2[fully_before] - FORMANT_GLIDE_FROM_HZ)) < 15.0
    assert np.max(np.abs(f2[fully_after] - FORMANT_GLIDE_TO_HZ)) < 15.0

    # グライド中は「低→高」へ単調に上昇し、1500 より大きく 1800 より小さい区間を経る。
    # （100ms の分析窓が瞬時周波数に跨がるため、窓内で瞬間的な線形補間値と厳密一致はしない。
    #   対応付け規則が保証するのは「同一フォルマントが周波数近接で継続し、軌跡の向きに追従する」こと。）
    during = (times >= glide_from_s) & (times < glide_to_s)
    f2_during = f2[during].astype(float)
    assert np.all(np.diff(f2_during[np.isfinite(f2_during)]) > -30.0), (
        "追跡が既知のグライド（上昇）に逆行してはならない"
    )
    assert np.mean(f2_during) > FORMANT_GLIDE_FROM_HZ
    assert np.max(f2_during) < FORMANT_GLIDE_TO_HZ


def test_formant_dist_is_zero_for_identical_pair(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "a_identical")
    target, sr = _read_mono(out_dir / p["target"])
    candidate, sr = _read_mono(out_dir / p["candidate"])

    result = formant_trajectory_distance(target, candidate, sr)

    assert result["missing_reason"] is None
    assert result["value"] == pytest.approx(0.0, abs=1e-6)


def test_formant_dist_is_positive_for_known_formant_shift(fixtures) -> None:
    """既知のフォルマント差（第2フォルマントが 100Hz 上昇）に対して正の距離が出る。

    第2フォルマントのみがずれるため距離は 0 より大きく、かつ既知の差以下のオーダーである。
    """
    out_dir, meta = fixtures
    p = _pair_meta(meta, "g_formant_shift")
    target, sr = _read_mono(out_dir / p["target"])
    candidate, sr = _read_mono(out_dir / p["candidate"])

    result = formant_trajectory_distance(target, candidate, sr)

    assert result["missing_reason"] is None
    # 第1・第3フォルマントは一致、第2のみ 100Hz ずれる ⇒ 平均は十分に正
    assert result["value"] > 5.0
    assert result["value"] < FORMANT_SHIFT_HZ


def test_formant_dist_is_missing_when_no_common_track(fixtures) -> None:
    """無音入力は追跡可能なピークを持たず、欠測になる（でたらめな値を返さない）。"""
    silence = np.zeros(8000)

    result = formant_trajectory_distance(silence, silence.copy(), sample_rate=16000)

    assert result["value"] is None
    assert result["missing_reason"]


def test_compute_metrics_vector_records_formant_algorithm_and_value(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "g_formant_shift")

    vector = compute_metrics_vector(out_dir / p["target"], out_dir / p["candidate"])

    trajectories = vector["trajectories"]
    assert trajectories["formant_dist"]["value"] is not None

    algorithms = vector["calc_conditions"]["estimation_algorithms"]
    formant_algo = next(a for a in algorithms if a["name"] == FORMANT_ALGORITHM_NAME)
    assert formant_algo["n_formants"] == DEFAULT_N_FORMANTS
    assert formant_algo["max_track_gap_hz"] > 0


def test_formant_trajectory_distance_is_deterministic(fixtures) -> None:
    out_dir, meta = fixtures
    p = _pair_meta(meta, "g_formant_shift")
    target, sr = _read_mono(out_dir / p["target"])
    candidate, sr = _read_mono(out_dir / p["candidate"])

    v1 = formant_trajectory_distance(target, candidate, sr)
    v2 = formant_trajectory_distance(target, candidate, sr)

    assert v1 == v2