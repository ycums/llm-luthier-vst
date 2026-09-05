"""全体指標（P0-05 #5）・区間別指標（P0-06 #6）・帯域別指標（P0-07 #7）と軌跡指標（P0-08 #8 / P0-09 #9）の実装。

`docs/04-metrics.md` の「全体指標」「区間別指標」「帯域別指標」「軌跡指標」（トランジェント包絡相関・
f0軌跡距離・フォルマント軌跡距離）を算出し、`docs/04-metrics.schema.json` に valid な指標ベクトル全体
（overall / segments / bands / trajectories / calc_conditions）を組み立てる。
区間別指標は全体指標と同じ3指標（msstft / mfcc / loudness_diff_db）を、区間境界で
切り出した部分波形に対して算出したものである。

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

estimation_algorithms に記録する推定アルゴリズムは、f0推定（P0-08 で使用する pYIN）と
フォルマント推定（P0-09 で使用する `stft-peak-tracking`）の2つ。フォルマント推定・追跡の
方式は暫定であり、その是非は docs/06-open-questions.md Q-010 の観測対象。

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

#: フォルマント推定・追跡の算出条件。方式の選択理由・暫定性は docs/06-open-questions.md Q-010 参照。
FORMANT_ALGORITHM_NAME = "stft-peak-tracking"
#: フォルマント追跡の対象数。
DEFAULT_N_FORMANTS = 3
#: フォルマント推定のフレームパラメータ。
DEFAULT_FORMANT_FRAME_LENGTH = 1600  # 100ms
DEFAULT_FORMANT_HOP_LENGTH = 320  # 20ms
DEFAULT_FORMANT_FFT_SIZE = 1024
DEFAULT_FORMANT_FMIN_HZ = 200.0
DEFAULT_FORMANT_FMAX_HZ = 5000.0
#: 対数振幅スペクトルの平滑化幅。
DEFAULT_FORMANT_SMOOTH_FRAMES = 9
#: フレーム間追跡の対応付け幅（tracking rule, Q-010）。
DEFAULT_FORMANT_MAX_TRACK_GAP_HZ = 300.0
#: 初期化のピーク選択で、既選択ピークとこれ以上離れていないピークは同一フォルマントとみなさない
#: （非最大値抑制, peak selection rule, Q-010）。
DEFAULT_FORMANT_MIN_SEP_HZ = 250.0

#: アタック区間の既定境界（秒）。ノートオンを0秒として [0, DEFAULT_ATTACK_END_S) をアタックとする。
#: docs/04-metrics.md に明記されているとおり**仮の値**（20ms）であり、妥当性は
#: docs/06-open-questions.md の Q-009 として未解決のまま。遷移部・定常部・リリースの境界には
#: これに類する既定値を置かない（呼び出し側が注釈として与えなければ欠測になる）。
DEFAULT_ATTACK_END_S: float = 0.02

#: RMSがちょうど0の区間（無音）でも log10 が発散しないようにするための下駄。
_LOUDNESS_RMS_EPS = 1e-12

#: 出力する指標ベクトルのスキーマバージョン（docs/04-metrics.schema.json に合わせる）。
#: segments/bands の実装（P0-06/P0-07）で 2.0.0（segment_boundaries の null 許容）、
#: フォルマント軌跡距離の実装（P0-09）で estimation_algorithms に方式パラメータを追加した。
SCHEMA_VERSION = "2.0.0"

#: 区間境界が注釈として与えられていない場合の欠測理由（segment名ごと）。
_SEGMENT_BOUNDARY_MISSING_REASONS = {
    "transition": "遷移部の区間境界（transition_end_s）が注釈として与えられていない",
    "sustain": "定常部の区間境界（transition_end_s / sustain_end_s）が注釈として与えられていない",
    "release": "リリースの区間境界（sustain_end_s）が注釈として与えられていない",
}

#: `segments` / `trajectories` のフィールド名（`build_missing_metrics_vector` が
#: 全指標欠測のベクトルを組み立てる際に使う。docs/04-metrics.schema.json の
#: `segment_metrics_set` / `trajectory_metrics` の必須プロパティと一致させること）。
_SEGMENT_NAMES = ("attack", "transition", "sustain", "release")
_TRAJECTORY_NAMES = ("transient_env_corr", "f0_dist", "formant_dist")


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


def _log_magnitude_spectrum_peaks(
    signal: np.ndarray,
    sample_rate: int,
    frame_length: int = DEFAULT_FORMANT_FRAME_LENGTH,
    hop_length: int = DEFAULT_FORMANT_HOP_LENGTH,
    smooth_frames: int = DEFAULT_FORMANT_SMOOTH_FRAMES,
    fft_size: int = DEFAULT_FORMANT_FFT_SIZE,
    fmin: float = DEFAULT_FORMANT_FMIN_HZ,
    fmax: float = DEFAULT_FORMANT_FMAX_HZ,
) -> tuple[list[list[float]], list[np.ndarray]]:
    """フレームごとの対数振幅スペクトルのローカル極大（ピーク）の周波数Hzを返す。

    各フレームをハン窓で掛けてFFTし、対数振幅を `smooth_frames` 幅で平滑化してから局所極大を
    拾う。帯域は `[fmin, fmax]`。戻り値は `(peak_freqs_per_frame, frame_freqs)`。空フレームは空リスト。
    """
    import scipy.ndimage
    from scipy.signal import find_peaks

    n = len(signal)
    frames_freqs = []
    peaks_per_frame: list[list[float]] = []
    fft_freqs = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)
    for start in range(0, max(n - frame_length + 1, 0), hop_length):
        frame = signal[start : start + frame_length]
        if len(frame) < frame_length:
            break
        win = np.hanning(frame_length)
        mag = np.abs(np.fft.rfft(frame * win, n=fft_size))
        log_mag = np.log(mag + 1e-12)
        if smooth_frames > 1:
            log_mag = scipy.ndimage.uniform_filter1d(log_mag, size=smooth_frames)
        band = (fft_freqs >= fmin) & (fft_freqs <= fmax)
        fb = fft_freqs[band]
        mb = log_mag[band]
        pk, _ = find_peaks(mb)
        peaks = fb[pk]
        frames_freqs.append(fft_freqs)
        peaks_per_frame.append(peaks.tolist() if len(peaks) else [])
    return peaks_per_frame, frames_freqs


def estimate_formant_tracks(
    signal: np.ndarray,
    sample_rate: int,
    n_formants: int = DEFAULT_N_FORMANTS,
    frame_length: int = DEFAULT_FORMANT_FRAME_LENGTH,
    hop_length: int = DEFAULT_FORMANT_HOP_LENGTH,
    max_track_gap_hz: float = DEFAULT_FORMANT_MAX_TRACK_GAP_HZ,
    smooth_frames: int = DEFAULT_FORMANT_SMOOTH_FRAMES,
    fft_size: int = DEFAULT_FORMANT_FFT_SIZE,
    fmin: float = DEFAULT_FORMANT_FMIN_HZ,
    fmax: float = DEFAULT_FORMANT_FMAX_HZ,
) -> np.ndarray:
    """フレームごとのスペクトルピークから、`n_formants` 本のフォルマント軌跡を追跡する。

    戻り値は `(n_formants, n_frames)` のfloat配列。未追跡（続きが見つからない）は NaN。

    対応付け規則（docs/06-open-questions.md Q-010）：初期化は最初のフレームのピーク群を
    振幅の大きい順に `n_formants` 本選び昇順に並べる。以降の各フレームは、前フレームの追跡値から
    `max_track_gap_hz` 以内の最も近いピークへ継続する（1ピーク1追跡の貪欲割当）。範囲内に候補が
    ないフレームは NaN とする。安定性（決定論）を優先する素朴な追跡であり、代替規則は Q-010 を
    待って比較する。
    """
    peaks_per_frame, _freqs = _log_magnitude_spectrum_peaks(
        signal,
        sample_rate=sample_rate,
        frame_length=frame_length,
        hop_length=hop_length,
        smooth_frames=smooth_frames,
        fft_size=fft_size,
        fmin=fmin,
        fmax=fmax,
    )
    n_frames = len(peaks_per_frame)
    if n_frames == 0:
        return np.full((n_formants, 0), np.nan)

    tracks = np.full((n_formants, n_frames), np.nan)
    prev_freqs = None

    for fi in range(n_frames):
        pk = peaks_per_frame[fi]
        if not pk:
            prev_freqs = None
            continue
        cand = np.sort(np.array(pk, dtype=np.float64))
        if prev_freqs is None or not np.isfinite(prev_freqs).any():
            # 初期化（あるいは途切れ後の再初期化）
            use = _initial_formants(signal, sample_rate, frame_length, hop_length,
                                    fi, n_formants, smooth_frames, fft_size, fmin, fmax)
            prev_freqs = use.copy() if len(use) == n_formants else None
            if prev_freqs is not None:
                tracks[:, fi] = prev_freqs
        else:
            # 追跡：前フレームの各値から max_track_gap_hz 以内の最も近い候補へ（貪欲、1ピーク1追跡）
            assigned = np.full(prev_freqs.shape, np.nan)
            used = set()
            for i, pv in enumerate(prev_freqs):
                if np.isnan(pv):
                    continue
                best_j, best_d = None, max_track_gap_hz
                for j, cj in enumerate(cand):
                    if j in used:
                        continue
                    d = abs(cj - pv)
                    if d <= best_d:
                        best_d, best_j = d, j
                if best_j is not None:
                    assigned[i] = cand[best_j]
                    used.add(best_j)
            tracks[:, fi] = assigned
            prev_freqs = assigned.copy()
    return tracks


def _initial_formants(
    signal: np.ndarray,
    sample_rate: int,
    frame_length: int,
    hop_length: int,
    frame_index: int,
    n_formants: int,
    smooth_frames: int,
    fft_size: int,
    fmin: float,
    fmax: float,
    min_sep_hz: float = DEFAULT_FORMANT_MIN_SEP_HZ,
) -> np.ndarray:
    """`frame_index` フレームのスペクトルピークから n_formants 本を選び昇順に返す。

    Peak selection rule (Q-010)：候補を振幅の大きい順に走査し、**選択済みピークから
    `min_sep_hz` 以上離れたもの**だけを選ぶ（非最大値抑制）。足りなければ空を返す（欠測扱い）。
    """
    import scipy.ndimage
    from scipy.signal import find_peaks

    start = frame_index * hop_length
    frame = signal[start : start + frame_length]
    win = np.hanning(frame_length)
    mag = np.abs(np.fft.rfft(frame * win, n=fft_size))
    log_mag = np.log(mag + 1e-12)
    if smooth_frames > 1:
        log_mag = scipy.ndimage.uniform_filter1d(log_mag, size=smooth_frames)
    freqs = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)
    band = (freqs >= fmin) & (freqs <= fmax)
    fb = freqs[band]
    mb = log_mag[band]
    pk, _ = find_peaks(mb)
    if len(pk) == 0:
        return np.array([])

    # 振幅の大きい順（peak selection rule）
    order = pk[np.argsort(mb[pk])[::-1]]
    selected = []
    for i in order:
        fi = fb[i]
        if all(abs(fi - s) >= min_sep_hz for s in selected):
            selected.append(fi)
        if len(selected) >= n_formants:
            break
    return np.sort(np.array(selected))


def formant_trajectory_distance(
    target: np.ndarray,
    candidate: np.ndarray,
    sample_rate: int,
    n_formants: int = DEFAULT_N_FORMANTS,
    frame_length: int = DEFAULT_FORMANT_FRAME_LENGTH,
    hop_length: int = DEFAULT_FORMANT_HOP_LENGTH,
    max_track_gap_hz: float = DEFAULT_FORMANT_MAX_TRACK_GAP_HZ,
    smooth_frames: int = DEFAULT_FORMANT_SMOOTH_FRAMES,
    fft_size: int = DEFAULT_FORMANT_FFT_SIZE,
    fmin: float = DEFAULT_FORMANT_FMIN_HZ,
    fmax: float = DEFAULT_FORMANT_FMAX_HZ,
) -> dict:
    """フォルマント軌跡の平均絶対誤差（Hz）。両方が追跡できたフレーム（非NaN）のヘルツ差を平均する。

    対応付け規則（Q-010）のとおり、各フォルマントは周波数近接でフレーム間継続される。
    比較できたフレームが1つもない場合は欠測を返す。
    """
    tracks_t = estimate_formant_tracks(
        target, sample_rate, frame_length=frame_length, hop_length=hop_length,
        n_formants=n_formants, max_track_gap_hz=max_track_gap_hz,
        smooth_frames=smooth_frames, fft_size=fft_size, fmin=fmin, fmax=fmax,
    )
    tracks_c = estimate_formant_tracks(
        candidate, sample_rate, frame_length=frame_length, hop_length=hop_length,
        n_formants=n_formants, max_track_gap_hz=max_track_gap_hz,
        smooth_frames=smooth_frames, fft_size=fft_size, fmin=fmin, fmax=fmax,
    )
    n_frames = min(tracks_t.shape[1], tracks_c.shape[1])
    t = tracks_t[:, :n_frames]
    c = tracks_c[:, :n_frames]
    both = np.isfinite(t) & np.isfinite(c)
    if not np.any(both):
        return _missing(
            "target と candidate でフォルマントを追跡できたフレームが重ならないため算出不能"
        )
    diff = np.abs(t - c)[both]
    return _metric_value(float(np.mean(diff)))


def compute_trajectory_metrics(
    target: np.ndarray,
    candidate: np.ndarray,
    sample_rate: int,
) -> dict:
    """軌跡指標3件を組み立てる。フォルマント軌跡距離（P0-09）も算出する。"""
    return {
        "transient_env_corr": transient_envelope_correlation(target, candidate),
        "f0_dist": f0_trajectory_distance(target, candidate, sample_rate),
        "formant_dist": formant_trajectory_distance(target, candidate, sample_rate),
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


def _build_calc_conditions(
    fft_sizes: Sequence[int],
    attack_end_s: float,
    transition_end_s: float | None,
    sustain_end_s: float | None,
    band_edges_hz: Sequence[float],
) -> dict:
    """`calc_conditions` セクションを組み立てる。音声データそのものには依存しない
    （設定値のみから決まる）ため、実測（`compute_metrics_vector`）と欠測専用の
    ベクトル（`build_missing_metrics_vector`）の両方から共通に呼べる。
    """
    return {
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
        # f0推定（pYIN）とフォルマント推定（STFTピーク追跡）を使用。
        "estimation_algorithms": [
            {"name": F0_ALGORITHM_NAME, "version": librosa.__version__},
            {
                "name": FORMANT_ALGORITHM_NAME,
                "version": "1",
                "n_formants": DEFAULT_N_FORMANTS,
                "frame_length": DEFAULT_FORMANT_FRAME_LENGTH,
                "hop_length": DEFAULT_FORMANT_HOP_LENGTH,
                "fft_size": DEFAULT_FORMANT_FFT_SIZE,
                "max_track_gap_hz": DEFAULT_FORMANT_MAX_TRACK_GAP_HZ,
            },
        ],
    }


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

    全体指標（overall）・区間別指標（segments）・帯域別指標（bands）と、軌跡指標
    （トランジェント包絡相関・f0軌跡距離・フォルマント軌跡距離、P0-08/P0-09）を実際に算出する
    （P0-05 #5 / P0-06 #6 / P0-07 #7 / P0-08 #8 / P0-09 #9）。
    区間境界のうち `transition_end_s` / `sustain_end_s` は既定値を持たない外部入力であり、
    与えられなければ該当区間は欠測になる（`_segment_slice_bounds` 参照）。
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

    calc_conditions = _build_calc_conditions(
        fft_sizes, attack_end_s, transition_end_s, sustain_end_s, band_edges_hz
    )

    return {
        "target": target_path.name,
        "overall": overall,
        "segments": segments,
        "bands": bands,
        "trajectories": trajectories,
        "calc_conditions": calc_conditions,
    }


def build_missing_metrics_vector(
    target_name: str,
    reason: str,
    fft_sizes: Sequence[int] = DEFAULT_FFT_SIZES,
    attack_end_s: float = DEFAULT_ATTACK_END_S,
    transition_end_s: float | None = None,
    sustain_end_s: float | None = None,
    band_edges_hz: Sequence[float] = DEFAULT_BAND_EDGES_HZ,
) -> dict:
    """音声を1サンプルも読まずに、全指標が欠測の指標ベクトルを組み立てる（P0-11 #11）。

    コーパス実行ランナが、音源が手元に存在しない（欠測）場合や算出そのものが失敗した
    場合に使う。`docs/04-metrics.schema.json` は個々の指標単位の欠測しか表現しないため、
    音源1件が丸ごと欠測であることを、全指標に同じ `reason` を詰めることで表現する。
    """
    overall = _missing_overall(reason)
    segments = {name: _missing_overall(reason) for name in _SEGMENT_NAMES}
    bands = [
        {"lo_hz": float(lo), "hi_hz": float(hi), "error": _missing(reason)}
        for lo, hi in itertools.pairwise(band_edges_hz)
    ]
    trajectories = {name: _missing(reason) for name in _TRAJECTORY_NAMES}
    calc_conditions = _build_calc_conditions(
        fft_sizes, attack_end_s, transition_end_s, sustain_end_s, band_edges_hz
    )

    return {
        "target": target_name,
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
    "DEFAULT_FORMANT_FFT_SIZE",
    "DEFAULT_FORMANT_FMAX_HZ",
    "DEFAULT_FORMANT_FMIN_HZ",
    "DEFAULT_FORMANT_FRAME_LENGTH",
    "DEFAULT_FORMANT_HOP_LENGTH",
    "DEFAULT_FORMANT_MAX_TRACK_GAP_HZ",
    "DEFAULT_FORMANT_SMOOTH_FRAMES",
    "DEFAULT_N_FORMANTS",
    "DEFAULT_N_MFCC",
    "DEFAULT_TRANSIENT_ENV_FRAME_LENGTH",
    "DEFAULT_TRANSIENT_ENV_HOP_LENGTH",
    "F0_ALGORITHM_NAME",
    "FORMANT_ALGORITHM_NAME",
    "SCHEMA_VERSION",
    "band_spectral_error",
    "build_missing_metrics_vector",
    "compute_band_metrics",
    "compute_metrics_vector",
    "compute_overall_metrics",
    "compute_segment_metrics",
    "compute_trajectory_metrics",
    "estimate_f0_contour",
    "estimate_formant_tracks",
    "f0_trajectory_distance",
    "formant_trajectory_distance",
    "loudness_diff_db",
    "mfcc_distance",
    "multiscale_spectral_distance",
    "transient_envelope_correlation",
]
