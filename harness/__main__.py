"""`python -m harness` での起動を可能にするエントリポイント。"""

import sys

from harness.cli import main

if __name__ == "__main__":
    sys.exit(main())
