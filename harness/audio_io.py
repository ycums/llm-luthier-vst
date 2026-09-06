"""決定論的な音声I/O層。

WAVの読み込み規則（サンプルレート・チャネル・ビット深度の扱い）をここに閉じ込める。
各指標の実装（P0-05以降）はこのモジュールを経由してのみ音声データにアクセスすること。
規則を各指標に分散させると、指標間で前処理が食い違い、指標ベクトルとしての
比較可能性が壊れる（`docs/01-architecture.md` 参照）。

## ステレオ→モノの変換規則

全チャンネルの算術平均をとる（`sum(channels) / channel_count`）。音量補正・
ラウドネス正規化の類は一切行わない。この規則はこのモジュール内の
`to_mono` にのみ実装され、他の場所で独自に再実装してはならない。

## サンプルレート不一致の扱い

ターゲットと比較対象でサンプルレートが異なる場合、暗黙にリサンプルしない。
`docs/03-preset-format.md` の「未知のフィールドは黙って無視せずエラーで停止する」
という原則をここでも踏襲し、`SampleRateMismatchError` を送出して停止する。
リサンプリングそのものの実装は本モジュールのスコープ外（必要になった時点で
別Issueにする）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

# soundfileでの読み込みに用いるdtype。float64に統一することで、
# 16bit整数 / 24bit整数 / 32bit浮動小数のいずれのWAVも同一の表現に正規化し、
# 決定論的に（同一入力に対して常に同一のビット列を）読み込めるようにする。
_READ_DTYPE = "float64"


class SampleRateMismatchError(ValueError):
    """比較対象2音源のサンプルレートが一致しないときに送出する。

    暗黙のリサンプルは行わない。呼び出し側でリサンプルするか、
    サンプルレートの揃った音源を用意すること。
    """


@dataclass(frozen=True)
class AudioBuffer:
    """読み込んだ音声データを表す単一の構造体。

    Attributes:
        sample_rate: サンプルレート（Hz）
        channels: チャネル数
        num_samples: チャネルあたりのサンプル数（フレーム数）
        data: 形状 `(num_samples, channels)` のfloat64配列
    """

    sample_rate: int
    channels: int
    num_samples: int
    data: np.ndarray

    def __post_init__(self) -> None:
        if self.data.ndim != 2:
            raise ValueError(
                f"AudioBuffer.data は2次元配列でなければならない（実際: {self.data.ndim}次元）"
            )
        if self.data.shape != (self.num_samples, self.channels):
            raise ValueError(
                "AudioBuffer.data の形状が num_samples/channels と一致しない: "
                f"data.shape={self.data.shape}, "
                f"expected=({self.num_samples}, {self.channels})"
            )


def read_wav(path: str | Path) -> AudioBuffer:
    """WAVファイルを読み込み、AudioBufferを返す。

    16bit整数 / 24bit整数 / 32bit浮動小数のいずれのサブタイプも読み込める。
    元のビット深度に関わらず、内部表現は常にfloat64に正規化する。

    同一ファイルを複数回読んでも、返る `data` はビット単位で一致する
    （`soundfile` によるデコードは決定論的であるため）。
    """
    data, sample_rate = sf.read(str(path), dtype=_READ_DTYPE, always_2d=True)
    num_samples, channels = data.shape
    return AudioBuffer(
        sample_rate=int(sample_rate),
        channels=int(channels),
        num_samples=int(num_samples),
        data=data,
    )


def read_sample_rate(path: str | Path) -> int:
    """音声ファイルのサンプルレートだけを読み取る（全サンプルは読まない）。

    `soundfile.info` を使ってヘッダのみを読む。ターゲット音源のサンプルレートに
    合わせてレンダする（`harness/corpus_runner.py`）ときに、重いデコードを
    避けてSRだけを取得するために使う。
    """
    return int(sf.info(str(path)).samplerate)


def to_mono(buffer: AudioBuffer) -> AudioBuffer:
    """ステレオ（以上）のAudioBufferをモノラルに変換する。

    変換規則：全チャンネルの算術平均（モジュールdocstring参照）。
    既にモノラルの場合はそのまま返す。
    """
    if buffer.channels == 1:
        return buffer
    mono_data = buffer.data.mean(axis=1, keepdims=True)
    return AudioBuffer(
        sample_rate=buffer.sample_rate,
        channels=1,
        num_samples=buffer.num_samples,
        data=mono_data,
    )


def require_same_sample_rate(target: AudioBuffer, candidate: AudioBuffer) -> None:
    """target と candidate のサンプルレートが一致することを要求する。

    一致しない場合、暗黙にリサンプルせず `SampleRateMismatchError` を送出する。
    """
    if target.sample_rate != candidate.sample_rate:
        raise SampleRateMismatchError(
            "サンプルレートが一致しない: "
            f"target={target.sample_rate}Hz, candidate={candidate.sample_rate}Hz。"
            "暗黙のリサンプルは行わない設計であるため停止する"
            "（docs/03-preset-format.md の「黙って無視しない」原則）。"
        )
