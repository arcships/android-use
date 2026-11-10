#!/usr/bin/env python3
"""Command line for the hot-swap engine in src/android_use/hotswap, defaulting to the lab app."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from android_use.hotswap.engine import main  # noqa: E402

if __name__ == "__main__":
    main(default_project=str(HERE / "app"))
