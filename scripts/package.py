"""Build an installable plugin ZIP from an explicit source allowlist (no device state)."""

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
            ".env.example",
            ".codex-plugin/plugin.json",
            # Claude Code reads this manifest; its MCP entry overrides .mcp.json there.
            ".claude-plugin/plugin.json",
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
            }.intersection(p.relative_to(root).parts)
        )
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(root))
        # A downloaded archive is a complete local marketplace. Source checkouts
        # retain android-use-local -> dist/android-use; the release name keeps
        # registration separate from an existing development checkout.
        archive.writestr(
            ".agents/plugins/marketplace.json",
            json.dumps(
                {
                    "name": "android-use-release",
                    "interface": {"displayName": "Android Use Release"},
                    "plugins": [
                        {
                            "name": manifest["name"],
                            "source": {"source": "local", "path": "./"},
                            "policy": {
                                "installation": "AVAILABLE",
                                "authentication": "ON_INSTALL",
                            },
                            "category": "Productivity",
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
        )
    expanded = output.parent / "android-use"
    if expanded.exists():
        shutil.rmtree(expanded)
    with ZipFile(output) as archive:
        archive.extractall(expanded)
    print(output)


if __name__ == "__main__":
    main()
