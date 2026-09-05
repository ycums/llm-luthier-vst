"""harness.spectrogram（スペクトログラム画像生成ツール、P0-14）のテスト。

Issue #14 の完了条件を検証する：
- 2つのWAVパスと出力先からスペクトログラム画像の対（＋差分1枚）を生成できる
- カラースケール範囲（db_min/db_max）は引数で指定した値に固定され、
  matplotlibの自動スケーリングに任せて2枚で食い違うことがない
- 同一環境・同一入力で2回生成した画像のSHA256が一致する（決定論）
- FFTサイズ・ホップ長・窓関数がサイドカーファイル（metadata.json）に記録される
- サンプル数が異なる2音源からは差分グリッドが成立しないため停止する
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from harness.spectrogram import (
    AudioLengthMismatchError,
    _compute_spectrogram_db,
    _render_panel,
    render_pair,
)

SAMPLE_RATE = 16000
DURATION_S = 0.3
_NUM_S = int(SAMPLE_RATE * DURATION_S)


def _write_tone(path: Path, freq_hz: float, amplitude: float, num_samples: int = _NUM_S) -> None:
    t = np.arange(num_samples) / SAMPLE_RATE
    data = amplitude * np.sin(2.0 * np.pi * freq_hz * t)
    sf.write(str(path), data, SAMPLE_RATE, subtype="FLOAT")


def test_render_pair_creates_target_candidate_and_diff_images(tmp_path: Path) -> None:
    target_path = tmp_path / "target.wav"
    candidate_path = tmp_path / "candidate.wav"
    _write_tone(target_path, 440.0, 0.5)
    _write_tone(candidate_path, 440.0, 0.9)
    out_dir = tmp_path / "out"

    metadata = render_pair(target_path, candidate_path, out_dir)

    assert (out_dir / "target.png").is_file()
    assert (out_dir / "candidate.png").is_file()
    assert (out_dir / "diff.png").is_file()
    assert (out_dir / "metadata.json").is_file()
    assert metadata["files"] == {
        "target": "target.png",
        "candidate": "candidate.png",
        "diff": "diff.png",
    }


def test_metadata_records_fft_params_and_color_scale(tmp_path: Path) -> None:
    target_path = tmp_path / "target.wav"
    candidate_path = tmp_path / "candidate.wav"
    _write_tone(target_path, 440.0, 0.5)
    _write_tone(candidate_path, 440.0, 0.5)
    out_dir = tmp_path / "out"

    metadata = render_pair(
        target_path,
        candidate_path,
        out_dir,
        fft_size=1024,
        hop_length=256,
        window="hamming",
        db_min=-60.0,
        db_max=10.0,
    )

    assert metadata["fft_size"] == 1024
    assert metadata["hop_length"] == 256
    assert metadata["window"] == "hamming"
    assert metadata["db_min"] == -60.0
    assert metadata["db_max"] == 10.0
    assert metadata["sample_rate"] == SAMPLE_RATE

    on_disk = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))
    assert on_disk == metadata


def test_no_diff_flag_skips_diff_image(tmp_path: Path) -> None:
    target_path = tmp_path / "target.wav"
    candidate_path = tmp_path / "candidate.wav"
    _write_tone(target_path, 440.0, 0.5)
    _write_tone(candidate_path, 440.0, 0.5)
    out_dir = tmp_path / "out"

    metadata = render_pair(target_path, candidate_path, out_dir, with_diff=False)

    assert not (out_dir / "diff.png").exists()
    assert metadata["files"]["diff"] is None
    assert "diff" not in metadata["sha256"]
    assert metadata["diff_range_db"] is None


def test_pair_shares_identical_frequency_and_time_axes(tmp_path: Path) -> None:
    """対になる2枚は同一の周波数軸・時間軸を持つ（同一サンプルレート・同一サンプル数から）。"""
    target_path = tmp_path / "target.wav"
    candidate_path = tmp_path / "candidate.wav"
    _write_tone(target_path, 440.0, 0.2)
    _write_tone(candidate_path, 880.0, 0.9)  # 周波数・振幅とも違う信号
    out_dir = tmp_path / "out"

    render_pair(target_path, candidate_path, out_dir, fft_size=512, hop_length=128)

    target_data = sf.read(str(target_path))[0]
    candidate_data = sf.read(str(candidate_path))[0]
    grid_t = _compute_spectrogram_db(target_data, SAMPLE_RATE, 512, 128, "hann")
    grid_c = _compute_spectrogram_db(candidate_data, SAMPLE_RATE, 512, 128, "hann")

    assert np.array_equal(grid_t.freqs, grid_c.freqs)
    assert np.array_equal(grid_t.times, grid_c.times)


def test_color_scale_is_fixed_and_not_autoscaled_per_panel(tmp_path: Path) -> None:
    """`db_min`/`db_max` が両パネルで固定され、信号レベルに応じて食い違わないことを確認する。

    matplotlibの既定（vmin/vmax=None）ではデータのmin/maxに応じて自動的に
    カラースケールが決まり、振幅の違う2信号では範囲が食い違ってしまう。
    `_render_panel` が明示的に固定値を `pcolormesh` に渡していることを、
    振幅が大きく異なる2つのグリッドそれぞれについて確認する。
    """
    loud_path = tmp_path / "loud.wav"
    quiet_path = tmp_path / "quiet.wav"
    _write_tone(loud_path, 440.0, 0.9)
    _write_tone(quiet_path, 440.0, 0.01)  # loud よりおよそ40dB静か

    loud_data = sf.read(str(loud_path))[0]
    quiet_data = sf.read(str(quiet_path))[0]
    grid_loud = _compute_spectrogram_db(loud_data, SAMPLE_RATE, 1024, 256, "hann")
    grid_quiet = _compute_spectrogram_db(quiet_data, SAMPLE_RATE, 1024, 256, "hann")

    # 2つのグリッドのdBレンジは実際に大きく異なる（自動スケールなら食い違う下地があることの確認）
    assert grid_loud.db.max() - grid_quiet.db.max() > 20.0

    db_min, db_max = -80.0, 0.0
    fig_loud, mesh_loud = _render_panel(
        grid_loud,
        title="loud",
        fft_size=1024,
        hop_length=256,
        window="hann",
        db_min=db_min,
        db_max=db_max,
    )
    fig_quiet, mesh_quiet = _render_panel(
        grid_quiet,
        title="quiet",
        fft_size=1024,
        hop_length=256,
        window="hann",
        db_min=db_min,
        db_max=db_max,
    )
    try:
        assert mesh_loud.norm.vmin == db_min
        assert mesh_loud.norm.vmax == db_max
        # 振幅が大きく異なっていても、両パネルのスケールは完全に一致する（食い違わない）
        assert mesh_quiet.norm.vmin == mesh_loud.norm.vmin
        assert mesh_quiet.norm.vmax == mesh_loud.norm.vmax
    finally:
        import matplotlib.pyplot as plt

        plt.close(fig_loud)
        plt.close(fig_quiet)


def test_length_mismatch_raises_without_padding(tmp_path: Path) -> None:
    target_path = tmp_path / "target.wav"
    candidate_path = tmp_path / "candidate.wav"
    _write_tone(target_path, 440.0, 0.5, num_samples=_NUM_S)
    _write_tone(candidate_path, 440.0, 0.5, num_samples=_NUM_S + 100)

    with pytest.raises(AudioLengthMismatchError):
        render_pair(target_path, candidate_path, tmp_path / "out")


def test_same_input_and_env_is_bit_exact_across_two_runs(tmp_path: Path) -> None:
    target_path = tmp_path / "target.wav"
    candidate_path = tmp_path / "candidate.wav"
    _write_tone(target_path, 440.0, 0.5)
    _write_tone(candidate_path, 440.0, 0.9)

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    render_pair(target_path, candidate_path, out1)
    render_pair(target_path, candidate_path, out2)

    for name in ("target.png", "candidate.png", "diff.png", "metadata.json"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()


def test_same_input_is_bit_exact_across_separate_processes(tmp_path: Path) -> None:
    """別プロセスのCLI実行でも、同一入力ならPNG・metadataがビット一致する。"""
    target_path = tmp_path / "target.wav"
    candidate_path = tmp_path / "candidate.wav"
    _write_tone(target_path, 440.0, 0.5)
    _write_tone(candidate_path, 440.0, 0.9)

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    for out in (out1, out2):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "harness",
                "spectrogram",
                "--target",
                str(target_path),
                "--candidate",
                str(candidate_path),
                "--out",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.returncode == 0

    for name in ("target.png", "candidate.png", "diff.png", "metadata.json"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()


def test_cli_no_diff_flag(tmp_path: Path) -> None:
    from harness.cli import main

    target_path = tmp_path / "target.wav"
    candidate_path = tmp_path / "candidate.wav"
    _write_tone(target_path, 440.0, 0.5)
    _write_tone(candidate_path, 440.0, 0.5)
    out_dir = tmp_path / "out"

    exit_code = main(
        [
            "spectrogram",
            "--target",
            str(target_path),
            "--candidate",
            str(candidate_path),
            "--out",
            str(out_dir),
            "--no-diff",
        ]
    )

    assert exit_code == 0
    assert not (out_dir / "diff.png").exists()
    assert (out_dir / "target.png").is_file()
