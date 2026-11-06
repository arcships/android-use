"""Check that a release tag matches every version source and has notes; print the title.

python3 scripts/release_check.py v0.6.1
The release body is docs/releases/<tag>.md as written; the title is its H1 plus tagline.
"""

import json
import re
import sys
import tomllib
from pathlib import Path


def versions(root):
    def read(name):
        return (root / name).read_text()

    def search(name, pattern):
        found = re.search(pattern, read(name))
        return found.group(1) if found else None

    return {
        ".codex-plugin/plugin.json": json.loads(read(".codex-plugin/plugin.json"))["version"],
        "pyproject.toml": tomllib.loads(read("pyproject.toml"))["project"]["version"],
        "uv.lock": search("uv.lock", r'name = "android-use"\nversion = "([^"]+)"'),
        "src/android_use/__init__.py": search(
            "src/android_use/__init__.py", r'__version__ = "([^"]+)"'
        ),
        "ui/package.json": json.loads(read("ui/package.json"))["version"],
        "ui/src/app.ts": search("ui/src/app.ts", r"name:'Android Use Screen',version:'([^']+)'"),
        # The compiled screen ships in the ZIP; a stale build would report the old version.
        "src/android_use/assets/phone-screen.html": search(
            "src/android_use/assets/phone-screen.html",
            r'"Android Use Screen",version:"([^"]+)"',
        ),
    }


def check(root, tag):
    match = re.fullmatch(r"v(\d+\.\d+\.\d+)", tag)
    if not match:
        return None, [f"tag {tag!r} is not vMAJOR.MINOR.PATCH"]
    version = match.group(1)
    errors = [
        f"{name} has version {found!r}, expected {version!r}"
        for name, found in versions(root).items()
        if found != version
    ]
    notes = root / "docs/releases" / f"{tag}.md"
    if not notes.is_file():
        return None, [*errors, f"missing release notes {notes.relative_to(root)}"]
    lines = [line.strip() for line in notes.read_text().splitlines() if line.strip()]
    heading = f"# Android Use {tag}"
    if not lines or lines[0] != heading:
        errors.append(f"{notes.relative_to(root)} must start with {heading!r}")
    tagline = (
        lines[1].strip("*").rstrip(".") if len(lines) > 1 and lines[1].startswith("**") else ""
    )
    title = f"Android Use {tag}" + (f" — {tagline}" if tagline else "")
    return title, errors


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    title, errors = check(Path(__file__).resolve().parents[1], sys.argv[1])
    if errors:
        sys.exit("\n".join(errors))
    print(title)


if __name__ == "__main__":
    main()
