"""全体指標（P0-05 #5）・帯域別指標（P0-07 #7）の実装。

`docs/04-metrics.md` の「全体指標」「帯域別指標」を算出し、`docs/04-metrics.schema.json` に
valid な指標ベクトル全体（overall / segments / bands / trajectories / calc_conditions）を
組み立てる。

区間別（P0-06 #6）・軌跡（P0-08 #8）の各指標は本モジュールのスコープ外であり、それぞれ欠測
（`value: null` + `missing_reason`）として出力する。calc_conditions に記録する区間境界の
既定値は、区間別指標が実装されるまでの設定として外出ししてあるのみで、値の妥当性はここでは
問わない（`docs/04-metrics.md` 末尾「未確定」、Q-009 参照）。estimation_algorithms も同じ理由で
「未使用」を表す `none` を記録する（f0・フォルマント推定は P0-08 / P0-09 のスコープ）。

帯域端リスト（`band_edges_hz`）は設定データであり、既定値は `harness/band_edges_default.json`
に外出ししてある。この既定値は暫定であり、帯域分割方式（等間隔/メル/バーク）自体は
`docs/06-open-questions.md` の Q-004 が未解決である（本Issueは Q-004 を解決するものではない。
Q-004 解決に必要な観測を行うために方式を切り替え可能にするのが本Issueの目的）。

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

## 帯域別誤差（bands）

`multiscale_spectral_distance` と同じ算出式（線形振幅の平均絶対誤差＋対数振幅の平均絶対誤差を
スケールごとに求め、スケール間で平均する）を、STFTの周波数ビンを `[lo_hz, hi_hz)` に絞った
うえで適用したものを帯域誤差とする（最後の帯域のみ上端 `hi_hz` を含む。隣接する帯域の境界に
一致するビンを二重に数えないため）。帯域を1つ（`[0, ナイキスト]`）に設定すると全ビンを含む
ため、`multiscale_spectral_distance` と数値的に一致する（完了条件）。

帯域端は Hz のリスト（設定データ）として与える。等間隔・メル・バークいずれの刻みで生成した
リストでも、本実装はビンをHzの範囲で選ぶだけで刻み方式そのものを一切知らない。そのため
分割方式の変更は設定側（呼び出し時に渡すリスト）だけで完結し、実装の変更を要さない
（帯域分割方式そのものの決定はQ-004、`docs/06-open-questions.md` 参照。未解決のまま本Issueに
着手してよいと判断されている）。
"""

from __future__ import annotations

import json
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

#: 既定の帯域端リスト（Hz、昇順）を設定ファイルから読み込む場所。
#: この値自体は暫定であり、帯域分割方式（等間隔/メル/バーク）はQ-004が未解決（同ファイル内に明記）。
_BAND_EDGES_CONFIG_PATH = Path(__file__).resolve().parent / "band_edges_default.json"


def _load_default_band_edges_hz() -> tuple[float, ...]:
    config = json.loads(_BAND_EDGES_CONFIG_PATH.read_text(encoding="utf-8"))
    return tuple(float(edge) for edge in config["band_edges_hz"])


DEFAULT_BAND_EDGES_HZ: tuple[float, ...] = _load_default_band_edges_hz()

#: 区間別指標（P0-06、本モジュールのスコープ外）用の既定設定。値そのものの妥当性は問わない
#: （docs/04-metrics.md「未確定」節、Q-009参照）。
DEFAULT_SEGMENT_BOUNDARIES_S: dict = {
    "attack_end_s": 0.02,
    "transition_end_s": 0.08,
    "sustain_end_s": 0.45,
}

#: 出力する指標ベクトルのスキーマバージョン（docs/04-metrics.schema.json に合わせる）。
SCHEMA_VERSION = "1.0.0"

_NOT_IMPLEMENTED_REASONS = {
    "segments": "区間別指標は本Issue（P0-07 #7）のスコープ外（P0-06 #6 で実装予定）",
    "trajectories": "軌跡指標は本Issue（P0-07 #7）のスコープ外（P0-08 #8 で実装予定）",
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


def _scale_spectral_distance(
    mag_target: np.ndarray, mag_candidate: np.ndarray
) -> float:
    """振幅スペクトル対（周波数ビン × フレーム）から1スケール分の距離を返す。

    距離 = 線形振幅の平均絶対誤差 + 対数振幅の平均絶対誤差。
    `multiscale_spectral_distance` と `band_spectral_error` の共通処理。
    """
    n_frames = min(mag_target.shape[1], mag_candidate.shape[1])
    mag_target = mag_target[:, :n_frames]
    mag_candidate = mag_candidate[:, :n_frames]

    linear_term = float(np.mean(np.abs(mag_target - mag_candidate)))
    log_term = float(
        np.mean(
            np.abs(np.log(mag_target + _LOG_EPS) - np.log(mag_candidate + _LOG_EPS))
        )
    )
    return linear_term + _LOG_MAGNITUDE_WEIGHT * log_term


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
        scale_distances.append(_scale_spectral_distance(mag_target, mag_candidate))

    return float(np.mean(scale_distances))


def band_spectral_error(
    target: np.ndarray,
    candidate: np.ndarray,
    sample_rate: int,
    lo_hz: float,
    hi_hz: float,
    fft_sizes: Sequence[int] = DEFAULT_FFT_SIZES,
    *,
    hi_inclusive: bool = True,
) -> float | None:
    """`[lo_hz, hi_hz)`（`hi_inclusive=True` なら `[lo_hz, hi_hz]`）に絞ったマルチスケール
    スペクトル距離を返す。

    算出式は `multiscale_spectral_distance` と同一（`_scale_spectral_distance`）で、周波数
    ビンを帯域に絞る点のみが異なる。帯域を `[0, ナイキスト]` 全体に取れば全ビンを含むため、
    `multiscale_spectral_distance` と数値的に一致する。

    与えられた全FFTサイズで対象帯域にビンが1本も含まれない場合は `None` を返す
    （呼び出し側が欠測として扱う）。
    """
    scale_distances = []
    for fft_size in fft_sizes:
        hop_length = max(1, fft_size // 4)
        freqs = librosa.fft_frequencies(sr=sample_rate, n_fft=fft_size)
        if hi_inclusive:
            band_mask = (freqs >= lo_hz) & (freqs <= hi_hz)
        else:
            band_mask = (freqs >= lo_hz) & (freqs < hi_hz)
        if not np.any(band_mask):
            continue

        mag_target = np.abs(librosa.stft(target, n_fft=fft_size, hop_length=hop_length))[
            band_mask
        ]
        mag_candidate = np.abs(
            librosa.stft(candidate, n_fft=fft_size, hop_length=hop_length)
        )[band_mask]
        scale_distances.append(_scale_spectral_distance(mag_target, mag_candidate))

    if not scale_distances:
        return None
    return float(np.mean(scale_distances))


def compute_band_metrics(
    target: np.ndarray,
    candidate: np.ndarray,
    sample_rate: int,
    band_edges_hz: Sequence[float] = DEFAULT_BAND_EDGES_HZ,
    fft_sizes: Sequence[int] = DEFAULT_FFT_SIZES,
) -> list[dict]:
    """帯域端リスト（設定データ）から、隣接ペアごとの帯域誤差配列を組み立てる。

    各要素は実際に使用した `lo_hz` / `hi_hz` を実値で含む（docs/04-metrics.md の例示形式）。
    最後の帯域のみ上端 `hi_hz` を含む（隣接帯域の境界に一致するビンの二重カウントを避ける）。
    """
    edges = list(band_edges_hz)
    last_index = len(edges) - 2
    bands = []
    for i, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
        error = band_spectral_error(
            target,
            candidate,
            sample_rate,
            lo,
            hi,
            fft_sizes,
            hi_inclusive=(i == last_index),
        )
        bands.append(
            {
                "lo_hz": float(lo),
                "hi_hz": float(hi),
                "error": (
                    _metric_value(error)
                    if error is not None
                    else _missing(f"[{lo}, {hi}] Hz 帯域にビンが1本も含まれないため算出不能")
                ),
            }
        )
    return bands


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
    band_edges_hz: Sequence[float] = DEFAULT_BAND_EDGES_HZ,
) -> dict:
    """2つのWAVパスから、`docs/04-metrics.schema.json` に valid な指標ベクトルを組み立てる。

    全体指標（overall）・帯域別指標（bands）を実際に算出する（P0-05 #5 / P0-07 #7）。
    segments / trajectories は P0-06 / P0-08 のスコープであり、ここでは欠測として出力する。
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

    bands = compute_band_metrics(
        target, candidate, target_buffer.sample_rate, band_edges_hz, fft_sizes
    )

    trajectories = {
        name: _missing(_NOT_IMPLEMENTED_REASONS["trajectories"])
        for name in ("transient_env_corr", "f0_dist", "formant_dist")
    }

    calc_conditions = {
        "schema_version": SCHEMA_VERSION,
        "fft_sizes": [int(size) for size in fft_sizes],
        "band_edges_hz": [float(edge) for edge in band_edges_hz],
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
    "band_spectral_error",
    "compute_overall_metrics",
    "compute_band_metrics",
    "compute_metrics_vector",
]
