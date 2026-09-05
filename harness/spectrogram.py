"""スペクトログラム画像生成ツール（P0-14）。

`AGENTS.md` 第4節「エビデンス要件」が要求する、音に影響する変更のPRに添付する
「差分が最大だった1〜2音源のスペクトログラム画像（変更前後の対）」を生成する。

## これは指標算出ではない

本モジュールは図示のみを行う。指標の算出（マルチスケールスペクトル距離等、
`docs/04-metrics.md`）はP0-05以降の別モジュールが担う。ここで使うFFTサイズ・
ホップ長・窓関数は純粋に図示のためのパラメータであり、`docs/06-open-questions.md`
Q-004（帯域分割方式）とは無関係である（Issue #14 補足）。混同しないこと。

## 対になる2枚（＋差分1枚）の同一性

target・candidate の2枚は、必ず同一の周波数軸範囲（同一サンプルレートに由来する
ナイキスト周波数）・同一の時間軸範囲・同一のカラースケール範囲（`db_min`/`db_max`
を明示的に指定し、matplotlibの自動スケーリングに任せない）で描画する。
2枚のサンプル数が異なる場合、STFTのフレーム格子がずれて差分画像が成立しないため、
`AudioLengthMismatchError` を送出して停止する（暗黙の切り詰め・パディングはしない）。

## 決定論

matplotlibのAggバックエンドを使い、PNGメタデータにタイムスタンプ等の非決定要素を
含めずに書き出す。同一環境・同一入力であれば、生成したPNGはビット単位で一致する。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # バックエンド確定後にimportする必要がある
import numpy as np
from scipy.signal import stft

from harness.audio_io import read_wav, require_same_sample_rate, to_mono

# 振幅0（無音）でlog10が発散しないための下駄。dB値は -240dB 相当が下限になる。
_AMPLITUDE_FLOOR = 1e-12

# 図の見た目を固定するための定数。値を変えると出力PNGのバイト列が変わる。
_FIGSIZE = (8.0, 4.0)
_DPI = 100
_SPECTROGRAM_CMAP = "magma"
_DIFF_CMAP = "coolwarm"
# savefigのメタデータに実行環境依存の値（matplotlibバージョン文字列等）を含めないための固定値。
_PNG_METADATA = {"Software": "llm-luthier-harness spectrogram tool"}


class AudioLengthMismatchError(ValueError):
    """target と candidate のサンプル数が一致しないときに送出する。

    STFTのフレーム格子（時間軸）がずれると、差分画像が「同一の時間・周波数グリッド上の差」
    にならない。暗黙の切り詰め・ゼロ詰めは行わず、ここで停止する。
    """


@dataclass(frozen=True)
class SpectrogramGrid:
    """1音源分のSTFT結果（dBスケール）。

    Attributes:
        freqs: 周波数軸（Hz）。形状 `(num_freq_bins,)`
        times: 時間軸（秒）。形状 `(num_frames,)`
        db: 振幅スペクトルのdB値。形状 `(num_freq_bins, num_frames)`
    """

    freqs: np.ndarray
    times: np.ndarray
    db: np.ndarray


def _compute_spectrogram_db(
    data: np.ndarray,
    sample_rate: int,
    fft_size: int,
    hop_length: int,
    window: str,
) -> SpectrogramGrid:
    """モノラル信号からdBスケールのSTFTスペクトログラムを計算する。

    フレームは信号の内側だけを使う（`boundary=None, padded=False`）。端をゼロ詰めで
    延長すると、時間軸の範囲が実際の音声長からずれるため。
    """
    freqs, times, zxx = stft(
        data,
        fs=sample_rate,
        window=window,
        nperseg=fft_size,
        noverlap=fft_size - hop_length,
        boundary=None,
        padded=False,
    )
    db = 20.0 * np.log10(np.maximum(np.abs(zxx), _AMPLITUDE_FLOOR))
    return SpectrogramGrid(freqs=freqs, times=times, db=db)


def _params_line(fft_size: int, hop_length: int, window: str) -> str:
    return f"fft_size={fft_size}  hop_length={hop_length}  window={window}"


def _render_panel(
    grid: SpectrogramGrid,
    *,
    title: str,
    fft_size: int,
    hop_length: int,
    window: str,
    db_min: float,
    db_max: float,
):
    """1枚分のスペクトログラム画像のFigureを構築して返す（保存は呼び出し側）。

    `vmin`/`vmax` を明示的に固定するのがこの関数の核心：matplotlibの自動レンジ
    調整（データのmin/maxに合わせる挙動）に任せると、2枚のカラースケールが
    互いの信号レベルに応じて食い違ってしまう。
    """
    fig, ax = plt.subplots(figsize=_FIGSIZE, dpi=_DPI)
    mesh = ax.pcolormesh(
        grid.times,
        grid.freqs,
        grid.db,
        shading="gouraud",
        cmap=_SPECTROGRAM_CMAP,
        vmin=db_min,
        vmax=db_max,
    )
    ax.set_xlabel("time [s]")
    ax.set_ylabel("frequency [Hz]")
    ax.set_title(f"{title}\n{_params_line(fft_size, hop_length, window)}")
    fig.colorbar(mesh, ax=ax, label=f"dB (vmin={db_min:g}, vmax={db_max:g})")
    fig.tight_layout()
    return fig, mesh


def _render_diff_panel(
    grid_target: SpectrogramGrid,
    grid_candidate: SpectrogramGrid,
    *,
    fft_size: int,
    hop_length: int,
    window: str,
    diff_range_db: float,
):
    """差分（candidate - target）のFigureを構築して返す。

    2つのグリッドの `freqs`/`times` が同一であることは呼び出し側
    （`render_pair` が `AudioLengthMismatchError` で保証）に依存する。
    """
    diff_db = grid_candidate.db - grid_target.db
    fig, ax = plt.subplots(figsize=_FIGSIZE, dpi=_DPI)
    mesh = ax.pcolormesh(
        grid_target.times,
        grid_target.freqs,
        diff_db,
        shading="gouraud",
        cmap=_DIFF_CMAP,
        vmin=-diff_range_db,
        vmax=diff_range_db,
    )
    ax.set_xlabel("time [s]")
    ax.set_ylabel("frequency [Hz]")
    ax.set_title(
        "diff = candidate - target (dB)\n" + _params_line(fft_size, hop_length, window)
    )
    fig.colorbar(
        mesh, ax=ax, label=f"diff dB (vmin={-diff_range_db:g}, vmax={diff_range_db:g})"
    )
    fig.tight_layout()
    return fig, mesh


def _save_fig(fig, path: Path) -> None:
    fig.savefig(path, dpi=_DPI, metadata=_PNG_METADATA)
    plt.close(fig)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def render_pair(
    target_path: str | Path,
    candidate_path: str | Path,
    out_dir: str | Path,
    *,
    fft_size: int = 2048,
    hop_length: int = 512,
    window: str = "hann",
    db_min: float = -80.0,
    db_max: float = 0.0,
    diff_range_db: float = 40.0,
    with_diff: bool = True,
) -> dict:
    """target/candidate の2つのWAVから、対になるスペクトログラム画像を生成する。

    `out_dir` 直下に `target.png` / `candidate.png`（と `with_diff=True` なら
    `diff.png`）、および `metadata.json`（描画パラメータとSHA256）を書き出す。

    同一環境・同一引数で2回呼び出すと、生成されるすべてのファイルがビット単位で
    一致する（PNGメタデータからタイムスタンプ等を除去しているため）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    target_buf = to_mono(read_wav(target_path))
    candidate_buf = to_mono(read_wav(candidate_path))
    require_same_sample_rate(target_buf, candidate_buf)
    if target_buf.num_samples != candidate_buf.num_samples:
        raise AudioLengthMismatchError(
            "target と candidate のサンプル数が一致しない: "
            f"target={target_buf.num_samples}, candidate={candidate_buf.num_samples}。"
            "STFTグリッドがずれるため、暗黙の切り詰め・パディングはせず停止する。"
        )

    sample_rate = target_buf.sample_rate
    grid_target = _compute_spectrogram_db(
        target_buf.data[:, 0], sample_rate, fft_size, hop_length, window
    )
    grid_candidate = _compute_spectrogram_db(
        candidate_buf.data[:, 0], sample_rate, fft_size, hop_length, window
    )

    target_png = out_dir / "target.png"
    candidate_png = out_dir / "candidate.png"
    fig, _ = _render_panel(
        grid_target,
        title="target",
        fft_size=fft_size,
        hop_length=hop_length,
        window=window,
        db_min=db_min,
        db_max=db_max,
    )
    _save_fig(fig, target_png)
    fig, _ = _render_panel(
        grid_candidate,
        title="candidate",
        fft_size=fft_size,
        hop_length=hop_length,
        window=window,
        db_min=db_min,
        db_max=db_max,
    )
    _save_fig(fig, candidate_png)

    files = {"target": "target.png", "candidate": "candidate.png", "diff": None}
    sha256 = {"target": _sha256(target_png), "candidate": _sha256(candidate_png)}

    if with_diff:
        diff_png = out_dir / "diff.png"
        fig, _ = _render_diff_panel(
            grid_target,
            grid_candidate,
            fft_size=fft_size,
            hop_length=hop_length,
            window=window,
            diff_range_db=diff_range_db,
        )
        _save_fig(fig, diff_png)
        files["diff"] = "diff.png"
        sha256["diff"] = _sha256(diff_png)

    nyquist_hz = float(grid_target.freqs[-1]) if grid_target.freqs.size else 0.0
    duration_s = float(grid_target.times[-1]) if grid_target.times.size else 0.0

    metadata = {
        "fft_size": fft_size,
        "hop_length": hop_length,
        "window": window,
        "db_min": db_min,
        "db_max": db_max,
        "diff_range_db": diff_range_db if with_diff else None,
        "sample_rate": sample_rate,
        "num_freq_bins": int(grid_target.freqs.size),
        "num_frames": int(grid_target.times.size),
        "freq_range_hz": [0.0, nyquist_hz],
        "time_range_s": [0.0, duration_s],
        "files": files,
        "sha256": sha256,
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return metadata


__all__ = [
    "AudioLengthMismatchError",
    "SpectrogramGrid",
    "render_pair",
]
