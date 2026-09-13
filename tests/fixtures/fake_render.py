"""テスト用の偽レンダラ（P1-07 #52 の実行を単体/CLIテストから検証するためのスタブ）。

`harness.corpus_runner.run_corpus` / CLI `run-corpus` がレンダラを subprocess として
起動する流儀を共有する。olsサンプル:
    fake_render <preset.json> <out.wav> --sample-rate <N>

本物の `luthier-render` と同じインタフェース（位置引数2つ + `--sample-rate`）をとり、
次の3つの挙動を模す。

- プリセットに `"__fail__": true` が含まれる場合 → 標準エラーにメッセージを出して
  非0で終了する（本物のエンジンは未知フィールド（docs/03）でエラー停止するため、
  `__fail__` は「エンジンがスキーマ違反プリセットで停止する」挙動の再現になる）。
- プリセットに `"__fail_utf8_ja__": true` が含まれる場合 →
  UTF-8の日本語メッセージを標準エラーに書いて非0で終了する（Issue #122）。
  本物のエンジンは `/utf-8`（MSVC）でビルドされ、実行環境のロケールに関わらず
  常にUTF-8で日本語メッセージを書く（`engine/cli/src/main.cpp`）。この挙動を
  ロケールに依存せず再現するため、`print()`（子プロセス自身の既定エンコーディング
  に依存する）ではなく `sys.stderr.buffer` へUTF-8バイト列を直接書き込む。
- それ以外 → ターゲットSRと同SRの2秒無音WAVを書き出して0で終了する。
  実際のP1-06時点のレンダラが無音を出す（docs/02「この時点では4層とも音を出さない」）
  ため、これで「レンダ結果をcandidateに使った指標算出」の経路を現実的に模せる。

子プロセスで実行されるスタンドアロンスクリプト（`harness` を import しない）。
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import soundfile as sf

# 本物のエンジン（engine/cli/src/main.cpp）が出す「予期しないエラー: 」相当の
# 日本語メッセージ。cp932でデコードすると不正なマルチバイト列になるバイト列を含む
# （Issue #122の実測と同じ再現条件）。
_UTF8_JA_FAILURE_MESSAGE = "予期しないエラー: 不明なフィールドです\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("preset", help="プリセットJSONのパス")
    parser.add_argument("output_wav", help="出力WAVのパス")
    parser.add_argument("--sample-rate", type=int, default=44100)
    args = parser.parse_args(argv)

    with open(args.preset, encoding="utf-8") as f:
        preset = json.load(f)
    if preset.get("__fail_utf8_ja__"):
        sys.stderr.buffer.write(_UTF8_JA_FAILURE_MESSAGE.encode("utf-8"))
        sys.stderr.buffer.flush()
        return 1
    if preset.get("__fail__"):
        print("fake_render: preset requested failure (__fail__=true)", file=sys.stderr)
        return 1

    sr = args.sample_rate
    samples = np.zeros(int(sr * 2.0), dtype=np.float64)
    sf.write(args.output_wav, samples.reshape(-1, 1), sr, subtype="FLOAT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))