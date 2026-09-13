"""luthier-render（エンジンコア骨格＋ヘッドレスCLIレンダラ、P1-06 #51）が出力する
WAVが harness の音声I/O層（`harness/audio_io.py`）で読めることを検証する。

このテストはCLIバイナリをsubprocessから直接起動する（docs/adr/0006
「ハーネスはビルド済みバイナリのパスを明示的な設定（環境変数または引数）で
受け取り」と同じ流儀）。ただし `corpus/manifest.json` の `candidate_path`
seam の差し替え（ハーネスをレンダラの出力に正式に繋ぐこと）はP1-07の範囲で
あり、本テストはそれを行わない。ここではあくまで「CLIが吐くWAVがharnessの
規則に適合しているか」だけを確認する。

ビルド済みバイナリのパスは環境変数 LUTHIER_RENDER_BIN で受け取る。未設定、
またはバイナリが存在しない環境（エンジンをビルドしていないローカル環境等）
ではこのモジュールの全テストをスキップする。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness.audio_io import read_wav

_BIN_ENV = "LUTHIER_RENDER_BIN"


def _engine_binary() -> Path | None:
    raw = os.environ.get(_BIN_ENV)
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


pytestmark = pytest.mark.skipif(
    _engine_binary() is None,
    reason=f"{_BIN_ENV} が未設定、またはバイナリが存在しない（エンジン未ビルド）",
)


def _constant_timeseries(unit: str, value: float) -> dict:
    return {"unit": unit, "interp": "linear", "points": [{"t": 0.0, "v": value}]}


def _formant_band(freq_hz: float, q: float, gain_db: float) -> dict:
    return {
        "freq": _constant_timeseries("hz", freq_hz),
        "q": _constant_timeseries("linear", q),
        "gain": _constant_timeseries("db", gain_db),
    }


def _minimal_preset() -> dict:
    return {
        "format_version": "0.1.0",
        "engine_spec_version": "0.1.0",
        "layers": {
            "transient": {
                "enabled": True,
                "gain": -6.0,
                "duration": 20.0,
                "seed": 0,
                "spectral_envelope": [_constant_timeseries("db", 0.0)],
            },
            "harmonic": {
                "enabled": True,
                "f0": _constant_timeseries("hz", 220.0),
                "partial_amplitudes": [_constant_timeseries("linear", 1.0)],
                "inharmonicity": 0.0,
            },
            "formant": {
                "enabled": True,
                "bands": [
                    _formant_band(500.0, 5.0, 0.0),
                    _formant_band(1500.0, 5.0, 0.0),
                    _formant_band(2500.0, 5.0, 0.0),
                    _formant_band(3500.0, 5.0, 0.0),
                ],
            },
        },
    }


def test_rendered_wav_is_readable_by_harness_audio_io(tmp_path: Path) -> None:
    preset_path = tmp_path / "preset.json"
    preset_path.write_text(json.dumps(_minimal_preset()), encoding="utf-8")
    output_path = tmp_path / "out.wav"
    binary = _engine_binary()
    assert binary is not None  # pytestmarkのskipifで保証済み

    result = subprocess.run(
        [str(binary), str(preset_path), str(output_path), "--sample-rate", "48000"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    buffer = read_wav(output_path)
    assert buffer.sample_rate == 48000
    assert buffer.channels == 1
    assert buffer.num_samples > 0
    # P1-08（#53）で Harmonic 層（加算合成）、P1-09（#54）で Transient 層（ノイズ）が
    # 実装されたため、minimal preset（harmonic/transient とも enabled）は正弦音＋ノイズを
    # 出す（無音ではない）。各層の無効化挙動は engine/tests/test_render.cpp の単体テストで
    # 担保し、ここでは CLI の結線（render 出力が harness の audio_io で読める）だけを握る。
    assert (buffer.data != 0.0).any()


def test_engine_renders_silence_when_all_layers_disabled(tmp_path: Path) -> None:
    preset = _minimal_preset()
    preset["layers"]["transient"]["enabled"] = False
    preset["layers"]["harmonic"]["enabled"] = False
    preset["layers"]["formant"]["enabled"] = False
    preset_path = tmp_path / "preset_disabled.json"
    preset_path.write_text(json.dumps(preset), encoding="utf-8")
    output_path = tmp_path / "out_disabled.wav"
    binary = _engine_binary()
    assert binary is not None

    result = subprocess.run(
        [str(binary), str(preset_path), str(output_path), "--sample-rate", "48000"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    buffer = read_wav(output_path)
    assert buffer.sample_rate == 48000
    assert buffer.channels == 1
    assert buffer.num_samples > 0
    # 全層を無効にしたときは出力が無音になる（各層の enabled ゲート。統合後仕様）。
    assert (buffer.data == 0.0).all()


def test_engine_stops_on_a_preset_with_an_unknown_field(tmp_path: Path) -> None:
    preset = _minimal_preset()
    preset["not_a_real_field"] = True
    preset_path = tmp_path / "preset.json"
    preset_path.write_text(json.dumps(preset), encoding="utf-8")
    output_path = tmp_path / "out.wav"
    binary = _engine_binary()
    assert binary is not None

    result = subprocess.run(
        [str(binary), str(preset_path), str(output_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    # docs/03「未知のフィールドを見つけたエンジンはエラーで停止する。黙って無視しない」
    assert result.returncode != 0
    assert not output_path.exists()
