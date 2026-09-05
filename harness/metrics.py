"""全体指標の実装（P0-05 #5）: マルチスケールスペクトル距離 / MFCC距離 / ラウドネス差。

`docs/04-metrics.md` の「全体指標」を算出し、`docs/04-metrics.schema.json` に valid な
指標ベクトル全体（overall / segments / bands / trajectories / calc_conditions）を組み立てる。

区間別（P0-06 #6）・帯域別（P0-07 #7）・軌跡（P0-08 #8）の各指標は本Issueのスコープ外であり、
それぞれ欠測（`value: null` + `missing_reason`）として出力する。calc_conditions に記録する
帯域端・区間境界の既定値は、それらの指標が実装されるまでの設定として外出ししてあるのみで、
値の妥当性はここでは問わない（`docs/04-metrics.md` 末尾「未確定」、Q-004 / Q-009 参照）。
estimation_algorithms も同じ理由で「未使用」を表す `none` を記録する（f0・フォルマント推定は
P0-08 / P0-09 のスコープ）。

## ラウドネス差の定義と符号

    loudness_diff_db = 20 * log10(rms(candidate) / rms(target))

正の値は candidate の方が target よりラウドネスが大きいことを意味する（target を基準にする）。
ここでのラウドネスはRMSベースの近似であり、知覚的ラウドネス（ITU-R BS.1770等の心理音響重み付け）
の実装は本Issueのスコープ外（docs/04-metrics.md に定義がない）。ゲイン差の切り分け用途には
RMSで十分である。

## マルチスケールスペクトル距離（msstft）

複数のFFTサイズ（既定 `DEFAULT_FFT_SIZES`、`docs/04-metrics.md` の例示値）それぞれで振幅
スペクトログラムを計算し、線形振幅の平均絶対誤差と対数振幅の平均絶対誤差の和をスケールごとに
求め、スケール間で平均する（Engel et al. 2020, DDSP のマルチスケールスペクトルロスに準拠した
設計。時間分解能と周波数分解能の両方を見るという `docs/04-metrics.md` の目的に対応する）。

## MFCC距離

`librosa.feature.mfcc` で算出したMFCC系列について、フレームごとのユークリッド距離を
フレーム間で平均する。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import librosa
import numpy as np

from harness.audio_io import AudioBuffer, read_wav, require_same_sample_rate, to_mono

# --- 設定（外出し。docs/04-metrics.md の calc_conditions に記録する） ---

#: マルチスケールスペクトル距離の算出に使うFFTサイズの集合。
#: docs/04-metrics.md の例示と同じ値を既定とする。
DEFAULT_FFT_SIZES: tuple[int, ...] = (512, 2048, 8192)

#: 対数振幅項の重み（線形振幅項の重みは1.0固定）。
_LOG_MAGNITUDE_WEIGHT = 1.0
#: log(0) を避けるための下駄（振幅スペクトルは非負なのでこれで十分）。
_LOG_EPS = 1e-8

#: MFCCの次数。
DEFAULT_N_MFCC = 13

#: 帯域別・区間別・軌跡の各指標（P0-06/07/08、本Issueのスコープ外）用の既定設定。
#: 値そのものの妥当性は問わない（docs/04-metrics.md「未確定」節、Q-004/Q-009参照）。
DEFAULT_BAND_EDGES_HZ: tuple[float, ...] = (0, 200, 800, 2000, 5000, 20000)
DEFAULT_SEGMENT_BOUNDARIES_S: dict = {
    "attack_end_s": 0.02,
    "transition_end_s": 0.08,
    "sustain_end_s": 0.45,
}

#: 出力する指標ベクトルのスキーマバージョン（docs/04-metrics.schema.json に合わせる）。
SCHEMA_VERSION = "1.0.0"

_NOT_IMPLEMENTED_REASONS = {
    "segments": "区間別指標は本Issue（P0-05 #5）のスコープ外（P0-06 #6 で実装予定）",
    "bands": "帯域別指標は本Issue（P0-05 #5）のスコープ外（P0-07 #7 で実装予定）",
    "trajectories": "軌跡指標は本Issue（P0-05 #5）のスコープ外（P0-08 #8 で実装予定）",
}


def _metric_value(value: float) -> dict:
    return {"value": float(value), "missing_reason": None}


def _missing(reason: str) -> dict:
    return {"value": None, "missing_reason": reason}


def loudness_diff_db(target: np.ndarray, candidate: np.ndarray) -> float:
    """RMSベースのラウドネス差（dB）を返す。

    `20*log10(rms(candidate)/rms(target))`。正なら candidate の方が大きい
    （モジュールdocstring「ラウドネス差の定義と符号」参照）。
    """
    rms_target = float(np.sqrt(np.mean(np.square(target))))
    rms_candidate = float(np.sqrt(np.mean(np.square(candidate))))
    return 20.0 * float(np.log10(rms_candidate / rms_target))


def multiscale_spectral_distance(
    target: np.ndarray,
    candidate: np.ndarray,
    fft_sizes: Sequence[int] = DEFAULT_FFT_SIZES,
) -> float:
    """複数のFFTサイズで振幅スペクトルの距離を測り、スケール間で平均する。

    各スケールでの距離 = 線形振幅の平均絶対誤差 + 対数振幅の平均絶対誤差。
    """
    scale_distances = []
    for fft_size in fft_sizes:
        hop_length = max(1, fft_size // 4)
        mag_target = np.abs(librosa.stft(target, n_fft=fft_size, hop_length=hop_length))
        mag_candidate = np.abs(
            librosa.stft(candidate, n_fft=fft_size, hop_length=hop_length)
        )
        n_frames = min(mag_target.shape[1], mag_candidate.shape[1])
        mag_target = mag_target[:, :n_frames]
        mag_candidate = mag_candidate[:, :n_frames]

        linear_term = float(np.mean(np.abs(mag_target - mag_candidate)))
        log_term = float(
            np.mean(
                np.abs(
                    np.log(mag_target + _LOG_EPS) - np.log(mag_candidate + _LOG_EPS)
                )
            )
        )
        scale_distances.append(linear_term + _LOG_MAGNITUDE_WEIGHT * log_term)

    return float(np.mean(scale_distances))


def mfcc_distance(
    target: np.ndarray,
    candidate: np.ndarray,
    sample_rate: int,
    n_mfcc: int = DEFAULT_N_MFCC,
) -> float:
    """MFCC系列のフレームごとユークリッド距離を、フレーム間で平均する。"""
    mfcc_target = librosa.feature.mfcc(y=target, sr=sample_rate, n_mfcc=n_mfcc)
    mfcc_candidate = librosa.feature.mfcc(y=candidate, sr=sample_rate, n_mfcc=n_mfcc)
    n_frames = min(mfcc_target.shape[1], mfcc_candidate.shape[1])
    diff = mfcc_target[:, :n_frames] - mfcc_candidate[:, :n_frames]
    frame_distances = np.linalg.norm(diff, axis=0)
    return float(np.mean(frame_distances))


def _load_mono(path: str | Path) -> AudioBuffer:
    return to_mono(read_wav(path))


def compute_overall_metrics(
    target: np.ndarray,
    candidate: np.ndarray,
    sample_rate: int,
    fft_sizes: Sequence[int] = DEFAULT_FFT_SIZES,
) -> dict:
    """全体指標（msstft / mfcc / loudness_diff_db）の3件を算出する。"""
    return {
        "msstft": _metric_value(
            multiscale_spectral_distance(target, candidate, fft_sizes)
        ),
        "mfcc": _metric_value(mfcc_distance(target, candidate, sample_rate)),
        "loudness_diff_db": _metric_value(loudness_diff_db(target, candidate)),
    }


def _missing_overall(reason: str) -> dict:
    return {
        "msstft": _missing(reason),
        "mfcc": _missing(reason),
        "loudness_diff_db": _missing(reason),
    }


def compute_metrics_vector(
    target_path: str | Path,
    candidate_path: str | Path,
    fft_sizes: Sequence[int] = DEFAULT_FFT_SIZES,
) -> dict:
    """2つのWAVパスから、`docs/04-metrics.schema.json` に valid な指標ベクトルを組み立てる。

    全体指標（overall）のみ本Issue（#5）で実際に算出する。segments / bands /
    trajectories は P0-06 / P0-07 / P0-08 のスコープであり、ここでは欠測として出力する。
    """
    target_path = Path(target_path)
    candidate_path = Path(candidate_path)

    target_buffer = _load_mono(target_path)
    candidate_buffer = _load_mono(candidate_path)
    require_same_sample_rate(target_buffer, candidate_buffer)

    target = target_buffer.data[:, 0]
    candidate = candidate_buffer.data[:, 0]

    overall = compute_overall_metrics(
        target, candidate, target_buffer.sample_rate, fft_sizes
    )

    segments = {
        segment: _missing_overall(_NOT_IMPLEMENTED_REASONS["segments"])
        for segment in ("attack", "transition", "sustain", "release")
    }

    band_edges = [float(edge) for edge in DEFAULT_BAND_EDGES_HZ]
    bands = [
        {
            "lo_hz": lo,
            "hi_hz": hi,
            "error": _missing(_NOT_IMPLEMENTED_REASONS["bands"]),
        }
        for lo, hi in zip(band_edges[:-1], band_edges[1:])
    ]

    trajectories = {
        name: _missing(_NOT_IMPLEMENTED_REASONS["trajectories"])
        for name in ("transient_env_corr", "f0_dist", "formant_dist")
    }

    calc_conditions = {
        "schema_version": SCHEMA_VERSION,
        "fft_sizes": [int(size) for size in fft_sizes],
        "band_edges_hz": band_edges,
        "segment_boundaries_s": dict(DEFAULT_SEGMENT_BOUNDARIES_S),
        # f0・フォルマント推定（P0-08/09）は本Issueで未使用のため "none" を記録する。
        # 決め打ちの推測ではなく「現時点で何も使っていない」という事実の記録。
        "estimation_algorithms": [{"name": "none", "version": "n/a"}],
    }

    return {
        "target": target_path.name,
        "overall": overall,
        "segments": segments,
        "bands": bands,
        "trajectories": trajectories,
        "calc_conditions": calc_conditions,
    }


__all__ = [
    "DEFAULT_FFT_SIZES",
    "DEFAULT_N_MFCC",
    "DEFAULT_BAND_EDGES_HZ",
    "DEFAULT_SEGMENT_BOUNDARIES_S",
    "SCHEMA_VERSION",
    "loudness_diff_db",
    "multiscale_spectral_distance",
    "mfcc_distance",
    "compute_overall_metrics",
    "compute_metrics_vector",
]
