"""Build a local-plugin ZIP from an explicit source allowlist (no device state)."""

import json
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / ".codex-plugin/plugin.json").read_text())
    output = root / "dist" / f"android-use-{manifest['version']}-plugin.zip"
    output.parent.mkdir(exist_ok=True)
    files = [
        root / name
        for name in (
            "README.md",
            "README.zh-CN.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "pyproject.toml",
            "uv.lock",
            ".mcp.json",
            ".gitignore",
            ".codex-plugin/plugin.json",
        )
    ]
    for directory in ("src", "skills", "scripts", "docs", "tests", "ui"):
        files.extend(
            p
            for p in (root / directory).rglob("*")
            if p.is_file()
            and not {
                "__pycache__",
                "node_modules",
                "test-results",
                "playwright-report",
            }.intersection(p.parts)
        )
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(root))
    expanded = output.parent / "android-use"
    if expanded.exists():
        shutil.rmtree(expanded)
    with ZipFile(output) as archive:
        archive.extractall(expanded)
    print(output)


if __name__ == "__main__":
    main()
