"""Run with uv-managed Python; no Android Use dependency install is required."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from android_use.installation import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
