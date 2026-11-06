"""Build a second version in a disposable source tree for native lifecycle acceptance."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from android_use import __version__  # noqa: E402
from android_use.installation import source_files  # noqa: E402

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="android-use-upgrade-source-") as folder:
        checkout = Path(folder)
        for source, relative in source_files(ROOT):
            destination = checkout / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        init = checkout / "src/android_use/__init__.py"
        init.write_text(
            init.read_text().replace(__version__, __version__.split("+")[0] + "+upgrade-test")
        )
        subprocess.run(
            [
                sys.executable,
                str(checkout / "scripts/build_runtime.py"),
                "--cache",
                str(ROOT / "dist/runtime-build"),
                "--output",
                str(ROOT / "dist/runtime-upgrade"),
            ],
            env={**os.environ, "PYTHONPATH": str(checkout / "src")},
            check=True,
        )
