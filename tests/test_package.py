import json
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile


def test_release_archive_is_an_installable_marketplace_without_local_state(tmp_path):
    root = tmp_path / "release source"
    script = root / "scripts/package.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/package.py", script)
    files = {
        "README.md": "English",
        "README.zh-CN.md": "中文",
        "LICENSE": "MIT",
        "THIRD_PARTY_NOTICES.md": "notices",
        "pyproject.toml": "",
        "uv.lock": "",
        ".mcp.json": "{}",
        ".gitignore": "",
        ".env.example": "TYPESAFE_API_KEY=\n",
        ".codex-plugin/plugin.json": json.dumps({"name": "android-use", "version": "0.3.0"}),
        "src/android_use/assets/phone-screen.html": "compiled UI",
        "skills/android-use/SKILL.md": "skill",
        "docs/guide.md": "guide",
        "docs/images/hero.png": "documentation asset",
        "ui/node_modules/dependency/index.js": "excluded",
        "ui/test-results/screen.png": "excluded",
        "src/android_use/__pycache__/module.pyc": "excluded",
        ".env": "excluded",
        "config.json": "private device state",
        ".agents/plugins/marketplace.json": "development source must not leak",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run([sys.executable, str(script)], check=True, capture_output=True, timeout=10)
    with ZipFile(root / "dist/android-use-0.3.0-plugin.zip") as archive:
        names = set(archive.namelist())
        marketplace = json.loads(archive.read(".agents/plugins/marketplace.json"))
        assert marketplace["name"] == "android-use-release"
        entry = marketplace["plugins"][0]
        assert entry["name"] == "android-use"
        assert entry["source"] == {"source": "local", "path": "./"}
        assert {
            "README.md",
            "README.zh-CN.md",
            "docs/guide.md",
            "src/android_use/assets/phone-screen.html",
            ".codex-plugin/plugin.json",
            ".env.example",
        } <= names
        assert not any(
            set(Path(n).parts) & {"node_modules", "__pycache__", "test-results"} for n in names
        )
        assert not {".env", "config.json"} & names
        expanded = root / "dist/android-use"
        assert (
            json.loads((expanded / ".agents/plugins/marketplace.json").read_text()) == marketplace
        )
        assert (expanded / entry["source"]["path"] / ".codex-plugin/plugin.json").is_file()
