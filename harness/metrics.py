"""全体指標（P0-05 #5）・区間別指標（P0-06 #6）・帯域別指標（P0-07 #7）と軌跡指標の一部（P0-08 #8）の実装。

`docs/04-metrics.md` の「全体指標」「区間別指標」「帯域別指標」、および軌跡指標のうちトランジェント
包絡相関・f0軌跡距離を算出し、`docs/04-metrics.schema.json` に valid な指標ベクトル全体
（overall / segments / bands / trajectories / calc_conditions）を組み立てる。
区間別指標は全体指標と同じ3指標（msstft / mfcc / loudness_diff_db）を、区間境界で
切り出した部分波形に対して算出したものである。

帯域別指標（P0-07 #7）と区間別指標は本モジュールで実際に算出する。残るフォルマント軌跡距離（P0-09）
のみがスコープ外であり、欠測（`value: null` + `missing_reason`）として出力する。
estimation_algorithms に記録するf0推定アルゴリズムは軌跡指標（P0-08 #8）で使用する pYIN であり、
フォルマント推定（P0-09）は未使用のため記録しない（Q-011 参照）。

## 区間境界は外部入力である（Issue #6 完了条件）

区間境界（`attack_end_s` / `transition_end_s` / `sustain_end_s`）を実装に埋め込まない。
`DEFAULT_ATTACK_END_S`（0.02s = 20ms）だけが既定値を持つ。これは `docs/04-metrics.md` に
明記されているとおり**仮の値**であり、妥当性は未解決（`docs/06-open-questions.md` Q-009）。
遷移部・定常部・リリースの境界（`transition_end_s` / `sustain_end_s`）には既定値を置かない。
呼び出し側（設定またはマニフェストの注釈）から与えられなければ、該当する区間は欠測
（理由付き）として出力する。エラーで落とさない。これにより、境界注釈を持たない音源も
そのまま処理できる。

帯域端リスト（`band_edges_hz`）は設定データであり、既定値は `harness/band_edges_default.json`
に外出ししてある。この既定値は暫定であり、帯域分割方式（等間隔/メル/バーク）自体は
`docs/06-open-questions.md` の Q-004 が未解決である（本Issueは Q-004 を解決するものではない。
Q-004 解決に必要な観測を行うために方式を切り替え可能にするのがそのIssueの目的）。

## ラウドネス差の定義と符号

    loudness_diff_db = 20 * log10(rms(candidate) / rms(target))

正の値は candidate の方が target よりラウドネスが大きいことを意味する（target を基準にする）。
ここでのラウドネスはRMSベースの近似であり、知覚的ラウドネス（ITU-R BS.1770等の心理音響重み付け）
の実装は本Issueのスコープ外（docs/04-metrics.md に定義がない）。ゲイン差の切り分け用途には
RMSで十分である。RMSが厳密に0になる区間（例：アタック区間の境界がオンセットより手前にあり、
candidate側がまだ無音の場合）で `log10(0)` に発散しないよう、両辺に微小値を加えてから比を取る。

## マルチスケールスペクトル距離（msstft）

複数のFFTサイズ（既定 `DEFAULT_FFT_SIZES`、`docs/04-metrics.md` の例示値）それぞれで振幅
スペクトログラムを計算し、線形振幅の平均絶対誤差と対数振幅の平均絶対誤差の和をスケールごとに
求め、スケール間で平均する（Engel et al. 2020, DDSP のマルチスケールスペクトルロスに準拠した
設計。時間分解能と周波数分解能の両方を見るという `docs/04-metrics.md` の目的に対応する）。
区間別指標として短い部分波形に適用する場合、FFTサイズが区間長を超えることがあるが、
`librosa.stft` は既定でゼロパディングするためエラーにはならない（短い区間ほど周波数分解能は
実質的に低下する。これは区間別指標の限界として受け入れる）。

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
（帯域分割方式そのものの決定はQ-004、`docs/06-open-questions.md` 参照）。

## トランジェント包絡相関（transient_env_corr）

`librosa.feature.rms`（フレーム単位のRMS振幅）を振幅包絡として、target・candidate間の
ピアソン相関係数を返す（`docs/04-metrics.md` の目的「アタックの形が合っているか」）。
時間軸全体の包絡を使う（アタック区間だけに窓を切らない）。理由：フィクスチャ生成器
（`harness/fixture_gen.py`）が作る音は立ち上がりに減衰・フェードを持たない矩形状の
ゲイン変化であり、区間別指標のアタック窓（既定20ms、`DEFAULT_ATTACK_END_S`）に
限定すると、target側の窓内振幅がほぼ一定になり相関係数の分散項がゼロに近づいて数値的に
不安定になる（アタック位置がその窓幅を超えてずれるフィクスチャでは片方が窓内で無音になり
相関が定義できないケースすら生じる）。時間軸全体を使えば、倍音間のビート（うなり）に
由来する自然な包絡変動が両系列に残り、既知の遅延に対しても頑健に相関が計算できる
（`tests/test_trajectory_metrics.py` で実測を確認）。
包絡の分散がゼロ（無音・完全に一定の信号）の場合は相関が定義できないため欠測を返す。

## f0軌跡距離（f0_dist）

`librosa.pyin`（確率的YIN、Mauch & Dixon 2014）でフレームごとのf0（Hz）と有声フラグを推定し、
target・candidateの双方が有声なフレームに限定してf0の絶対誤差（Hz）を平均する。
単純な `librosa.yin` ではなく `pyin` を使う理由：`yin` は無声区間でも「それらしい」周波数を
常に返してしまい、`docs/04-metrics.md` の「f0が存在しない入力に対してでたらめな値を返さない」
という要求（Issue #8 完了条件）を満たせない。`pyin` の有声判定（ビタビ復号）を使うことで、
無音・非周期音（ノイズ）を「有声フレームなし」として区別できる。
target・candidateのどちらかに有声フレームが1つもない場合、または両者の有声区間が重ならない
場合は欠測を返す。
使用したアルゴリズム名・バージョン（`F0_ALGORITHM_NAME` / `librosa.__version__`）は
calc_conditions.estimation_algorithms に記録する（Issue #8 完了条件、
`docs/06-open-questions.md` Q-011 参照：pYIN以外の推定方式は今後の検討課題として残す）。
"""

from __future__ import annotations

import itertools
import json
import warnings
from collections.abc import Sequence
from pathlib import Path

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

#: トランジェント包絡相関の算出に使う振幅包絡（フレームRMS）のフレーム長・ホップ長。
DEFAULT_TRANSIENT_ENV_FRAME_LENGTH = 1024
DEFAULT_TRANSIENT_ENV_HOP_LENGTH = 256

#: f0軌跡距離の算出に使うpYINの探索範囲とフレーム設定。
#: fmin/fmaxはコーパスの実用的な音域（低音楽器〜高音域）を広めにカバーする値。
DEFAULT_F0_FMIN_HZ = 60.0
DEFAULT_F0_FMAX_HZ = 1000.0
DEFAULT_F0_FRAME_LENGTH = 2048
DEFAULT_F0_HOP_LENGTH = 256

#: f0推定に使うアルゴリズム名（calc_conditions.estimation_algorithms に記録する）。
#: 選定の背景・代替案は docs/06-open-questions.md Q-011 を参照。
F0_ALGORITHM_NAME = "pyin"

#: アタック区間の既定境界（秒）。ノートオンを0秒として [0, DEFAULT_ATTACK_END_S) をアタックとする。
#: docs/04-metrics.md に明記されているとおり**仮の値**（20ms）であり、妥当性は
#: docs/06-open-questions.md の Q-009 として未解決のまま。遷移部・定常部・リリースの境界には
#: これに類する既定値を置かない（呼び出し側が注釈として与えなければ欠測になる）。
DEFAULT_ATTACK_END_S: float = 0.02

#: RMSがちょうど0の区間（無音）でも log10 が発散しないようにするための下駄。
_LOUDNESS_RMS_EPS = 1e-12

#: 出力する指標ベクトルのスキーマバージョン（docs/04-metrics.schema.json に合わせる）。
SCHEMA_VERSION = "2.0.0"

_NOT_IMPLEMENTED_REASONS = {
    "formant_dist": "フォルマント軌跡距離は本モジュールのスコープ外（P0-09 で実装予定）",
}

#: 区間境界が注釈として与えられていない場合の欠測理由（segment名ごと）。
_SEGMENT_BOUNDARY_MISSING_REASONS = {
    "transition": "遷移部の区間境界（transition_end_s）が注釈として与えられていない",
    "sustain": "定常部の区間境界（transition_end_s / sustain_end_s）が注釈として与えられていない",
    "release": "リリースの区間境界（sustain_end_s）が注釈として与えられていない",
}


def _metric_value(value: float) -> dict:
    return {"value": float(value), "missing_reason": None}


def _missing(reason: str) -> dict:
    return {"value": None, "missing_reason": reason}


def loudness_diff_db(target: np.ndarray, candidate: np.ndarray) -> float:
    """RMSベースのラウドネス差（dB）を返す。

    `20*log10(rms(candidate)/rms(target))`。正なら candidate の方が大きい
    （モジュールdocstring「ラウドネス差の定義と符号」参照）。RMSが厳密に0になる場合
    （区間別指標で、注目区間がまだ無音であるケース等）に log10(0) へ発散しないよう、
    両辺に `_LOUDNESS_RMS_EPS` を加えてから比を取る。
    """
    rms_target = float(np.sqrt(np.mean(np.square(target))))
    rms_candidate = float(np.sqrt(np.mean(np.square(candidate))))
    return 20.0 * float(
        np.log10(
            (rms_candidate + _LOUDNESS_RMS_EPS) / (rms_target + _LOUDNESS_RMS_EPS)
        )
    )


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

    区間別指標として短い部分波形を渡す場合、`fft_size` が波形長を超えることがある
    （モジュールdocstring参照）。`librosa` がそれを警告するが、ゼロパディングで処理は継続する
    ため、既知の想定内動作としてここでは黙らせる。
    """
    scale_distances = []
    for fft_size in fft_sizes:
        hop_length = max(1, fft_size // 4)
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message=r"n_fft=\d+ is too large for input signal.*"
            )
            mag_target = np.abs(
                librosa.stft(target, n_fft=fft_size, hop_length=hop_length)
            )
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
    for i, (lo, hi) in enumerate(itertools.pairwise(edges)):
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
    """MFCC系列のフレームごとユークリッド距離を、フレーム間で平均する。

    区間別指標として短い部分波形を渡す場合の `n_fft` 過大警告の扱いは
    `multiscale_spectral_distance` と同じ（モジュールdocstring参照）。
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message=r"n_fft=\d+ is too large for input signal.*"
        )
        mfcc_target = librosa.feature.mfcc(y=target, sr=sample_rate, n_mfcc=n_mfcc)
        mfcc_candidate = librosa.feature.mfcc(y=candidate, sr=sample_rate, n_mfcc=n_mfcc)
    n_frames = min(mfcc_target.shape[1], mfcc_candidate.shape[1])
    diff = mfcc_target[:, :n_frames] - mfcc_candidate[:, :n_frames]
    frame_distances = np.linalg.norm(diff, axis=0)
    return float(np.mean(frame_distances))


def transient_envelope_correlation(
    target: np.ndarray,
    candidate: np.ndarray,
    frame_length: int = DEFAULT_TRANSIENT_ENV_FRAME_LENGTH,
    hop_length: int = DEFAULT_TRANSIENT_ENV_HOP_LENGTH,
) -> dict:
    """振幅包絡（フレームRMS）のピアソン相関係数を返す。

    設計の背景はモジュールdocstring「トランジェント包絡相関」を参照。
    包絡の分散がゼロ（無音・完全に一定の信号）の場合は欠測を返す。
    """
    env_target = librosa.feature.rms(
        y=target, frame_length=frame_length, hop_length=hop_length
    )[0]
    env_candidate = librosa.feature.rms(
        y=candidate, frame_length=frame_length, hop_length=hop_length
    )[0]
    n_frames = min(len(env_target), len(env_candidate))
    env_target = env_target[:n_frames]
    env_candidate = env_candidate[:n_frames]

    if n_frames < 2 or np.std(env_target) == 0.0 or np.std(env_candidate) == 0.0:
        return _missing(
            "振幅包絡の分散がゼロのため相関を算出できない（無音または完全に一定の信号）"
        )

    corr = float(np.corrcoef(env_target, env_candidate)[0, 1])
    return _metric_value(corr)


def estimate_f0_contour(
    signal: np.ndarray,
    sample_rate: int,
    fmin: float = DEFAULT_F0_FMIN_HZ,
    fmax: float = DEFAULT_F0_FMAX_HZ,
    frame_length: int = DEFAULT_F0_FRAME_LENGTH,
    hop_length: int = DEFAULT_F0_HOP_LENGTH,
) -> tuple[np.ndarray, np.ndarray]:
    """pYINでフレームごとのf0（Hz）と有声フラグを推定する。

    戻り値は `(f0, voiced)`。`voiced[i]` が `False` のフレームの `f0[i]` は
    無声区間の値であり、でたらめな値として扱わない（呼び出し側は `voiced` で
    フィルタすること）。単純なYINではなくpYINを使う理由はモジュールdocstring
    「f0軌跡距離」を参照。
    """
    f0, voiced, _voiced_prob = librosa.pyin(
        signal,
        fmin=fmin,
        fmax=fmax,
        sr=sample_rate,
        frame_length=frame_length,
        hop_length=hop_length,
    )
    return f0, voiced


def f0_trajectory_distance(
    target: np.ndarray,
    candidate: np.ndarray,
    sample_rate: int,
    fmin: float = DEFAULT_F0_FMIN_HZ,
    fmax: float = DEFAULT_F0_FMAX_HZ,
    frame_length: int = DEFAULT_F0_FRAME_LENGTH,
    hop_length: int = DEFAULT_F0_HOP_LENGTH,
) -> dict:
    """f0軌跡の平均絶対誤差（Hz）。target・candidateの両方が有声なフレームのみで平均する。

    どちらかに有声フレームが1つもない場合、または有声区間が重ならない場合は欠測を返す
    （でたらめな値を返さないため。Issue #8 完了条件）。
    """
    f0_target, voiced_target = estimate_f0_contour(
        target, sample_rate, fmin, fmax, frame_length, hop_length
    )
    f0_candidate, voiced_candidate = estimate_f0_contour(
        candidate, sample_rate, fmin, fmax, frame_length, hop_length
    )
    n_frames = min(len(f0_target), len(f0_candidate))
    f0_target = f0_target[:n_frames]
    voiced_target = voiced_target[:n_frames]
    f0_candidate = f0_candidate[:n_frames]
    voiced_candidate = voiced_candidate[:n_frames]

    if not np.any(voiced_target):
        return _missing("target に有声フレームが存在しないためf0軌跡を算出できない")
    if not np.any(voiced_candidate):
        return _missing("candidate に有声フレームが存在しないためf0軌跡を算出できない")

    both_voiced = voiced_target & voiced_candidate
    if not np.any(both_voiced):
        return _missing(
            "target と candidate の有声区間が重ならないためf0軌跡を算出できない"
        )

    diff = np.abs(f0_target[both_voiced] - f0_candidate[both_voiced])
    return _metric_value(float(np.mean(diff)))


def compute_trajectory_metrics(
    target: np.ndarray,
    candidate: np.ndarray,
    sample_rate: int,
) -> dict:
    """軌跡指標3件を組み立てる。フォルマント軌跡距離はP0-09のスコープであり欠測とする。"""
    return {
        "transient_env_corr": transient_envelope_correlation(target, candidate),
        "f0_dist": f0_trajectory_distance(target, candidate, sample_rate),
        "formant_dist": _missing(_NOT_IMPLEMENTED_REASONS["formant_dist"]),
    }


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


def _segment_slice_bounds(
    attack_end_s: float,
    transition_end_s: float | None,
    sustain_end_s: float | None,
) -> dict[str, tuple[float, float | None] | None]:
    """各区間の (start_s, end_s) を返す。`end_s=None` は「音源末尾まで」を意味する。

    区間境界（`transition_end_s` / `sustain_end_s`）が注釈として与えられていない区間は
    `None`（呼び出し側で欠測として扱う）。アタックは `attack_end_s` に常に既定値があるため
    常に算出される（`DEFAULT_ATTACK_END_S` 参照）。
    """
    has_transition = transition_end_s is not None
    has_sustain = has_transition and sustain_end_s is not None
    return {
        "attack": (0.0, attack_end_s),
        "transition": (attack_end_s, transition_end_s) if has_transition else None,
        "sustain": (transition_end_s, sustain_end_s) if has_sustain else None,
        "release": (sustain_end_s, None) if sustain_end_s is not None else None,
    }


def _slice_by_seconds(
    array: np.ndarray, sample_rate: int, start_s: float, end_s: float | None
) -> np.ndarray:
    """時刻（秒）で `array` を切り出す。`end_s=None` は配列末尾までを意味する。

    範囲は配列長にクリップする（境界が音源長を超えていても例外を投げない）。
    """
    start = max(0, round(start_s * sample_rate))
    end = len(array) if end_s is None else max(0, round(end_s * sample_rate))
    end = min(end, len(array))
    start = min(start, end)
    return array[start:end]


def compute_segment_metrics(
    target: np.ndarray,
    candidate: np.ndarray,
    sample_rate: int,
    attack_end_s: float = DEFAULT_ATTACK_END_S,
    transition_end_s: float | None = None,
    sustain_end_s: float | None = None,
    fft_sizes: Sequence[int] = DEFAULT_FFT_SIZES,
) -> dict:
    """区間別指標（attack/transition/sustain/release × 全体指標と同じ3指標）を算出する。

    境界が注釈として与えられていない区間（`transition_end_s` / `sustain_end_s` が `None`）は
    欠測として出力する（Issue #6 完了条件）。切り出した部分波形の長さが0になる場合
    （音源がその境界より短い等）も同様に欠測として出力し、例外は投げない。
    """
    bounds = _segment_slice_bounds(attack_end_s, transition_end_s, sustain_end_s)

    segments: dict[str, dict] = {}
    for name, bound in bounds.items():
        if bound is None:
            segments[name] = _missing_overall(_SEGMENT_BOUNDARY_MISSING_REASONS[name])
            continue

        start_s, end_s = bound
        target_segment = _slice_by_seconds(target, sample_rate, start_s, end_s)
        candidate_segment = _slice_by_seconds(candidate, sample_rate, start_s, end_s)
        n = min(len(target_segment), len(candidate_segment))
        if n == 0:
            segments[name] = _missing_overall(
                f"{name} 区間の波形長が0（音源がこの区間境界より短い）"
            )
            continue

        segments[name] = compute_overall_metrics(
            target_segment[:n], candidate_segment[:n], sample_rate, fft_sizes
        )

    return segments


def compute_metrics_vector(
    target_path: str | Path,
    candidate_path: str | Path,
    fft_sizes: Sequence[int] = DEFAULT_FFT_SIZES,
    attack_end_s: float = DEFAULT_ATTACK_END_S,
    transition_end_s: float | None = None,
    sustain_end_s: float | None = None,
    band_edges_hz: Sequence[float] = DEFAULT_BAND_EDGES_HZ,
) -> dict:
    """2つのWAVパスから、`docs/04-metrics.schema.json` に valid な指標ベクトルを組み立てる。

    全体指標（overall）・区間別指標（segments）・帯域別指標（bands）と、軌跡指標のうちトランジェント
    包絡相関・f0軌跡距離を実際に算出する（P0-05 #5 / P0-06 #6 / P0-07 #7 / P0-08 #8）。
    区間境界のうち `transition_end_s` / `sustain_end_s` は既定値を持たない外部入力であり、
    与えられなければ該当区間は欠測になる（`_segment_slice_bounds` 参照）。フォルマント軌跡距離
    （P0-09）は欠測として出力する。
    """
    target_path = Path(target_path)
    candidate_path = Path(candidate_path)

    target_buffer = _load_mono(target_path)
    candidate_buffer = _load_mono(candidate_path)
    require_same_sample_rate(target_buffer, candidate_buffer)

    target = target_buffer.data[:, 0]
    candidate = candidate_buffer.data[:, 0]
    sample_rate = target_buffer.sample_rate

    overall = compute_overall_metrics(target, candidate, sample_rate, fft_sizes)

    segments = compute_segment_metrics(
        target,
        candidate,
        sample_rate,
        attack_end_s=attack_end_s,
        transition_end_s=transition_end_s,
        sustain_end_s=sustain_end_s,
        fft_sizes=fft_sizes,
    )

    bands = compute_band_metrics(
        target, candidate, target_buffer.sample_rate, band_edges_hz, fft_sizes
    )

    trajectories = compute_trajectory_metrics(target, candidate, target_buffer.sample_rate)

    calc_conditions = {
        "schema_version": SCHEMA_VERSION,
        "fft_sizes": [int(size) for size in fft_sizes],
        "band_edges_hz": [float(edge) for edge in band_edges_hz],
        # 実際に使用した区間境界の実値を記録する（Issue #6 完了条件）。注釈が与えられて
        # いない transition_end_s / sustain_end_s は None（欠測）のまま記録し、値を捏造しない。
        "segment_boundaries_s": {
            "attack_end_s": float(attack_end_s),
            "transition_end_s": (
                float(transition_end_s) if transition_end_s is not None else None
            ),
            "sustain_end_s": (
                float(sustain_end_s) if sustain_end_s is not None else None
            ),
        },
        # f0推定は pYIN（軌跡指標 P0-08 #8）で使用。フォルマント推定（P0-09）は未使用。
        "estimation_algorithms": [
            {"name": F0_ALGORITHM_NAME, "version": librosa.__version__}
        ],
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
    "DEFAULT_ATTACK_END_S",
    "DEFAULT_BAND_EDGES_HZ",
    "DEFAULT_F0_FMAX_HZ",
    "DEFAULT_F0_FMIN_HZ",
    "DEFAULT_F0_FRAME_LENGTH",
    "DEFAULT_F0_HOP_LENGTH",
    "DEFAULT_FFT_SIZES",
    "DEFAULT_N_MFCC",
    "DEFAULT_TRANSIENT_ENV_FRAME_LENGTH",
    "DEFAULT_TRANSIENT_ENV_HOP_LENGTH",
    "F0_ALGORITHM_NAME",
    "SCHEMA_VERSION",
    "band_spectral_error",
    "compute_band_metrics",
    "compute_metrics_vector",
    "compute_overall_metrics",
    "compute_segment_metrics",
    "compute_trajectory_metrics",
    "estimate_f0_contour",
    "f0_trajectory_distance",
    "loudness_diff_db",
    "mfcc_distance",
    "multiscale_spectral_distance",
    "transient_envelope_correlation",
]
