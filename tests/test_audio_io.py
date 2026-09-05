"""harness.audio_io のテスト。

WAVフィクスチャは本テスト内で都度生成する（既知解テスト用の汎用フィクスチャ
生成器はP0-04のスコープであり、ここではP0-02の完了条件に必要な最小限の
生成のみを行う）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from harness.audio_io import (
    AudioBuffer,
    SampleRateMismatchError,
    read_wav,
    require_same_sample_rate,
    to_mono,
)

SAMPLE_RATE = 8000


def _write_wav(path: Path, data: np.ndarray, sample_rate: int, subtype: str) -> None:
    sf.write(str(path), data, sample_rate, subtype=subtype)


def _mono_sine(num_samples: int) -> np.ndarray:
    t = np.arange(num_samples) / SAMPLE_RATE
    return 0.5 * np.sin(2 * np.pi * 220.0 * t)


@pytest.mark.parametrize(
    "subtype",
    ["PCM_16", "PCM_24", "FLOAT"],
)
def test_read_wav_supports_all_bit_depths(tmp_path: Path, subtype: str) -> None:
    num_samples = 400
    data = _mono_sine(num_samples)
    wav_path = tmp_path / f"mono_{subtype}.wav"
    _write_wav(wav_path, data, SAMPLE_RATE, subtype)

    buffer = read_wav(wav_path)

    assert isinstance(buffer, AudioBuffer)
    assert buffer.sample_rate == SAMPLE_RATE
    assert buffer.channels == 1
    assert buffer.num_samples == num_samples
    assert buffer.data.shape == (num_samples, 1)


def test_read_wav_reports_stereo_channel_count(tmp_path: Path) -> None:
    num_samples = 300
    left = _mono_sine(num_samples)
    right = left * 0.5
    stereo = np.stack([left, right], axis=1)
    wav_path = tmp_path / "stereo.wav"
    _write_wav(wav_path, stereo, SAMPLE_RATE, "PCM_16")

    buffer = read_wav(wav_path)

    assert buffer.channels == 2
    assert buffer.num_samples == num_samples
    assert buffer.data.shape == (num_samples, 2)


def test_read_wav_is_bit_exact_across_repeated_reads(tmp_path: Path) -> None:
    data = _mono_sine(500)
    wav_path = tmp_path / "repeat.wav"
    _write_wav(wav_path, data, SAMPLE_RATE, "PCM_24")

    first = read_wav(wav_path)
    second = read_wav(wav_path)

    assert np.array_equal(first.data, second.data)
    assert first.sample_rate == second.sample_rate
    assert first.channels == second.channels
    assert first.num_samples == second.num_samples


def test_to_mono_averages_channels(tmp_path: Path) -> None:
    num_samples = 200
    left = np.full(num_samples, 1.0)
    right = np.full(num_samples, -1.0)
    stereo = np.stack([left, right], axis=1)
    wav_path = tmp_path / "stereo_const.wav"
    # FLOAT: 量子化誤差なしで ±1.0 を厳密に表現できる（平均が厳密に0になることを見たいため）
    _write_wav(wav_path, stereo, SAMPLE_RATE, "FLOAT")

    buffer = read_wav(wav_path)
    mono = to_mono(buffer)

    assert mono.channels == 1
    assert mono.num_samples == num_samples
    # (1.0 + -1.0) / 2 == 0.0 と厳密に一致するはず
    assert np.allclose(mono.data, 0.0)


def test_to_mono_is_noop_for_already_mono_buffer() -> None:
    data = np.zeros((10, 1))
    buffer = AudioBuffer(sample_rate=SAMPLE_RATE, channels=1, num_samples=10, data=data)

    result = to_mono(buffer)

    assert result is buffer


def test_require_same_sample_rate_passes_when_equal() -> None:
    a = AudioBuffer(sample_rate=44100, channels=1, num_samples=1, data=np.zeros((1, 1)))
    b = AudioBuffer(sample_rate=44100, channels=1, num_samples=1, data=np.zeros((1, 1)))

    require_same_sample_rate(a, b)  # 例外が出なければ成功


def test_require_same_sample_rate_raises_on_mismatch_without_resampling() -> None:
    target = AudioBuffer(sample_rate=44100, channels=1, num_samples=1, data=np.zeros((1, 1)))
    candidate = AudioBuffer(sample_rate=48000, channels=1, num_samples=1, data=np.zeros((1, 1)))

    with pytest.raises(SampleRateMismatchError):
        require_same_sample_rate(target, candidate)


def test_audio_buffer_rejects_shape_mismatch() -> None:
    with pytest.raises(ValueError):
        AudioBuffer(sample_rate=44100, channels=2, num_samples=10, data=np.zeros((10, 1)))
