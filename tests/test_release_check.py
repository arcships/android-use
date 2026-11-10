import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def release_tree(root, version="1.2.3", screen=None, notes=True):
    script = root / "scripts/release_check.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/release_check.py", script)
    files = {
        ".codex-plugin/plugin.json": json.dumps({"name": "android-use", "version": version}),
        ".claude-plugin/plugin.json": json.dumps({"name": "android-use", "version": version}),
        "pyproject.toml": f'[project]\nname = "android-use"\nversion = "{version}"\n',
        "uv.lock": f'[[package]]\nname = "android-use"\nversion = "{version}"\n',
        "src/android_use/__init__.py": f'__version__ = "{version}"\n',
        "ui/package.json": json.dumps({"name": "android-use-screen", "version": version}),
        "ui/src/app.ts": f"new App({{name:'Android Use Screen',version:'{version}'}},",
        "src/android_use/assets/phone-screen.html": (
            f'new X({{name:"Android Use Screen",version:"{screen or version}"}},'
        ),
    }
    if notes:
        files[f"docs/releases/v{version}.md"] = (
            f"# Android Use v{version}\n\n**Box stops guessing at text.**\n\n## What's changed\n"
        )
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return script


def run(script, tag):
    return subprocess.run(
        [sys.executable, str(script), tag], capture_output=True, text=True, timeout=10
    )


def test_matching_tag_prints_title_from_notes(tmp_path):
    result = run(release_tree(tmp_path), "v1.2.3")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Android Use v1.2.3 — Box stops guessing at text"


@pytest.mark.parametrize(
    "tree,tag,error",
    [
        ({}, "v1.2.4", "pyproject.toml has version '1.2.3', expected '1.2.4'"),
        ({}, "1.2.3", "is not vMAJOR.MINOR.PATCH"),
        ({"screen": "1.2.2"}, "v1.2.3", "phone-screen.html has version '1.2.2'"),
        ({"notes": False}, "v1.2.3", "missing release notes docs/releases/v1.2.3.md"),
    ],
)
def test_mismatches_block_the_release(tmp_path, tree, tag, error):
    result = run(release_tree(tmp_path, **tree), tag)
    assert result.returncode == 1 and error in result.stderr and not result.stdout
