"""コーパスの倍音構造観測（P1-02 #47）。

Q-001（`docs/06-open-questions.md`、Harmonic層を加算合成とウェーブテーブルのどちらにするか）
は「決めるために必要な観測：コーパスの倍音構造の多様性。倍音が時間的に大きく動く音が多いなら
加算が有利」と定める。本モジュールはこの観測のみを行い、方式を決めない（決定はP1-03）。

## 観測する2項目

1. **`harmonic_amplitude_motion`**：倍音の振幅比が時間的にどれだけ動くか。基音（1次）に対する
   各倍音（2次以降）の振幅比を有声フレームごとに求め、各倍音次数についての変動係数
   （フレーム間の標準偏差 / 平均）を、有効な倍音次数間で平均した値。大きいほど倍音バランスが
   時間的に大きく動く（加算合成が有利、というQ-001の判断材料に対応する）。
2. **`non_integer_partial_ratio`**：非整数次成分の割合。各有声フレームで、全スペクトルエネルギー
   に対して「同定できた整数次倍音のエネルギー」が占めない割合（1 - 倍音エネルギー/全エネルギー、
   [0, 1]にクリップ）を、有声フレーム間で平均した値。大きいほど非整数次・非調的な成分が多く、
   `inharmonicity`（`docs/02-engine-spec.md` Harmonic層）というスカラー1個で表現しきれない
   可能性が高いことを示す。

## 倍音の同定方法

f0推定（`harness.metrics.estimate_f0_contour`、pYIN）で得たf0軌跡・有声フラグを使う。
Q-011（`docs/06-open-questions.md`）の暫定の扱いに従い、この観測のために推定方式・
パラメータを新たに選び直さない（fmin/fmax/frame_length/hop_lengthは`harness.metrics`の
既定値をそのまま使う）。各有声フレームについて、k=1..n_harmonics（既定8）の理想周波数
`k * f0` の近傍（中心ビンの前後`harmonic_search_bins`ビン、既定2）の振幅二乗和の平方根を
その倍音の振幅とする（`estimate_partial_amplitudes` のdocstring参照。理想周波数がビン
境界に一致しないことによるスペクトル漏れを、窓内エネルギーの合計として回収するため）。
理想周波数がナイキスト周波数を超える次数は、そのフレームでは算出対象から除く
（存在しないと決めつけず、単に評価対象外とする）。

## 音高を持たない音源の扱い（Issue #47 完了条件）

有声フレーム比率（`voiced_frame_ratio`）が `DEFAULT_NO_PITCH_VOICED_RATIO_THRESHOLD`
未満の場合、`status="no_pitch"`として明示的に記録する。これは打楽器・ノイズ等、倍音構造を
定義できない音源を観測から除外せず欠測（valid、`docs/04-metrics.md`）として扱うための状態
であり、エラーではない。

閾値を「有声フレーム0件」ではなく比率にした理由：pYINは短時間の疑似周期性により、
真に無声の広帯域ノイズに対しても一部のフレームを有声と誤判定することがある（境界効果を
含む、`docs/06-open-questions.md` Q-011の対象外の既知の癖）。実測では、ゴールデン音源
セットの `white_noise`（10秒の白色雑音）で有声フレーム比率 約8.8%、`spoken_hello`
（発話）で約49%、`sine_440hz`（純音）で100%であった（`corpus/audio/` 同梱分での実測、
2026年時点）。この差は大きく、閾値0.15はノイズ側に余裕を持って倒れる。この閾値自体は
本観測ツール固有のヒューリスティックであり、`docs/02` `docs/03` の仕様値ではないため
ADRを要しない。実際の観測でノイズ以外の音源に誤判定が生じた場合は、この閾値の妥当性を
`docs/06-open-questions.md` に追記して見直す。

## 決定論とスコープ外

同一入力に対する2回の実行は出力JSONがビット単位で一致する（音声デコード・pYIN・STFTは
いずれも決定論的）。区間別（アタック/定常部等）の分解、合成方式の決定、エンジンの実装は
本モジュールのスコープ外。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import librosa
import numpy as np

from harness.audio_io import read_wav, to_mono
from harness.metrics import (
    DEFAULT_F0_FMAX_HZ,
    DEFAULT_F0_FMIN_HZ,
    DEFAULT_F0_FRAME_LENGTH,
    DEFAULT_F0_HOP_LENGTH,
    F0_ALGORITHM_NAME,
    estimate_f0_contour,
)

#: 倍音の同定を試みる最大次数（既定）。`docs/02-engine-spec.md` の `partial_amplitudes` に
#: 上限は定義されていない（`docs/06-open-questions.md` 未確定）。本観測用のパラメータであり、
#: エンジン仕様の上限を意味しない。
DEFAULT_N_HARMONICS = 8

#: 理想周波数（k*f0）の中心ビンから前後何ビンでピークを探すか。
DEFAULT_HARMONIC_SEARCH_BINS = 2

#: 有声フレーム比率がこの値未満なら「音高を持たない音源」として明示的に記録する
#: （モジュールdocstring「音高を持たない音源の扱い」参照。実測値に基づく閾値）。
DEFAULT_NO_PITCH_VOICED_RATIO_THRESHOLD = 0.15

#: 基音に対する平均振幅比がこの値未満の倍音は「観測されていない」として
#: `harmonic_amplitude_motion` の変動係数平均から除外する。実測（`corpus/audio/sine_440hz.ogg`、
#: 単一倍音のみを持つ純音）では、実体のない倍音位置の平均振幅比は最大でも約0.0001程度で
#: あり、拾われるのはノイズフロアのみだった。ノイズフロアは振幅がほぼ0であるがゆえに
#: 変動係数（標準偏差/平均）が発散しやすく、除外しないと「動きが大きい」と誤って報告される。
DEFAULT_MIN_RELATIVE_HARMONIC_AMPLITUDE = 0.01

#: このJSON出力のスキーマバージョン（`harness/harmonic_observation.schema.json` に合わせる）。
SCHEMA_VERSION = "1.0.0"

#: インデックスJSON自体の構造のバージョン（`harness.corpus_runner.INDEX_SCHEMA_VERSION` と同じ役割）。
INDEX_SCHEMA_VERSION = "1.0.0"

#: 全エネルギーがこれ以下のフレームは無音とみなし、非整数次成分比率の算出から除外する。
_ENERGY_EPS = 1e-12
#: 基音振幅がこれ以下のフレームは倍音比の分母として使わない。
_AMPLITUDE_EPS = 1e-12


class ManifestError(RuntimeError):
    """マニフェストが読めない、またはエントリが1件もないときに送出する（実行不能）。"""


def _metric_value(value: float) -> dict:
    return {"value": float(value), "missing_reason": None}


def _missing(reason: str) -> dict:
    return {"value": None, "missing_reason": reason}


def _stft_magnitude(signal: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
    """振幅スペクトログラム（周波数ビン × フレーム）を返す。"""
    return np.abs(librosa.stft(signal, n_fft=frame_length, hop_length=hop_length))


def estimate_partial_amplitudes(
    magnitude: np.ndarray,
    sample_rate: int,
    frame_length: int,
    f0: np.ndarray,
    voiced: np.ndarray,
    n_harmonics: int = DEFAULT_N_HARMONICS,
    search_bins: int = DEFAULT_HARMONIC_SEARCH_BINS,
) -> np.ndarray:
    """フレームごとに f0 の整数倍（倍音）位置の振幅を拾う。

    戻り値は形状 `(n_harmonics, n_frames)`。無声フレーム・ナイキスト周波数を超える次数は
    NaN（存在しないと決めつけず、算出対象外として区別する。モジュールdocstring参照）。
    `n_frames` は `magnitude` と `f0`/`voiced` のうち短い方に合わせる。

    振幅は探索窓内の最大値1本ではなく、窓内の振幅二乗和の平方根（窓内エネルギーの合計から
    逆算した実効振幅）で求める。理想周波数がビン境界に一致しない場合、真の信号エネルギーは
    隣接ビンに分散する（スペクトル漏れ）。最大値1本だけを拾うとこの分散分を取りこぼし、
    倍音振幅を過小評価する（`non_integer_partial_ratio` を過大評価する原因になる。
    実際のゴールデン音源 `sine_440hz.ogg` の観測で発覚したバグの回避）。
    """
    n_bins, n_stft_frames = magnitude.shape
    n_frames = min(n_stft_frames, len(f0))
    freqs = librosa.fft_frequencies(sr=sample_rate, n_fft=frame_length)
    bin_width = freqs[1] - freqs[0]
    nyquist = sample_rate / 2.0

    amplitudes = np.full((n_harmonics, n_frames), np.nan)
    for t in range(n_frames):
        if not voiced[t] or not np.isfinite(f0[t]) or f0[t] <= 0.0:
            continue
        for k in range(1, n_harmonics + 1):
            ideal_hz = k * f0[t]
            if ideal_hz > nyquist:
                break  # 以降の次数もナイキストを超えるため打ち切り
            center_bin = round(ideal_hz / bin_width)
            lo = max(0, center_bin - search_bins)
            hi = min(n_bins - 1, center_bin + search_bins)
            window = magnitude[lo : hi + 1, t]
            amplitudes[k - 1, t] = float(np.sqrt(np.sum(window**2.0)))
    return amplitudes


def harmonic_amplitude_motion(
    partial_amplitudes: np.ndarray,
    min_relative_amplitude: float = DEFAULT_MIN_RELATIVE_HARMONIC_AMPLITUDE,
) -> dict:
    """倍音振幅比（対基音）の時間変動を、各倍音次数の変動係数の平均として返す。

    2次以降の各倍音について、基音振幅比（`amp[k] / amp[1]`）が有効な（両方が有限かつ
    基音振幅が`_AMPLITUDE_EPS`超）フレームが2未満の場合は、その次数を平均対象から除く。
    さらに、比の平均が`min_relative_amplitude`未満の次数も除く：その倍音位置に実体の
    信号がなくノイズフロアだけが拾われている（ノイズフロアは振幅がほぼ0であるがゆえに
    変動係数が発散しやすく、実在しない「動き」を作り出す。`DEFAULT_MIN_RELATIVE_HARMONIC_AMPLITUDE`
    のdocstring参照）。有効な次数が1つもない場合は欠測（モジュールdocstring「観測する2項目」参照）。
    """
    n_harmonics, _n_frames = partial_amplitudes.shape
    fundamental = partial_amplitudes[0, :]

    coefficients_of_variation = []
    for k in range(1, n_harmonics):
        valid = (
            np.isfinite(partial_amplitudes[k, :])
            & np.isfinite(fundamental)
            & (fundamental > _AMPLITUDE_EPS)
        )
        if np.count_nonzero(valid) < 2:
            continue
        ratio = partial_amplitudes[k, valid] / fundamental[valid]
        mean = float(np.mean(ratio))
        if mean < min_relative_amplitude:
            continue
        coefficients_of_variation.append(float(np.std(ratio)) / mean)

    if not coefficients_of_variation:
        return _missing(
            "倍音振幅比の変動を算出できる倍音次数が1つもない"
            "（有声フレーム不足、または2次以降の倍音の平均振幅比が"
            f"{min_relative_amplitude}未満でノイズフロアと区別できない）"
        )
    return _metric_value(float(np.mean(coefficients_of_variation)))


def non_integer_partial_ratio(
    magnitude: np.ndarray,
    voiced: np.ndarray,
    partial_amplitudes: np.ndarray,
) -> dict:
    """有声フレームにおける非整数次成分の割合（1 - 倍音エネルギー/全エネルギー）の平均。

    全エネルギーが`_ENERGY_EPS`以下（無音）のフレームは平均から除く。有効なフレームが
    1つもない場合は欠測。
    """
    total_energy = np.sum(magnitude**2.0, axis=0)
    n_frames = min(len(total_energy), partial_amplitudes.shape[1], len(voiced))

    ratios = []
    for t in range(n_frames):
        if not voiced[t]:
            continue
        energy = total_energy[t]
        if energy <= _ENERGY_EPS:
            continue
        harmonic_energy = float(np.nansum(partial_amplitudes[:, t] ** 2.0))
        ratio = 1.0 - harmonic_energy / energy
        ratios.append(float(np.clip(ratio, 0.0, 1.0)))

    if not ratios:
        return _missing("有声フレームが存在しないため非整数次成分の割合を算出できない")
    return _metric_value(float(np.mean(ratios)))


def _build_calc_conditions(
    n_harmonics: int,
    search_bins: int,
    fmin: float,
    fmax: float,
    frame_length: int,
    hop_length: int,
    no_pitch_voiced_ratio_threshold: float,
    min_relative_harmonic_amplitude: float,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "n_harmonics": int(n_harmonics),
        "harmonic_search_bins": int(search_bins),
        "no_pitch_voiced_ratio_threshold": float(no_pitch_voiced_ratio_threshold),
        "min_relative_harmonic_amplitude": float(min_relative_harmonic_amplitude),
        "f0_estimation": {
            "name": F0_ALGORITHM_NAME,
            "version": librosa.__version__,
            "fmin_hz": float(fmin),
            "fmax_hz": float(fmax),
            "frame_length": int(frame_length),
            "hop_length": int(hop_length),
        },
    }


def _build_record(
    target_name: str,
    status: str,
    status_detail: str | None,
    voiced_frame_ratio: float | None,
    calc_conditions: dict,
    harmonic_amplitude_motion_value: dict | None = None,
    non_integer_partial_ratio_value: dict | None = None,
) -> dict:
    reason = status_detail or f"status={status} のため算出していない"
    return {
        "target": target_name,
        "status": status,
        "status_detail": status_detail,
        "voiced_frame_ratio": (
            float(voiced_frame_ratio) if voiced_frame_ratio is not None else None
        ),
        "harmonic_amplitude_motion": harmonic_amplitude_motion_value or _missing(reason),
        "non_integer_partial_ratio": non_integer_partial_ratio_value or _missing(reason),
        "calc_conditions": calc_conditions,
    }


def observe_harmonic_structure(
    path: str | Path,
    n_harmonics: int = DEFAULT_N_HARMONICS,
    fmin: float = DEFAULT_F0_FMIN_HZ,
    fmax: float = DEFAULT_F0_FMAX_HZ,
    frame_length: int = DEFAULT_F0_FRAME_LENGTH,
    hop_length: int = DEFAULT_F0_HOP_LENGTH,
    search_bins: int = DEFAULT_HARMONIC_SEARCH_BINS,
    no_pitch_voiced_ratio_threshold: float = DEFAULT_NO_PITCH_VOICED_RATIO_THRESHOLD,
    min_relative_harmonic_amplitude: float = DEFAULT_MIN_RELATIVE_HARMONIC_AMPLITUDE,
) -> dict:
    """1音源から倍音構造の観測結果（`harmonic_observation.schema.json` に valid）を組み立てる。

    有声フレーム比率が`no_pitch_voiced_ratio_threshold`未満の音源は`status="no_pitch"`として
    明示的に記録する（モジュールdocstring「音高を持たない音源の扱い」参照）。
    """
    calc_conditions = _build_calc_conditions(
        n_harmonics, search_bins, fmin, fmax, frame_length, hop_length,
        no_pitch_voiced_ratio_threshold, min_relative_harmonic_amplitude,
    )
    target_name = Path(path).name

    buffer = to_mono(read_wav(path))
    signal = buffer.data[:, 0]
    sample_rate = buffer.sample_rate

    f0, voiced = estimate_f0_contour(signal, sample_rate, fmin, fmax, frame_length, hop_length)
    n_total_frames = len(f0)
    n_voiced = int(np.count_nonzero(voiced))
    voiced_frame_ratio = (n_voiced / n_total_frames) if n_total_frames > 0 else 0.0

    if voiced_frame_ratio < no_pitch_voiced_ratio_threshold:
        return _build_record(
            target_name=target_name,
            status="no_pitch",
            status_detail=(
                f"有声フレーム比率が{voiced_frame_ratio:.4f}"
                f"（閾値{no_pitch_voiced_ratio_threshold}未満）"
                "であり、音高を持たない音源として明示的に記録する（打楽器・ノイズ等）"
            ),
            voiced_frame_ratio=voiced_frame_ratio,
            calc_conditions=calc_conditions,
        )

    magnitude = _stft_magnitude(signal, frame_length, hop_length)
    partial_amplitudes = estimate_partial_amplitudes(
        magnitude, sample_rate, frame_length, f0, voiced, n_harmonics, search_bins
    )
    motion = harmonic_amplitude_motion(partial_amplitudes, min_relative_harmonic_amplitude)
    non_integer = non_integer_partial_ratio(magnitude, voiced, partial_amplitudes)

    return _build_record(
        target_name=target_name,
        status="ok",
        status_detail=None,
        voiced_frame_ratio=voiced_frame_ratio,
        calc_conditions=calc_conditions,
        harmonic_amplitude_motion_value=motion,
        non_integer_partial_ratio_value=non_integer,
    )


def _resolve_local_path(entry: dict, repo_root: Path) -> Path:
    """マニフェストの1エントリから音声のローカルパスを解決する。

    `harness.corpus_runner._resolve_local_path` と同じ規則（bundled ならその `bundled_path`、
    そうでなければ `corpus/.cache/<id>.<format>`）。規則の実体は取得スクリプト
    （`corpus/fetch_and_verify.py`）側にあり、ここでは同じ規則を踏襲するだけに留める。
    """
    if entry.get("bundled"):
        return repo_root / entry["bundled_path"]
    return repo_root / "corpus" / ".cache" / f"{entry['id']}.{entry['format']}"


def observe_corpus(
    manifest_path: str | Path,
    out_dir: str | Path,
    n_harmonics: int = DEFAULT_N_HARMONICS,
) -> dict:
    """マニフェストの全エントリに対して倍音構造観測を実行し、結果一式を `out_dir` に書き出す。

    `<out_dir>/<id>.json`（音源1件につき1つ）と `<out_dir>/index.json`（全件を束ねる）を
    出力する。音声ファイルが手元に存在しないエントリは`missing_audio`として記録し、残りの
    処理を継続する（`harness.corpus_runner.run_corpus` と同じ「1件の欠測・失敗が全体を
    止めない」規約）。
    """
    manifest_path = Path(manifest_path)
    out_dir = Path(out_dir)

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"マニフェストを読み込めない: {manifest_path}: {exc}") from exc

    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ManifestError(f"マニフェストにエントリが1件もない: {manifest_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    repo_root = manifest_path.resolve().parent.parent
    missing_calc_conditions = _build_calc_conditions(
        n_harmonics,
        DEFAULT_HARMONIC_SEARCH_BINS,
        DEFAULT_F0_FMIN_HZ,
        DEFAULT_F0_FMAX_HZ,
        DEFAULT_F0_FRAME_LENGTH,
        DEFAULT_F0_HOP_LENGTH,
        DEFAULT_NO_PITCH_VOICED_RATIO_THRESHOLD,
        DEFAULT_MIN_RELATIVE_HARMONIC_AMPLITUDE,
    )

    index_entries = []
    for entry in entries:
        entry_id = entry["id"]
        start = time.perf_counter()
        local_path = _resolve_local_path(entry, repo_root)

        if not local_path.exists():
            retrieval = entry.get("retrieval") or "取得手順は corpus/manifest.json を参照"
            record = _build_record(
                target_name=entry_id,
                status="missing_audio",
                status_detail=f"音源が手元に存在しない: {local_path}（{retrieval}）",
                voiced_frame_ratio=None,
                calc_conditions=missing_calc_conditions,
            )
        else:
            try:
                record = observe_harmonic_structure(local_path, n_harmonics=n_harmonics)
            except Exception as exc:  # noqa: BLE001 - 1件の失敗で全体を止めないため捕捉して記録する
                record = _build_record(
                    target_name=entry_id,
                    status="error",
                    status_detail=f"観測が例外で失敗: {type(exc).__name__}: {exc}",
                    voiced_frame_ratio=None,
                    calc_conditions=missing_calc_conditions,
                )
        # target フィールドはファイル名ではなくマニフェストのidに揃える
        # （harness.corpus_runner.run_corpus と同じ規則。複数エントリを束ねるインデックスと
        # 突き合わせるときの一意なキーにするため）。
        record["target"] = entry_id
        elapsed_s = time.perf_counter() - start

        output_filename = f"{entry_id}.json"
        (out_dir / output_filename).write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        index_entries.append(
            {
                "id": entry_id,
                "status": record["status"],
                "output_file": output_filename,
                "elapsed_s": elapsed_s,
            }
        )

    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "manifest_path": str(manifest_path),
        "entry_count": len(index_entries),
        "entries": index_entries,
        "summary": {
            "ok": sum(1 for e in index_entries if e["status"] == "ok"),
            "no_pitch": sum(1 for e in index_entries if e["status"] == "no_pitch"),
            "missing_audio": sum(1 for e in index_entries if e["status"] == "missing_audio"),
            "error": sum(1 for e in index_entries if e["status"] == "error"),
        },
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return index


__all__ = [
    "DEFAULT_HARMONIC_SEARCH_BINS",
    "DEFAULT_MIN_RELATIVE_HARMONIC_AMPLITUDE",
    "DEFAULT_NO_PITCH_VOICED_RATIO_THRESHOLD",
    "DEFAULT_N_HARMONICS",
    "INDEX_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "ManifestError",
    "estimate_partial_amplitudes",
    "harmonic_amplitude_motion",
    "non_integer_partial_ratio",
    "observe_corpus",
    "observe_harmonic_structure",
]
