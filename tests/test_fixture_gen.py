"""harness.fixture_gen（既知解テスト用フィクチャ生成器）のテスト。

Issue #4 の完了条件を検証する：
- CLI生成器が seed と出力先を引数に取り、6種のペアを生成する
- 同一 seed で2回生成したWAVのSHA256が一致する（決定論）
- 各ペアの「既知の差」がメタデータに値で出力され、テストがそれを参照する
- 生成物はバージョン管理に含めず、テスト実行時に一時生成される
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import butter, filtfilt

from harness.audio_io import read_wav
from harness.fixture_gen import (
    DURATION_S,
    F0_FIXED_HZ,
    F0_GLIDE_FROM_HZ,
    F0_GLIDE_TO_HZ,
    GAIN_DB,
    GLIDE_INTERVAL_S,
    LOWPASS_CUTOFF_HZ,
    NOISE_SNR_DB,
    SAMPLE_RATE,
    generate_all,
)


def _sh256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pair_meta(meta: dict, name: str) -> dict:
    for p in meta["pairs"]:
        if p["name"] == name:
            return p
    raise AssertionError(f"pair {name} not found")


def test_generate_all_produces_eight_pairs_with_metadata(tmp_path: Path) -> None:
    meta = generate_all(tmp_path, seed=0)

    names = [p["name"] for p in meta["pairs"]]
    assert set(names) == {
        "a_identical",
        "b_gain",
        "c_lowpass",
        "d_noise",
        "e_attack_shift",
        "f_f0_glide",
        "g_formant_shift",
        "h_formant_glide",
    }

    for p in meta["pairs"]:
        assert (tmp_path / p["target"]).is_file()
        assert (tmp_path / p["candidate"]).is_file()

    assert (tmp_path / "metadata.json").is_file()
    assert meta["sample_rate"] == SAMPLE_RATE
    assert meta["duration_s"] == DURATION_S


def test_same_seed_is_bit_exact_across_two_runs(tmp_path: Path) -> None:
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    meta1 = generate_all(out1, seed=12345)
    meta2 = generate_all(out2, seed=12345)

    # メタデータに含まれるSHA256自身も一致し、かつファイル実体のハッシュと一致する
    for p1, p2 in zip(meta1["pairs"], meta2["pairs"]):
        assert p1["name"] == p2["name"]
        assert p1["target_sha256"] == _sh256(out1 / p1["target"])
        assert p1["candidate_sha256"] == _sh256(out1 / p1["candidate"])
        assert _sh256(out1 / p1["target"]) == _sh256(out2 / p2["target"])
        assert _sh256(out1 / p1["candidate"]) == _sh256(out2 / p2["candidate"])

    # メタデータJSON自体もビット単位で一致する
    assert (out1 / "metadata.json").read_bytes() == (out2 / "metadata.json").read_bytes()


def test_different_seed_gives_different_wavs(tmp_path: Path) -> None:
    out1 = tmp_path / "s1"
    out2 = tmp_path / "s2"
    meta1 = generate_all(out1, seed=1)
    meta2 = generate_all(out2, seed=2)

    # 初期位相が seed 依存のため、どのペアの target も seed で変わる
    t1 = _sh256(out1 / meta1["pairs"][0]["target"])
    t2 = _sh256(out2 / meta2["pairs"][0]["target"])
    assert t1 != t2


def test_identical_pair_is_not_bit_different(tmp_path: Path) -> None:
    meta = generate_all(tmp_path, seed=0)
    p = _pair_meta(meta, "a_identical")

    target = read_wav(tmp_path / p["target"]).data
    candidate = read_wav(tmp_path / p["candidate"]).data

    assert p["known"]["relation"] == "identical"
    assert p["known"]["expected_diff"] == 0.0
    # 浮動小数WAV書き出しを経ても同一ファイルのデータはビット一致する
    assert np.array_equal(target, candidate)


def test_gain_pair_matches_known_db(tmp_path: Path) -> None:
    meta = generate_all(tmp_path, seed=0)
    p = _pair_meta(meta, "b_gain")

    target = read_wav(tmp_path / p["target"]).data[:, 0]
    candidate = read_wav(tmp_path / p["candidate"]).data[:, 0]

    assert p["known"]["gain_db"] == GAIN_DB
    scale = 10.0 ** (GAIN_DB / 20.0)
    # candidate = target * scale。FLOAT書き出しなので相対誤差は十分小さい
    assert np.allclose(candidate, target * scale, rtol=1e-5, atol=1e-6)


def test_lowpass_pair_reduces_high_frequency_energy(tmp_path: Path) -> None:
    """カットオフ以上の帯域のエネルギーが candidate で減少していることを確認する。"""
    meta = generate_all(tmp_path, seed=0)
    p = _pair_meta(meta, "c_lowpass")

    target = read_wav(tmp_path / p["target"]).data[:, 0]
    candidate = read_wav(tmp_path / p["candidate"]).data[:, 0]

    assert p["known"]["cutoff_hz"] == LOWPASS_CUTOFF_HZ

    freqs = np.fft.rfftfreq(len(target), d=1.0 / SAMPLE_RATE)
    spec_t = np.abs(np.fft.rfft(target))
    spec_c = np.abs(np.fft.rfft(candidate))

    above = freqs > LOWPASS_CUTOFF_HZ
    high_t = np.sum(spec_t[above])
    high_c = np.sum(spec_c[above])

    # カットオフ以上の高域エネルギーが明確に減る
    assert high_c < 0.5 * high_t

    # ローパス実装（butter+filtfilt、生成器と同じ）を経由した信号に一致する
    b, a = butter(4, LOWPASS_CUTOFF_HZ / (SAMPLE_RATE / 2.0), btype="low")
    expected = filtfilt(b, a, target)
    assert np.allclose(candidate, expected, atol=1e-10)


def test_noise_pair_matches_known_snr(tmp_path: Path) -> None:
    meta = generate_all(tmp_path, seed=0)
    p = _pair_meta(meta, "d_noise")

    target = read_wav(tmp_path / p["target"]).data[:, 0]
    candidate = read_wav(tmp_path / p["candidate"]).data[:, 0]

    assert p["known"]["snr_db"] == NOISE_SNR_DB

    noise = candidate - target
    rms_noise = float(np.sqrt(np.mean(noise**2.0)))
    rms_sig = float(np.sqrt(np.mean(target**2.0)))
    measured_snr_db = 20.0 * np.log10(rms_sig / rms_noise)

    assert measured_snr_db == pytest.approx(NOISE_SNR_DB, abs=0.1)


def test_attack_shift_pair_has_known_onset_delay(tmp_path: Path) -> None:
    """candidate の先頭に既知の無声が入り、立ち上がりが遅れることを確認する。"""
    meta = generate_all(tmp_path, seed=0)
    p = _pair_meta(meta, "e_attack_shift")

    target = read_wav(tmp_path / p["target"]).data[:, 0]
    candidate = read_wav(tmp_path / p["candidate"]).data[:, 0]

    delay_samples = round(p["known"]["onset_shift_s"] * SAMPLE_RATE)

    # 初めて信号が閾値を超えるサンプル位置の差が delay に一致する（無音区間は正確にゼロ）
    threshold = 1e-3
    first_t = int(np.argmax(np.abs(target) > threshold))
    first_c = int(np.argmax(np.abs(candidate) > threshold))
    assert first_c - first_t == delay_samples
    # 先頭 delay サンプルは厳密に無声
    assert np.all(candidate[:delay_samples] == 0.0)


def _dominant_freq(data: np.ndarray, lo_hz: float, hi_hz: float) -> float:
    """指定帯域内でピークを探して支配周波数（Hz）を返す。"""
    freqs = np.fft.rfftfreq(len(data), d=1.0 / SAMPLE_RATE)
    spec = np.abs(np.fft.rfft(data))
    band = (freqs >= lo_hz) & (freqs <= hi_hz)
    return freqs[band][np.argmax(spec[band])]


def test_f0_glide_pair_has_known_fundamental(tmp_path: Path) -> None:
    """固定f0とグライドf0の両方が既知の基本周波数を持つことをFFTで確認する。"""
    meta = generate_all(tmp_path, seed=0)
    p = _pair_meta(meta, "f_f0_glide")

    target = read_wav(tmp_path / p["target"]).data[:, 0]
    candidate = read_wav(tmp_path / p["candidate"]).data[:, 0]

    known = p["known"]
    f0_fixed = known["f0_fixed_hz"]
    f0_start = known["f0_glide_from_hz"]
    f0_end = known["f0_glide_to_hz"]
    glide_from_s, glide_to_s = known["glide_interval_s"]

    # メタデータの既知の差が、fixture_gen の公開定数と一致している（ドリフト検出）
    assert f0_fixed == F0_FIXED_HZ
    assert f0_start == F0_GLIDE_FROM_HZ
    assert f0_end == F0_GLIDE_TO_HZ
    assert (glide_from_s, glide_to_s) == GLIDE_INTERVAL_S

    # 固定f0ペアの target：どの帯域でも f0 が支配的
    assert _dominant_freq(target, 150.0, 400.0) == pytest.approx(f0_fixed, abs=5.0)

    # グライドペアの candidate：区間前は start、区間後は end
    win = int(0.08 * SAMPLE_RATE)  # 80ms窓
    t = np.arange(len(candidate)) / SAMPLE_RATE

    early = candidate[(t >= 0.0) & (t < glide_from_s - 0.01)]
    late = candidate[t > glide_to_s + 0.01]

    assert _dominant_freq(early[:win], 150.0, 400.0) == pytest.approx(f0_start, abs=8.0)
    assert _dominant_freq(late[:win], 200.0, 500.0) == pytest.approx(f0_end, abs=8.0)

def test_same_seed_is_bit_exact_across_separate_processes(tmp_path: Path) -> None:
    """別プロセスのCLI実行でも、同一 seed ならWAVとmetadataがビット一致する。

    これは libsndfile の PEAK chunk（浮動小数演算で1ULP揺れる）が除去されていることを
    クロスプロセスで実証するためのテスト。単一プロセス内の `generate_all` 呼び出しでは
    この欠陥が現れないため、サブプロセスとして実装する。
    """
    import subprocess
    import sys

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"

    for out in (out1, out2):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "harness",
                "generate-fixtures",
                "--seed",
                "12345",
                "--out",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

    meta1 = json.loads((out1 / "metadata.json").read_text(encoding="utf-8"))
    meta2 = json.loads((out2 / "metadata.json").read_text(encoding="utf-8"))

    assert [p["name"] for p in meta1["pairs"]] == [p["name"] for p in meta2["pairs"]]

    # 全ペアの target / candidate をクロスプロセス比較
    for p1, p2 in zip(meta1["pairs"], meta2["pairs"]):
        assert p1["name"] == p2["name"]
        assert (out1 / p1["target"]).read_bytes() == (out2 / p2["target"]).read_bytes()
        assert (out1 / p1["candidate"]).read_bytes() == (out2 / p2["candidate"]).read_bytes()

    # metadata.json 自体もビット一致
    assert (out1 / "metadata.json").read_bytes() == (out2 / "metadata.json").read_bytes()
