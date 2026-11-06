"""Fetch and verify upstream build inputs once, before native CI fan-out."""

import argparse
import json
from pathlib import Path

from build_runtime import ROOT, locked_input

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    lock = json.loads((ROOT / "scripts/runtime-inputs.json").read_text())
    for item in [lock["ffmpeg"], *lock["adb"].values()]:
        print(locked_input(args.cache, item).name)
