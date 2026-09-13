"""harness.cli のテスト。

完了条件：エントリポイントを `--help` 付きで起動すると終了コード0を返す。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from harness.cli import main


def test_main_help_exits_zero() -> None:
    # argparse は --help を検出すると内部で sys.exit(0) を呼ぶため、
    # main() からの戻り値ではなく SystemExit の code を見る。
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0


def test_main_no_args_prints_help_and_exits_zero() -> None:
    exit_code = main([])

    assert exit_code == 0


def test_module_entrypoint_help_exits_zero() -> None:
    # `python -m harness --help` としての起動確認（サブプロセス経由）。
    # `--help` の出力は日本語（parserのdescription/help文言）を含み、CLIは常にUTF-8で
    # 出力する（Issue #111）。`encoding`を指定しない場合、decodeにOSロケール依存の
    # エンコーディングが使われ、UTF-8出力とロケールが一致しない環境（例:日本語版Windows
    # のcp932）でUnicodeDecodeErrorになる。
    result = subprocess.run(
        [sys.executable, "-m", "harness", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0


def test_inspect_subcommand_reads_wav(tmp_path: Path) -> None:
    wav_path = tmp_path / "sample.wav"
    data = np.zeros((100, 1))
    sf.write(str(wav_path), data, 44100, subtype="PCM_16")

    exit_code = main(["inspect", str(wav_path)])

    assert exit_code == 0
