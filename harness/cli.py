"""ハーネスのエントリポイント。

観測用の合成フィクチャ（既知解テスト用ペア）を生成する `generate-fixtures`、WAVの内容を
表示する `inspect`、全体指標を算出する `metrics`（P0-05）の各サブコマンドを提供する。
区間別・帯域別・軌跡指標はP0-06以降の範囲。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness.audio_io import read_wav
from harness.fixture_gen import generate_all
from harness.metrics import compute_metrics_vector


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
        help="2つのWAVパスから全体指標を算出し、指標ベクトルJSONを標準出力に出す（P0-05）",
    )
    metrics_parser.add_argument("target_wav", type=Path, help="比較の基準となるWAVファイル")
    metrics_parser.add_argument("candidate_wav", type=Path, help="比較対象のWAVファイル")

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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "inspect":
        return _run_inspect(args.wav_path)
    if args.command == "generate-fixtures":
        return _run_fixtures(args.out, args.seed)
    if args.command == "metrics":
        return _run_metrics(args.target_wav, args.candidate_wav)

    # サブコマンド未指定時はヘルプを表示して終了する（エラー扱いにはしない）。
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
