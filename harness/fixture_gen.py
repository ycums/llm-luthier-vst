"""既知解テスト用の合成フィクチャ生成器（P0-04）。

指標の正しさを検証するため、差が既知の音源ペアを決定論的に生成する。ゴールデン音源だけでは
「値が出た」ことしか確認できず、その値が正しいかを判定できないため（Issue #4 目的）。

## これはシンセエンジンではない

- プリセット（docs/03）を読まない。層構造（docs/02）を持たない。
- フェーズ1エンジンの前身として設計しないこと。ここで作る信号生成コードをエンジンに
  流用してはならない（Issue #4 スコープ外、AGENTS.md の責務分担）。

## 決定論

`seed` から `numpy.Generator(PCG64)` を1つ生成し、基底波形の初期位相とノイズ等の確率要素は
すべてそこから取る。同一 seed で同コマンドを実行すれば、生成されるすべてのWAVのバイト列が
一致する。生成物（WAVとメタデータ）はバージョン管理に含めない。テスト実行時の一時的な出力と
して扱う（.gitignore の renders/ 方針）。

## 出力規則

- 全WAVは FLOAT（32bit浮動小数）サブタイプ・モノラル・16kHzで書き出す。既知のdB差・SNR・
  カットオフが量子化誤差で乱されないようにするため（16bitだとゲイン差が厳密に保てない）。
- 各ペア: ディレクトリごとに `target.wav` と `candidate.wav` の2本。
- `metadata.json` に、seed・サンプルレート・各ペアの「既知の差（known）」を値として出力する。
  テストはこの値を参照する。
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, filtfilt

# 出力サンプルレート。テストの速さと周波数分解能の両立地点。
SAMPLE_RATE = 16000
# 基底音の長さ（秒）。f0グライド区間などを表現するのに十分な長さ。
DURATION_S = 0.5
_NUM_S = int(SAMPLE_RATE * DURATION_S)

# FLOAT書き出しで既知差の精確性を保つため、書き出しサブタイプをFLOATに固定する。
_FLOAT_SUBTYPE = "FLOAT"

# 基底音（ハーモナイクトーン）の倍音数と振幅プロフィル。
# 低域に f0 の整数倍が並び、ローパスの高域成分減衰を確認できるスペクトル豊度を持たせる。
_HARMONIC_PROFILE: list[float] = [
    1.00, 0.70, 0.50, 0.40, 0.30, 0.25,
    0.20, 0.17, 0.15, 0.13, 0.11, 0.10,
]

# 基底音の基本周波数。全ペア共通（f0ペアのみ別値を使う）。
_F0_BASE_HZ = 220.0

# 各ペアの「既知の差」。テストの KNOWN_* はここから参照し、重複定義のドリフトを防ぐ。
GAIN_DB = 6.0
LOWPASS_CUTOFF_HZ = 2000.0
NOISE_SNR_DB = 15.0
ONSET_SHIFT_S = 0.05
F0_FIXED_HZ = 220.0
F0_GLIDE_FROM_HZ = 220.0
F0_GLIDE_TO_HZ = 330.0
GLIDE_INTERVAL_S: tuple[float, float] = (0.10, 0.40)

# 既知のフォルマント（共振）列: (中心Hz, 帯域幅Hz, ゲイン)。フォルマント軌跡距離（P0-09）の
# 既知解検証用。テストがこの共振の再現を確認する。
FORMANT_RESONANCES: list[tuple[float, float, float]] = [
    (500.0, 70.0, 1.0),
    (1500.0, 100.0, 0.7),
    (2500.0, 130.0, 0.4),
]
# フォルマント移動: 一方のペアで第2フォルマントを既知量だけ移す（formant_dist の既知解）。
FORMANT_SHIFT_HZ = 100.0
# 移動フォルマント: 第2フォルマントが既知区間で既知値へグライド（追跡の既知解）。
FORMANT_GLIDE_FROM_HZ = 1500.0
FORMANT_GLIDE_TO_HZ = 1800.0
FORMANT_GLIDE_INTERVAL_S: tuple[float, float] = (0.15, 0.35)


@dataclass
class FixturePair:
    """生成するペア1組。

    target / candidate: 形状 `(NUM_S, 1)` のfloat64モノラル音声（未正規化）。
    known: このペアの「既知の差」を表すdict（メタデータJSONにそのまま書くもの）。
    """

    name: str
    target: np.ndarray
    candidate: np.ndarray
    known: dict


def _generate_rng(seed: int) -> np.random.Generator:
    """seed から決定論的なRNGを生成する。"""
    return np.random.default_rng(seed)


def _instantaneous_phase(f0_hz: np.ndarray) -> np.ndarray:
    """時間変化する f0（Hz、サンプルごとの値）から瞬間位相を積分する。"""
    return 2.0 * np.pi * np.cumsum(f0_hz) / SAMPLE_RATE


def _tone(f0_hz: float, phase_offset: float, num_s: int) -> np.ndarray:
    """固定 f0 のハーモナイクトーンを生成する。"""
    t = np.arange(num_s) / SAMPLE_RATE
    phase = 2.0 * np.pi * f0_hz * t + phase_offset
    out = np.zeros(num_s)
    for k, amp in enumerate(_HARMONIC_PROFILE, start=1):
        out += amp * np.sin(k * phase)
    return out.reshape(num_s, 1)


def _glide_tone(
    f0_start_hz: float,
    f0_end_hz: float,
    glide_from_s: float,
    glide_to_s: float,
    phase_offset: float,
    num_s: int,
) -> np.ndarray:
    """既知の区間 [glide_from_s, glide_to_s] で f0_start から f0_end へ線形にグライドするトーン。

    f0 は区間外では両端の値に保持される（区間前=start、区間後=end）。
    """
    t = np.arange(num_s) / SAMPLE_RATE
    f0 = np.full(num_s, f0_start_hz)
    glide_mask = (t >= glide_from_s) & (t <= glide_to_s)
    glide_dur = glide_to_s - glide_from_s
    if glide_dur > 0.0:
        lin = (t[glide_mask] - glide_from_s) / glide_dur
        f0[glide_mask] = f0_start_hz + (f0_end_hz - f0_start_hz) * lin
    f0[t > glide_to_s] = f0_end_hz

    phase = _instantaneous_phase(f0) + phase_offset
    out = np.zeros(num_s)
    for k, amp in enumerate(_HARMONIC_PROFILE, start=1):
        out += amp * np.sin(k * phase)
    return out.reshape(num_s, 1)


def _formant_band_signal(
    center_freqs_hz: np.ndarray,
    bandwidths_hz: np.ndarray,
    gains: np.ndarray,
    rng: np.random.Generator,
    num_s: int,
) -> np.ndarray:
    """時間変化する共振を持つ帯域信号（source-filter的なモデル）を生成する。

    `center_freqs_hz` の各要素は各フォルマントのサンプルごとの中心周波数（Hz）。各フォルマントは
    中心周波数の周囲に `bandwidths_hz` の帯域幅で5本のトーンをクラスタリングして作る。振幅は中心から
    帯域半幅に向かってガウス状に減衰し、中心が支配的なピークになる（推定の既知解としてテストが参照）。
    位相は `rng` から決定論的に取る。
    """
    out = np.zeros(num_s)
    for fc_t, bw, gain in zip(center_freqs_hz, bandwidths_hz, gains):
        offsets = np.linspace(-bw / 2.0, bw / 2.0, 5)
        amps = np.exp(-((offsets) / (bw * 0.5)) ** 2.0)
        for off, a in zip(offsets, amps):
            phase_offset = float(rng.uniform(0.0, 2.0 * np.pi))
            phase = _instantaneous_phase(fc_t + off) + phase_offset
            out += gain * a * np.sin(phase)
    return out.reshape(num_s, 1)


def _static_formant_band(
    resonances: list[tuple[float, float, float]],
    rng: np.random.Generator,
    num_s: int,
) -> np.ndarray:
    """固定共振の帯域信号。`resonances` は (center_hz, bandwidth_hz, gain) の列。"""
    fc = np.tile(
        np.array([r[0] for r in resonances], dtype=np.float64)[:, None], (1, num_s)
    )
    bws = np.array([r[1] for r in resonances], dtype=np.float64)
    gains = np.array([r[2] for r in resonances], dtype=np.float64)
    return _formant_band_signal(fc, bws, gains, rng, num_s)


def _rms_amp(data: np.ndarray) -> float:
    """信号のRMS振幅を返す（ノイズSNR算出に使う）。"""
    return float(np.sqrt(np.mean(data**2.0)))


def _build_pairs(seed: int) -> list[FixturePair]:
    """seed から全6ペアの信号を生成する（ディスクへの書き出しは行わない）。"""
    rng = _generate_rng(seed)
    num_s = int(SAMPLE_RATE * DURATION_S)
    # 基底の初期位相。seed 依存にし、「同 seed で一致し、異 seed で異なる」意味を持たせる。
    phase_offset = float(rng.uniform(0.0, 2.0 * np.pi))

    # 共通の基底（target側の素）。素直なハーモナイクトーン。
    base = _tone(_F0_BASE_HZ, phase_offset, num_s)

    pairs: list[FixturePair] = []

    # (a) 完全に同一の2ファイル
    pairs.append(
        FixturePair(
            name="a_identical",
            target=base.copy(),
            candidate=base.copy(),
            known={"relation": "identical", "expected_diff": 0.0},
        )
    )

    # (b) 既知のdBだけゲイン違い（candidate の方が大きい）
    gain_db = GAIN_DB
    pairs.append(
        FixturePair(
            name="b_gain",
            target=base.copy(),
            candidate=(base * (10.0 ** (gain_db / 20.0))).copy(),
            known={
                "gain_db": gain_db,
                "direction": "candidate = target * 10^(gain_db/20)（candidate の方が gain_db dB 大きい）",
            },
        )
    )

    # (c) 既知のカットオフでローパス（candidate の方が高域が落ちる）
    cutoff_hz = LOWPASS_CUTOFF_HZ
    b, a = butter(4, cutoff_hz / (SAMPLE_RATE / 2.0), btype="low")
    candidate_c = filtfilt(b, a, base[:, 0]).reshape(num_s, 1)
    pairs.append(
        FixturePair(
            name="c_lowpass",
            target=base.copy(),
            candidate=candidate_c,
            known={"cutoff_hz": cutoff_hz},
        )
    )

    # (d) 既知のSNRのノイズ加算（candidate = target + 白色ガウスノイズ）
    snr_db = NOISE_SNR_DB
    noise_rms = _rms_amp(base[:, 0]) / (10.0 ** (snr_db / 20.0))
    white_noise = rng.normal(0.0, 1.0, num_s)
    white_noise *= noise_rms / _rms_amp(white_noise)
    candidate_d = (base[:, 0] + white_noise).reshape(num_s, 1)
    pairs.append(
        FixturePair(
            name="d_noise",
            target=base.copy(),
            candidate=candidate_d,
            known={
                "snr_db": snr_db,
                "reference": "candidate = target + white gauss noise。SNR_db = 20*log10(rms_sig / rms_noise)",
            },
        )
    )

    # (e) アタックの立ち上がり位置が既知の時間だけ後ろにずれている（candidate の先頭に無声を挿入）
    onset_shift_s = ONSET_SHIFT_S
    shift_samps = int(round(onset_shift_s * SAMPLE_RATE))
    candidate_e = np.concatenate(
        (
            np.zeros((shift_samps, 1)),
            base[: num_s - shift_samps],
        )
    )
    pairs.append(
        FixturePair(
            name="e_attack_shift",
            target=base.copy(),
            candidate=candidate_e,
            known={"onset_shift_s": onset_shift_s},
        )
    )

    # (f) 基本周波数が既知（固定 / 既知区間で既知の値へグライド）
    f0_fixed_hz = F0_FIXED_HZ
    f0_glide_from_hz = F0_GLIDE_FROM_HZ
    f0_glide_to_hz = F0_GLIDE_TO_HZ
    glide_interval_s = list(GLIDE_INTERVAL_S)
    candidate_f = _glide_tone(
        f0_glide_from_hz,
        f0_glide_to_hz,
        glide_interval_s[0],
        glide_interval_s[1],
        phase_offset,
        num_s,
    )
    pairs.append(
        FixturePair(
            name="f_f0_glide",
            target=_tone(f0_fixed_hz, phase_offset, num_s),
            candidate=candidate_f,
            known={
                "f0_fixed_hz": f0_fixed_hz,
                "f0_glide_from_hz": f0_glide_from_hz,
                "f0_glide_to_hz": f0_glide_to_hz,
                "glide_interval_s": glide_interval_s,
            },
        )
    )

    # (g) 既知フォルマント。candidate の第2フォルマントが既知量（FORMANT_SHIFT_HZ）だけ上に移る。
    formant_res = list(FORMANT_RESONANCES)
    bws = np.array([r[1] for r in formant_res])
    gains = np.array([r[2] for r in formant_res])
    fc_base = [r[0] for r in formant_res]
    fc_cand = fc_base.copy()
    fc_cand[1] += FORMANT_SHIFT_HZ
    candidate_g = _formant_band_signal(
        np.tile(np.array(fc_cand)[:, None], (1, num_s)), bws, gains, rng, num_s
    )
    pairs.append(
        FixturePair(
            name="g_formant_shift",
            target=_static_formant_band(formant_res, rng, num_s),
            candidate=candidate_g,
            known={
                "formant_freqs_target_hz": fc_base,
                "formant_freqs_candidate_hz": fc_cand,
                "formant_bw_hz": bws.tolist(),
                "formant_gain": gains.tolist(),
                "formant_shift_hz": FORMANT_SHIFT_HZ,
            },
        )
    )

    # (h) 移動フォルマント。第2フォルマントが既知区間で 1500→1800Hz へグライド（追跡の既知解）。
    fg_from, fg_to = FORMANT_GLIDE_FROM_HZ, FORMANT_GLIDE_TO_HZ
    glide_from_s, glide_to_s = FORMANT_GLIDE_INTERVAL_S
    fc_h = np.tile(np.array(fc_base, dtype=np.float64)[:, None], (1, num_s))
    t_vals = np.arange(num_s) / SAMPLE_RATE
    mask = (t_vals >= glide_from_s) & (t_vals <= glide_to_s)
    fc_h[1, mask] = fg_from + (fg_to - fg_from) * (t_vals[mask] - glide_from_s) / (glide_to_s - glide_from_s)
    fc_h[1, t_vals > glide_to_s] = fg_to
    candidate_h = _formant_band_signal(fc_h, bws, gains, rng, num_s)
    pairs.append(
        FixturePair(
            name="h_formant_glide",
            target=_static_formant_band(formant_res, rng, num_s),
            candidate=candidate_h,
            known={
                "formant_freqs_target_hz": fc_base,
                "formant_f2_glide_from_hz": fg_from,
                "formant_f2_glide_to_hz": fg_to,
                "glide_interval_s": [glide_from_s, glide_to_s],
                "formant_bw_hz": bws.tolist(),
                "formant_gain": gains.tolist(),
            },
        )
    )

    return pairs


def _strip_peak_chunk(raw: bytes) -> bytes:
    """WAVのバイト列から PEAK chunk を除去する。

    libsndfile は IEEE-float のWAVを書き出すときに PEAK chunk を付加する。そのピーク値は
    実行環境の浮動小数点演算の僅かな差で 1 ULP 単位で揺れるため、同一 seed でも
    プロセスが異なるとWAVのバイト列が一致しなくなる。PEAK chunk を除去することで
    バイト単位の決定論を回復する（data chunk の中身は決定論的である）。

    RIFF ヘッダのサイズフィールドは、PEAK chunk を除いた残り全体に合わせて更新する。
    他の chunk（fmt / fact / data）はバイト列をそのまま残す。
    """
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return raw  # WAV以外（判定不能）は手を加えない

    offset = 12  # 'RIFF' + size + 'WAVE'
    kept = bytearray(raw[:12])  # ヘッダをコピー（sizeは後で更新）
    while offset + 8 <= len(raw):
        chunk_id = raw[offset : offset + 4]
        size = struct.unpack("<I", raw[offset + 4 : offset + 8])[0]
        data_start = offset + 8
        data_end = data_start + size
        padded_end = data_end + (size % 2)  # chunk は 2 バイト境界にパディング
        if chunk_id == b"PEAK":
            offset = padded_end
            continue
        kept.extend(raw[offset:padded_end])
        offset = padded_end

    # RIFF の size フィールド = ヘッダ8byteを除いた残り全体
    kept[4:8] = struct.pack("<I", len(kept) - 8)
    return bytes(kept)


def _write_float_mono(path: Path, data: np.ndarray) -> None:
    """FLOAT・モノラルのWAVを書き出し、PEAK chunk を除去してバイト決定的にする。"""
    sf.write(str(path), data, SAMPLE_RATE, subtype=_FLOAT_SUBTYPE)
    stripped = _strip_peak_chunk(path.read_bytes())
    path.write_bytes(stripped)


def generate_all(out_dir: Path | str, seed: int) -> dict:
    """seed から全ペアのWAVを out_dir の直下に生成し、メタデータ辞書を返す。

    out_dir 配下レイアウト:
        <name>/target.wav
        <name>/candidate.wav
        metadata.json

    WAVとメタデータは決定論であり、同 seed で2回実行すればビット単位で一致する。
    """
    out_dir = Path(out_dir)
    pairs = _build_pairs(seed)

    pair_metas = []
    for pair in pairs:
        pair_dir = out_dir / pair.name
        pair_dir.mkdir(parents=True, exist_ok=True)
        target_path = pair_dir / "target.wav"
        candidate_path = pair_dir / "candidate.wav"
        _write_float_mono(target_path, pair.target)
        _write_float_mono(candidate_path, pair.candidate)
        pair_metas.append(
            {
                "name": pair.name,
                "target": f"{pair.name}/target.wav",
                "candidate": f"{pair.name}/candidate.wav",
                "known": pair.known,
                "target_sha256": _sha256(target_path),
                "candidate_sha256": _sha256(candidate_path),
            }
        )

    metadata = {
        "seed": seed,
        "sample_rate": SAMPLE_RATE,
        "duration_s": DURATION_S,
        "subtype": _FLOAT_SUBTYPE,
        "pairs": pair_metas,
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return metadata


def _sha256(path: Path) -> str:
    """ファイルのSHA-256を返す（決定論テスト用）。"""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


__all__ = [
    "generate_all",
    "SAMPLE_RATE",
    "DURATION_S",
    "GAIN_DB",
    "LOWPASS_CUTOFF_HZ",
    "NOISE_SNR_DB",
    "ONSET_SHIFT_S",
    "F0_FIXED_HZ",
    "F0_GLIDE_FROM_HZ",
    "F0_GLIDE_TO_HZ",
    "GLIDE_INTERVAL_S",
    "FORMANT_RESONANCES",
    "FORMANT_SHIFT_HZ",
    "FORMANT_GLIDE_FROM_HZ",
    "FORMANT_GLIDE_TO_HZ",
    "FORMANT_GLIDE_INTERVAL_S",
]