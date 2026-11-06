"""Build a native standalone runtime and a dependency-free Dim bootstrap plugin.

Run on each target OS/architecture, with locked Python deps and PyInstaller installed.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from android_use import __version__  # noqa: E402
from android_use.portable import fetch, target  # noqa: E402

CONFIGURE = [
    "--disable-autodetect",
    "--disable-network",
    "--disable-doc",
    "--disable-debug",
    "--disable-shared",
    "--enable-static",
    "--disable-x86asm",
    "--disable-everything",
    "--enable-ffmpeg",
    "--enable-decoder=h264",
    "--enable-parser=h264",
    "--enable-demuxer=h264",
    "--enable-encoder=mjpeg",
    "--enable-muxer=image2pipe",
    "--enable-protocol=pipe",
    "--enable-filter=select,scale,format",
]
if os.name == "nt":
    CONFIGURE.append("--extra-ldflags=-static")


def run(args, cwd=None):
    subprocess.run([str(a) for a in args], cwd=cwd, check=True, stdout=sys.stderr)


def locked_input(cache, item):
    path = cache / item["filename"]
    if path.exists():
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == item["sha256"]:
                return path
    temporary = cache / (item["filename"] + ".download-" + uuid.uuid4().hex)
    try:
        for attempt in range(3):
            try:
                fetch(item["url"], temporary)
                break
            except (OSError, httpx.TransportError):
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
        with temporary.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise ValueError("Build input checksum mismatch: " + path.name)
        os.replace(temporary, path)
        return path
    finally:
        temporary.unlink(missing_ok=True)


def archive_folder(folder, destination):
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder))


def build(output, cache, ffmpeg_binary=None):
    from android_use.installation import probe

    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    lock = json.loads((ROOT / "scripts/runtime-inputs.json").read_text())
    plat = target()
    host = plat.split("-")[0]
    adb_zip = locked_input(cache, lock["adb"][host])
    ffmpeg_source = locked_input(cache, lock["ffmpeg"])
    work = cache / ("build-" + plat)
    work.mkdir(exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    if ffmpeg_binary is None:
        source = work / "ffmpeg-8.0"
        if not source.exists():
            with tarfile.open(ffmpeg_source) as archive:
                archive.extractall(work, filter="data")
        ffmpeg_binary = source / ("ffmpeg" + suffix)
        if not ffmpeg_binary.exists():
            run(
                [
                    os.environ.get("ANDROID_USE_BUILD_BASH") or shutil.which("bash"),
                    "./configure",
                    *CONFIGURE,
                ],
                cwd=source,
            )
            run([shutil.which("make"), "-j4", "ffmpeg"], cwd=source)
    config = subprocess.run(
        [str(ffmpeg_binary), "-buildconf"], capture_output=True, text=True, check=True
    )
    configuration = (config.stdout + config.stderr).replace("'", "").replace('"', "")
    if not all(flag in configuration for flag in CONFIGURE) or any(
        flag in configuration for flag in ["--enable-gpl", "--enable-nonfree"]
    ):
        raise ValueError("FFmpeg must be built with the locked minimal configuration")
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--name",
            "android-use-runtime",
            "--distpath",
            work / "dist",
            "--workpath",
            work / "pyinstaller",
            "--specpath",
            work,
            "--paths",
            ROOT / "src",
            "--collect-all",
            "android_use",
            "--collect-data",
            "mcp",
            "--collect-submodules",
            "mcp.server",
            "--collect-all",
            "uiautomator2",
            "--copy-metadata",
            "android-use",
            ROOT / "scripts/frozen_entry.py",
        ]
    )
    bundle = work / "bundle"
    if bundle.exists():
        shutil.rmtree(bundle)
    shutil.copytree(work / "dist/android-use-runtime", bundle / "app", symlinks=False)
    with ZipFile(adb_zip) as archive:
        archive.extractall(work / "adb")
    shutil.copytree(work / "adb/platform-tools", bundle / "tools/adb", dirs_exist_ok=True)
    (bundle / "tools/adb" / ("adb" + suffix)).chmod(0o755)
    shutil.copyfile(ffmpeg_binary, bundle / "tools" / ("ffmpeg" + suffix))
    (bundle / "tools" / ("ffmpeg" + suffix)).chmod(0o755)
    ocr_archive = None
    if host == "macos":
        component = work / "ocr-component"
        if component.exists():
            shutil.rmtree(component)
        component.mkdir()
        run(["/usr/bin/swiftc", ROOT / "src/android_use/assets/ocr.swift", "-o", component / "ocr"])
        (component / "ocr.json").write_text(
            json.dumps({"schema": 1, "api": "vision-v1", "platform": plat, "version": __version__})
        )
        shutil.copyfile(ROOT / "LICENSE", component / "LICENSE")
        ocr_archive = output / f"android-use-ocr-{plat}.zip"
        archive_folder(component, ocr_archive)
    licenses = bundle / "licenses"
    licenses.mkdir()
    for filename in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(ROOT / filename, licenses / filename)
    # Carry corresponding FFmpeg source and exact configure options with the binary.
    shutil.copyfile(ffmpeg_source, licenses / ffmpeg_source.name)
    (licenses / "ffmpeg-build.json").write_text(
        json.dumps({"source": lock["ffmpeg"], "configure": CONFIGURE}, indent=2)
    )
    import sysconfig

    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ValueError("CPython license missing from build interpreter")
    shutil.copyfile(python_license, licenses / "CPython-LICENSE.txt")
    # Include license metadata for every installed Python distribution in the build env.
    import importlib.metadata

    for distribution in importlib.metadata.distributions():
        for file in distribution.files or []:
            if any(
                part.lower()
                in {"licenses", "license", "license.txt", "copying", "notice", "notice.txt"}
                for part in file.parts
            ):
                path = Path(distribution.locate_file(file))
                if path.is_file():
                    destination = licenses / "python" / str(file)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
    info = {
        "schema": 1,
        "version": __version__,
        "platform": plat,
        "inputs": lock,
        "python": sys.version.split()[0],
        "pyinstaller": importlib.metadata.version("pyinstaller"),
    }
    (bundle / "runtime.json").write_text(json.dumps(info, indent=2))
    entry = bundle / "app" / ("android-use-runtime" + suffix)
    run([entry, "health"])
    probe([str(entry), "serve"], os.environ.copy(), __version__)
    runtime_zip = output / f"android-use-runtime-{plat}.zip"
    archive_folder(bundle, runtime_zip)
    plugin = work / "plugin"
    if plugin.exists():
        shutil.rmtree(plugin)
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / "scripts").mkdir()
    shutil.copytree(ROOT / "skills", plugin / "skills")
    shutil.copyfile(ROOT / "LICENSE", plugin / "LICENSE")
    (plugin / "README.md").write_text(
        "# Android Use for Dim Agent\n\nThis lightweight plugin automatically installs a standalone runtime on first use.\nPython, ADB, ffmpeg and the screen UI are included in that runtime.\n\nDocumentation: https://github.com/arcships/android-use#quick-start\n"
    )
    (plugin / "docs").mkdir()
    for name in (
        "guide.md",
        "guide.zh-CN.md",
        "jev.md",
        "jev.zh-CN.md",
        "upgrading.md",
        "standalone.md",
    ):
        if (ROOT / "docs" / name).exists():
            shutil.copyfile(ROOT / "docs" / name, plugin / "docs" / name)
    for name in ("runtime.sh", "runtime.ps1"):
        shutil.copyfile(ROOT / "scripts" / name, plugin / "scripts" / name)
    manifest = {
        "name": "android-use",
        "version": __version__,
        "description": "Android control with an automatically provisioned standalone runtime",
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "interface": {"displayName": "Android Use"},
    }
    (plugin / ".codex-plugin/plugin.json").write_text(json.dumps(manifest, indent=2))
    if host == "windows":
        command, args = (
            "powershell.exe",
            [
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                "${CLAUDE_PLUGIN_ROOT}/scripts/runtime.ps1",
            ],
        )
    else:
        command, args = "/bin/sh", ["${CLAUDE_PLUGIN_ROOT}/scripts/runtime.sh"]
    (plugin / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"android_use": {"type": "stdio", "command": command, "args": args}}},
            indent=2,
        )
    )
    plugin_zip = output / f"android-use-{__version__}-{plat}-dim-plugin.zip"
    archive_folder(plugin, plugin_zip)
    checksums = []
    for archive in [runtime_zip, plugin_zip, *([ocr_archive] if ocr_archive else [])]:
        checksums.append(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name)
    (output / (plat + "-SHA256SUMS")).write_text("\n".join(checksums) + "\n")
    print(runtime_zip)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/runtime")
    parser.add_argument("--cache", type=Path, default=ROOT / "dist/runtime-build")
    parser.add_argument("--ffmpeg-binary", type=Path)
    args = parser.parse_args()
    build(args.output.resolve(), args.cache.resolve(), args.ffmpeg_binary)
