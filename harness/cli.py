"""ハーネスのエントリポイント。

`inspect`（WAVの中身確認）、`generate-fixtures`（既知解テスト用の合成フィクチャ生成、P0-04）、
`metrics`（全体指標・区間別指標・軌跡指標の一部の算出、P0-05/P0-06/P0-08）、`spectrogram`
（スペクトグラム画像対の生成、P0-14）、`run-corpus`（コーパス全体への指標算出の一括実行、
P0-11）の各サブコマンドを提供する。帯域別指標はP0-07、フォルマント軌跡距離はP0-09の範囲。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness.audio_io import read_wav
from harness.corpus_runner import ManifestError, run_corpus
from harness.fixture_gen import generate_all
from harness.metrics import DEFAULT_ATTACK_END_S, compute_metrics_vector
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
            "2つのWAVパスから全体指標・区間別指標・軌跡指標の一部を算出し、"
            "指標ベクトルJSONを標準出力に出す（P0-05 / P0-06 / P0-08）"
        ),
    )
    metrics_parser.add_argument("target_wav", type=Path, help="比較の基準となるWAVファイル")
    metrics_parser.add_argument("candidate_wav", type=Path, help="比較対象のWAVファイル")
    metrics_parser.add_argument(
        "--attack-end-s",
        type=float,
        default=DEFAULT_ATTACK_END_S,
        help=(
            "アタック区間の終端（秒、ノートオン=0秒）。既定値は docs/04-metrics.md が"
            "明記する仮の値（20ms＝0.02s）。妥当性は docs/06-open-questions.md の Q-009 参照"
        ),
    )
    metrics_parser.add_argument(
        "--transition-end-s",
        type=float,
        default=None,
        help=(
            "遷移部区間の終端（秒）。設定またはマニフェストの注釈として音源ごとに与える。"
            "指定しない場合、遷移部・定常部・リリースは欠測として出力される"
        ),
    )
    metrics_parser.add_argument(
        "--sustain-end-s",
        type=float,
        default=None,
        help=(
            "定常部区間の終端（秒）。設定またはマニフェストの注釈として音源ごとに与える。"
            "指定しない場合、定常部・リリースは欠測として出力される"
        ),
    )

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

    run_corpus_parser = subparsers.add_parser(
        "run-corpus",
        help=(
            "マニフェストの全エントリに指標算出を実行し、音源ごとのJSONと"
            "インデックスJSONを出力先ディレクトリに書き出す（P0-11）"
        ),
    )
    run_corpus_parser.add_argument(
        "--manifest", type=Path, required=True, help="コーパスのマニフェストJSONのパス"
    )
    run_corpus_parser.add_argument(
        "--out", type=Path, required=True, help="出力先ディレクトリ（作成される）"
    )
    run_corpus_parser.add_argument(
        "--attack-end-s",
        type=float,
        default=DEFAULT_ATTACK_END_S,
        help="アタック区間の終端（秒）。`metrics` サブコマンドと同じ既定値・意味を持つ",
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


def _run_metrics(
    target_wav: Path,
    candidate_wav: Path,
    attack_end_s: float,
    transition_end_s: float | None,
    sustain_end_s: float | None,
) -> int:
    vector = compute_metrics_vector(
        target_wav,
        candidate_wav,
        attack_end_s=attack_end_s,
        transition_end_s=transition_end_s,
        sustain_end_s=sustain_end_s,
    )
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


def _run_run_corpus(manifest: Path, out: Path, attack_end_s: float) -> int:
    try:
        index = run_corpus(manifest, out, attack_end_s=attack_end_s)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = index["summary"]
    print(
        f"entries: {index['entry_count']} "
        f"(ok={summary['ok']} missing={summary['missing']} error={summary['error']})"
    )
    print(f"index: {out / 'index.json'}")
    # 1件以上が例外で失敗した場合のみ非0（一部失敗）。欠測(missing)は正常系として扱う
    # （harness/corpus_runner.py モジュールdocstring「終了コードの意味」参照）。
    return 1 if summary["error"] > 0 else 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "inspect":
        return _run_inspect(args.wav_path)
    if args.command == "generate-fixtures":
        return _run_fixtures(args.out, args.seed)
    if args.command == "metrics":
        return _run_metrics(
            args.target_wav,
            args.candidate_wav,
            args.attack_end_s,
            args.transition_end_s,
            args.sustain_end_s,
        )
    if args.command == "spectrogram":
        return _run_spectrogram(args)
    if args.command == "run-corpus":
        return _run_run_corpus(args.manifest, args.out, args.attack_end_s)

    # サブコマンド未指定時はヘルプを表示して終了する（エラー扱いにはしない）。
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
