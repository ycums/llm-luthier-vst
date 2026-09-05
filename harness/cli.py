"""ハーネスのエントリポイント。

`inspect`（WAVの中身確認）、`generate-fixtures`（既知解テスト用の合成フィクチャ生成、P0-04）、
`metrics`（全体指標と軌跡指標の一部の算出、P0-05 / P0-08）、`spectrogram`
（スペクトログラム画像対の生成、P0-14）の各サブコマンドを提供する。区間別・帯域別指標は
P0-06 / P0-07、フォルマント軌跡距離はP0-09の範囲。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness.audio_io import read_wav
from harness.fixture_gen import generate_all
from harness.metrics import compute_metrics_vector
from harness.spectrogram import render_pair


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-luthier-harness",
        description="レンダ結果の客観評価指標を算出するハーネス（docs/04-metrics.md）。",
    )
    subparsers = parser.add_subparsers(dest="command")

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="WAVファイルを読み込み、サンプルレート・チャネル数・サンプル数を表示する",
    )
    inspect_parser.add_argument("wav_path", type=Path, help="読み込むWAVファイルのパス")

    fixture_parser = subparsers.add_parser(
        "generate-fixtures",
        help="既知解テスト用の合成フィクチャ（差が既知の音源ペア）を生成する（P0-04）",
    )
    fixture_parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="決定論的生成のシード（同じシードなら同じWAVを生成する）",
    )
    fixture_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="出力先ディレクトリ（作成される）",
    )

    metrics_parser = subparsers.add_parser(
        "metrics",
        help=(
            "2つのWAVパスから全体指標と軌跡指標の一部を算出し、"
            "指標ベクトルJSONを標準出力に出す（P0-05 / P0-08）"
        ),
    )
    metrics_parser.add_argument("target_wav", type=Path, help="比較の基準となるWAVファイル")
    metrics_parser.add_argument("candidate_wav", type=Path, help="比較対象のWAVファイル")

    spectrogram_parser = subparsers.add_parser(
        "spectrogram",
        help="2つのWAVから、対になるスペクトログラム画像（と差分）を生成する（P0-14）",
    )
    spectrogram_parser.add_argument(
        "--target", type=Path, required=True, help="変更前（target）のWAVパス"
    )
    spectrogram_parser.add_argument(
        "--candidate", type=Path, required=True, help="変更後（candidate）のWAVパス"
    )
    spectrogram_parser.add_argument(
        "--out", type=Path, required=True, help="出力先ディレクトリ（作成される）"
    )
    spectrogram_parser.add_argument(
        "--fft-size", type=int, default=2048, help="STFTのFFTサイズ（既定: 2048）"
    )
    spectrogram_parser.add_argument(
        "--hop-length", type=int, default=512, help="STFTのホップ長（既定: 512）"
    )
    spectrogram_parser.add_argument(
        "--window", type=str, default="hann", help="STFTの窓関数名（既定: hann）"
    )
    spectrogram_parser.add_argument(
        "--db-min",
        type=float,
        default=-80.0,
        help="カラースケールの下限dB（既定: -80.0）。2枚とも同じ値で描画する",
    )
    spectrogram_parser.add_argument(
        "--db-max",
        type=float,
        default=0.0,
        help="カラースケールの上限dB（既定: 0.0）。2枚とも同じ値で描画する",
    )
    spectrogram_parser.add_argument(
        "--diff-range-db",
        type=float,
        default=40.0,
        help="差分画像のカラースケール範囲（±dB、既定: 40.0）",
    )
    spectrogram_parser.add_argument(
        "--no-diff",
        action="store_true",
        help="差分画像（diff.png）を生成しない",
    )

    return parser


def _run_inspect(wav_path: Path) -> int:
    buffer = read_wav(wav_path)
    print(f"sample_rate: {buffer.sample_rate}")
    print(f"channels: {buffer.channels}")
    print(f"num_samples: {buffer.num_samples}")
    return 0


def _run_fixtures(out_dir: Path, seed: int) -> int:
    metadata = generate_all(out_dir, seed)
    print(f"fixtures generated: {len(metadata['pairs'])} pairs -> {out_dir}")
    print(f"metadata: {out_dir / 'metadata.json'}")
    return 0


def _run_metrics(target_wav: Path, candidate_wav: Path) -> int:
    vector = compute_metrics_vector(target_wav, candidate_wav)
    print(json.dumps(vector, ensure_ascii=False, indent=2))
    return 0


def _run_spectrogram(args: argparse.Namespace) -> int:
    metadata = render_pair(
        args.target,
        args.candidate,
        args.out,
        fft_size=args.fft_size,
        hop_length=args.hop_length,
        window=args.window,
        db_min=args.db_min,
        db_max=args.db_max,
        diff_range_db=args.diff_range_db,
        with_diff=not args.no_diff,
    )
    for label, filename in metadata["files"].items():
        if filename is not None:
            print(f"{label}: {args.out / filename}")
    print(f"metadata: {args.out / 'metadata.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "inspect":
        return _run_inspect(args.wav_path)
    if args.command == "generate-fixtures":
        return _run_fixtures(args.out, args.seed)
    if args.command == "metrics":
        return _run_metrics(args.target_wav, args.candidate_wav)
    if args.command == "spectrogram":
        return _run_spectrogram(args)

    # サブコマンド未指定時はヘルプを表示して終了する（エラー扱いにはしない）。
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
