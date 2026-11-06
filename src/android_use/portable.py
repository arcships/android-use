"""Self-contained runtime installation. No uv, Python install, phone or model required."""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from zipfile import ZipFile

from . import __version__
from .installation import InstallError, atomic_write, probe, read_json, write_json

REPOSITORY = "arcships/android-use"
MAX_DOWNLOAD = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
VERSION = r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?"
_UNSET = object()
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}")


def target():
    system = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    machine = {"arm64": "arm64", "aarch64": "arm64", "AMD64": "x86_64", "x86_64": "x86_64"}.get(
        platform.machine()
    )
    value = f"{system}-{machine}"
    if value not in {"macos-arm64", "macos-x86_64", "linux-x86_64", "windows-x86_64"}:
        raise InstallError("unsupported_runtime_platform")
    return value


def root_path():
    return (
        Path(os.environ.get("ANDROID_USE_RUNTIME_DIR", "~/.local/share/android-use/runtime"))
        .expanduser()
        .resolve()
    )


def bundle_path():
    return Path(sys.executable).resolve().parent.parent if getattr(sys, "frozen", False) else None


def configure_bundle():
    bundle = bundle_path()
    if bundle:
        suffix = ".exe" if os.name == "nt" else ""
        os.environ["ANDROID_USE_ADB"] = str(bundle / "tools/adb" / ("adb" + suffix))
        os.environ["ADBUTILS_ADB_PATH"] = os.environ["ANDROID_USE_ADB"]
        os.environ["ANDROID_USE_FFMPEG"] = str(bundle / "tools" / ("ffmpeg" + suffix))
        if platform.system() == "Darwin":
            root = Path(
                os.environ.get(
                    "ANDROID_USE_RUNTIME_DIR",
                    bundle.parent.parent if bundle.parent.name == "releases" else root_path(),
                )
            )
            os.environ["ANDROID_USE_OCR"] = str(ocr_binary(root) or bundle / "tools/ocr")


def release_id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise InstallError("invalid_runtime_pointer")
    return value


def current_id(root):
    path = root / "current.path"
    return release_id(path.read_text().strip()) if path.exists() else None


def executable(folder):
    path = (
        folder / "app" / ("android-use-runtime.exe" if os.name == "nt" else "android-use-runtime")
    )
    if not path.is_file() or not path.resolve().is_relative_to(folder.resolve()):
        raise InstallError("runtime_executable_missing")
    return path


def metadata(folder):
    value = read_json(folder / "runtime.json", {})
    if value.get("schema") != 1 or value.get("platform") != target():
        raise InstallError("runtime_platform_mismatch")
    if not isinstance(value.get("version"), str) or not re.fullmatch(VERSION, value["version"]):
        raise InstallError("invalid_runtime_version")
    executable(folder)
    return value


def status(root):
    active = current_id(root)
    if not active:
        return {"ok": True, "installed": False, "platform": target()}
    folder = root / "releases" / active
    info = metadata(folder)
    return {
        "ok": True,
        "installed": True,
        "version": info["version"],
        "platform": info["platform"],
        "release": active,
        "previous": read_json(folder / "activation.json", {}).get("previous"),
        "mcp_config": mcp_config(root),
    }


def mcp_config(root):
    if os.name == "nt":
        return {
            "command": str(root / "launcher-runtime/android-use-runtime.exe"),
            "args": ["--root", str(root), "auto-serve"],
        }
    return {"command": "/bin/sh", "args": [str(root / "launcher.sh"), "auto-serve"]}


def launchers(root, folder):
    # Written once. The only commit point is current.path; release history is immutable.
    if os.name == "nt":
        bridge = root / "launcher-runtime"
        if not bridge.exists():
            temporary = root / (".launcher-" + uuid.uuid4().hex)

            def link_or_copy(source, destination):
                try:
                    os.link(source, destination)
                except OSError:
                    shutil.copy2(source, destination)

            try:
                shutil.copytree(folder / "app", temporary, copy_function=link_or_copy)
                os.replace(temporary, bridge)
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
        return

    posix = """#!/bin/sh
set -eu
case "$0" in */*) base=${0%/*};; *) base=.;; esac
base=$(CDPATH= cd -- "$base" && pwd)
IFS= read -r release < "$base/current.path"
case "$release" in ''|*[!a-zA-Z0-9._-]*) echo 'Invalid runtime pointer' >&2; exit 1;; esac
exec "$base/releases/$release/app/android-use-runtime" --root "$base" "$@"
"""
    windows = """$ErrorActionPreference = 'Stop'
$release = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'current.path') -Raw).Trim()
if ($release -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$') { throw 'Invalid runtime pointer' }
& (Join-Path $PSScriptRoot "releases/$release/app/android-use-runtime.exe") --root $PSScriptRoot @args
exit $LASTEXITCODE
"""
    for name, text in [("launcher.sh", posix), ("launcher.ps1", windows)]:
        path = root / name
        if not path.exists():
            atomic_write(path, text.encode())


def extract(archive, destination):
    with ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > 15000 or sum(e.file_size for e in entries) > MAX_EXPANDED:
            raise InstallError("runtime_archive_too_large")
        seen = set()
        for item in entries:
            parts = item.filename.rstrip("/").split("/")
            key = item.filename.rstrip("/").casefold()
            if (
                not parts
                or any(
                    p in {"", ".", ".."}
                    or p.endswith((".", " "))
                    or p.split(".")[0].upper()
                    in {
                        "CON",
                        "PRN",
                        "AUX",
                        "NUL",
                        *[f"COM{i}" for i in range(1, 10)],
                        *[f"LPT{i}" for i in range(1, 10)],
                    }
                    for p in parts
                )
                or any(c in item.filename for c in ("\\", ":", "\x00"))
                or key in seen
                or stat.S_ISLNK(item.external_attr >> 16)
            ):
                raise InstallError("unsafe_runtime_archive")
            seen.add(key)
        for item in entries:
            path = destination.joinpath(*item.filename.rstrip("/").split("/"))
            if item.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as src, path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                path.chmod(0o755 if (item.external_attr >> 16) & 0o111 else 0o644)


def verify(folder):
    info = metadata(folder)
    entry = executable(folder)
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "ANDROID_USE_ADB",
            "ANDROID_USE_FFMPEG",
            "ANDROID_USE_OCR",
            "PYTHONPATH",
            "PYTHONHOME",
        }
    }
    result = subprocess.run(
        [str(entry), "health"], env=env, capture_output=True, text=True, timeout=30, check=True
    )
    health = json.loads(result.stdout)
    if not health.get("ok") or health.get("version") != info["version"]:
        raise InstallError("runtime_health_failed")
    return probe([str(entry), "serve"], env, info["version"])


def verify_archive(archive, digest):
    if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise InstallError("invalid_runtime_checksum")
    if archive.stat().st_size > MAX_DOWNLOAD:
        raise InstallError("runtime_archive_too_large")
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != digest.lower():
        raise InstallError("runtime_checksum_mismatch")


def activate(root, archive, digest, expected_version=None, expected_current=_UNSET):
    verify_archive(archive, digest)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "runtime.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise InstallError("runtime_upgrade_in_progress") from None
    folder = None
    promoted = False
    try:
        previous = current_id(root)
        if expected_current is not _UNSET and previous != expected_current:
            raise InstallError("runtime_changed_during_download")
        folder = root / "releases" / ("candidate-" + uuid.uuid4().hex)
        folder.mkdir(parents=True)
        extract(archive, folder)
        info = metadata(folder)
        if expected_version and info["version"] != expected_version:
            raise InstallError("runtime_release_version_mismatch")
        checked = verify(folder)
        write_json(folder / "activation.json", {"previous": previous, "sha256": digest.lower()})
        launchers(root, folder)
        atomic_write(root / "current.path", (folder.name + "\n").encode())
        promoted = True
        # A failed timestamp write must not undo a committed, verified installation.
        try:
            write_json(root / "last-check.json", {"time": time.time()})
        except OSError:
            pass
        return {**status(root), **checked, "phone_actions": False, "host_reload_required": True}
    finally:
        if folder is not None and not promoted:
            shutil.rmtree(folder, ignore_errors=True)
        lock.rmdir()


def rollback(root):
    lock = root / "runtime.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise InstallError("runtime_upgrade_in_progress") from None
    try:
        active = current_id(root)
        previous = read_json(root / "releases" / release_id(active) / "activation.json", {}).get(
            "previous"
        )
        if not previous:
            raise InstallError("no_previous_runtime")
        folder = root / "releases" / release_id(previous)
        verify(folder)
        write_json(root / "update-policy.json", {"paused": True})
        atomic_write(root / "current.path", (previous + "\n").encode())
        return status(root)
    finally:
        lock.rmdir()


def fetch(url, destination, limit=MAX_DOWNLOAD):
    import httpx

    if not url.startswith("https://"):
        raise InstallError("runtime_download_requires_https")
    asset_api = re.fullmatch(
        r"https://api.github.com/repos/arcships/android-use/releases/assets/[0-9]+", url
    )
    headers = {
        "User-Agent": "android-use-runtime",
        "Accept": "application/octet-stream"
        if asset_api or not url.startswith("https://api.github.com/")
        else "application/vnd.github+json",
    }
    token = os.environ.get("ANDROID_USE_GITHUB_TOKEN")
    if token and url.startswith(f"https://api.github.com/repos/{REPOSITORY}/"):
        headers["Authorization"] = "Bearer " + token

    def https_only(request):
        if request.url.scheme != "https":
            raise InstallError("runtime_download_requires_https")

    # HTTPX removes Authorization on cross-origin redirects and includes bundled CAs.
    with httpx.Client(
        follow_redirects=True, timeout=15, event_hooks={"request": [https_only]}
    ) as client:
        with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            with destination.open("wb") as stream:
                total = 0
                for block in response.iter_bytes(1024 * 1024):
                    total += len(block)
                    if total > limit:
                        raise InstallError("runtime_download_too_large")
                    stream.write(block)


def release_info(temporary, version=None):
    if version is not None and not re.fullmatch(VERSION, version):
        raise InstallError("invalid_runtime_version")
    endpoint = "latest" if version is None else "tags/v" + version
    release = temporary / "release.json"
    fetch(f"https://api.github.com/repos/{REPOSITORY}/releases/{endpoint}", release, 2_000_000)
    info = json.loads(release.read_text())
    tag = info.get("tag_name", "")
    if info.get("draft") or info.get("prerelease") or not re.fullmatch("v" + VERSION, tag):
        raise InstallError("invalid_runtime_release")
    if version and tag != "v" + version:
        raise InstallError("runtime_release_version_mismatch")
    return info


def download_asset(info, name, temporary):
    prefix = f"https://github.com/{REPOSITORY}/releases/download/{info['tag_name']}/"
    assets = {a["name"]: a for a in info.get("assets", [])}

    def asset_url(filename):
        asset = assets.get(filename, {})
        if asset.get("browser_download_url") != prefix + filename:
            raise InstallError("runtime_release_assets_missing")
        if os.environ.get("ANDROID_USE_GITHUB_TOKEN"):
            url = asset.get("url", "")
            if not re.fullmatch(
                r"https://api.github.com/repos/arcships/android-use/releases/assets/[0-9]+", url
            ):
                raise InstallError("invalid_private_asset_url")
            return url
        return prefix + filename

    sums = temporary / "SHA256SUMS"
    fetch(asset_url("SHA256SUMS"), sums, 1_000_000)
    matches = [
        line.split()[0]
        for line in sums.read_text().splitlines()
        if len(line.split()) == 2 and line.split()[1].lstrip("*") == name
    ]
    if len(matches) != 1:
        raise InstallError("runtime_checksum_missing")
    archive = temporary / name
    fetch(asset_url(name), archive)
    return archive, matches[0]


def update(root, version=None):
    with tempfile.TemporaryDirectory(prefix="android-use-update-") as temporary:
        temporary = Path(temporary)
        info = release_info(temporary, version)
        tag = info["tag_name"]
        installed = status(root)
        installed_version = installed.get("version")
        if installed_version == tag[1:]:
            return installed
        if installed_version and version is None:

            def numbers(text):
                return tuple(int(part) for part in re.split(r"[-+]", text)[0].split("."))

            if numbers(installed_version) >= numbers(tag[1:]):
                return installed
        archive, digest = download_asset(info, f"android-use-runtime-{target()}.zip", temporary)
        return activate(
            root,
            archive,
            digest,
            expected_version=tag[1:],
            expected_current=installed.get("release"),
        )


def update_due(root):
    try:
        last = read_json(root / "last-check.json", {}).get("time", 0)
        paused = read_json(root / "update-policy.json", {}).get("paused", False)
        return (
            not paused
            and type(last) in (int, float)
            and time.time() - last >= 86400
            and not (root / "download.lock").exists()
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False  # Auxiliary update state must never prevent serving a healthy runtime.


def auto_serve(root):
    active = release_id(current_id(root))
    if os.environ.get("ANDROID_USE_AUTO_UPDATE", "1") != "0" and update_due(root):
        # Never delay stdio initialize or replace the running process for an update.
        try:
            with (root / "update.log").open("ab") as log:
                subprocess.Popen(
                    [sys.executable, "--root", str(root), "update", "--automatic"],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                )
        except OSError:
            print(
                "Android Use: background update unavailable; keeping installed runtime.",
                file=sys.stderr,
            )
    entry = executable(root / "releases" / active)
    if os.name == "nt":
        return subprocess.call([str(entry), "serve"])
    os.execv(str(entry), [str(entry), "serve"])


def checked_update(root, version=None, automatic=False):
    root.mkdir(parents=True, exist_ok=True)
    if automatic and not update_due(root):
        return status(root)
    lock = root / "download.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise InstallError("runtime_download_in_progress") from None
    try:
        if automatic:
            last = read_json(root / "last-check.json", {}).get("time", 0)
            if (
                read_json(root / "update-policy.json", {}).get("paused")
                or time.time() - last < 86400
            ):
                return status(root)
            write_json(root / "last-check.json", {"time": time.time()})
        result = update(root, version)
        if not automatic:
            write_json(root / "update-policy.json", {"paused": False})
        return result
    finally:
        lock.rmdir()


def ocr_binary(root):
    try:
        pointer = root / "ocr.path"
        if not pointer.exists():
            return None
        component = root / "components" / release_id(pointer.read_text().strip())
        info = read_json(component / "ocr.json", {})
        binary = component / "ocr"
        if (
            info.get("schema") == 1
            and info.get("api") == "vision-v1"
            and info.get("platform") == target()
            and binary.is_file()
            and binary.resolve().is_relative_to(component.resolve())
        ):
            return binary
    except (OSError, ValueError, TypeError, AttributeError, InstallError):
        pass
    return None


def install_ocr(root, archive, digest):
    if not target().startswith("macos-"):
        raise InstallError("optional_ocr_requires_macos")
    verify_archive(archive, digest)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "runtime.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise InstallError("runtime_upgrade_in_progress") from None
    component = root / "components" / ("ocr-" + uuid.uuid4().hex)
    promoted = False
    try:
        component.mkdir(parents=True)
        extract(archive, component)
        info = read_json(component / "ocr.json", {})
        binary = component / "ocr"
        if (
            info.get("schema") != 1
            or info.get("api") != "vision-v1"
            or info.get("platform") != target()
            or not binary.is_file()
        ):
            raise InstallError("invalid_ocr_component")
        from PIL import Image

        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "test.png"
            Image.new("RGB", (100, 100), "white").save(image)
            result = subprocess.run(
                [str(binary), str(image)], capture_output=True, text=True, timeout=15, check=True
            )
        if not isinstance(json.loads(result.stdout), list):
            raise InstallError("ocr_component_health_failed")
        atomic_write(root / "ocr.path", (component.name + "\n").encode())
        promoted = True
        return {"ok": True, "ocr_installed": True, "host_reload_required": True}
    finally:
        if not promoted:
            shutil.rmtree(component, ignore_errors=True)
        lock.rmdir()


def download_ocr(root):
    version = metadata(root / "releases" / release_id(current_id(root)))["version"]
    with tempfile.TemporaryDirectory(prefix="android-use-ocr-download-") as folder:
        folder = Path(folder)
        info = release_info(folder, version)
        archive, digest = download_asset(info, f"android-use-ocr-{target()}.zip", folder)
        return install_ocr(root, archive, digest)


def health():
    configure_bundle()
    bundle = bundle_path()
    if not bundle:
        raise InstallError("health_requires_built_runtime")
    results = {}
    for name, variable, flag in [
        ("adb", "ANDROID_USE_ADB", "version"),
        ("ffmpeg", "ANDROID_USE_FFMPEG", "-version"),
    ]:
        result = subprocess.run(
            [os.environ[variable], flag], capture_output=True, text=True, timeout=10, check=True
        )
        results[name] = result.stdout.splitlines()[0]
    if platform.system() == "Darwin":
        results["ocr"] = (
            "installed"
            if Path(os.environ["ANDROID_USE_OCR"]).is_file()
            else "optional_not_installed"
        )
    return {"ok": True, "version": __version__, "platform": target(), "dependencies": results}


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--root":
        os.environ["ANDROID_USE_RUNTIME_DIR"] = str(Path(sys.argv[2]).expanduser().resolve())
    if os.name == "nt" and Path(sys.executable).parent.name == "launcher-runtime":
        try:
            root = root_path()
            entry = executable(root / "releases" / release_id(current_id(root)))
            return subprocess.call([str(entry), *sys.argv[1:]])
        except (OSError, ValueError, InstallError):
            print("Android Use: invalid standalone runtime entry", file=sys.stderr)
            return 1
    configure_bundle()
    parser = argparse.ArgumentParser(description="Android Use standalone runtime")
    parser.add_argument("--root", type=Path, default=root_path())
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "auto-serve", "health", "status", "rollback"):
        commands.add_parser(name)
    install = commands.add_parser("install")
    install.add_argument("--archive", type=Path, required=True)
    install.add_argument("--sha256", required=True)
    ocr = commands.add_parser("install-ocr")
    ocr.add_argument("--archive", type=Path)
    ocr.add_argument("--sha256")
    upgrade = commands.add_parser("update")
    upgrade.add_argument("--version")
    upgrade.add_argument("--automatic", action="store_true", help=argparse.SUPPRESS)
    commands.add_parser("cli").add_argument("arguments", nargs=argparse.REMAINDER)
    commands.add_parser("preview-daemon").add_argument("arguments", nargs=argparse.REMAINDER)
    # Preserve arbitrary daemon/CLI flags after these dispatch words.
    argv = sys.argv[1:]
    offset = 2 if argv[:1] == ["--root"] else 0
    if len(argv) > offset and argv[offset] in {"preview-daemon", "cli"}:
        mode = argv[offset]
        sys.argv = [sys.argv[0], *argv[offset + 1 :]]
        if mode == "preview-daemon":
            from .preview_daemon import main as dispatch
        else:
            from .cli import main as dispatch
        return dispatch()
    args = parser.parse_args(argv)
    args.root = args.root.expanduser().resolve()
    try:
        if args.command == "serve":
            from .server import main as serve

            return serve()
        if args.command == "auto-serve":
            return auto_serve(args.root)
        if args.command == "health":
            result = health()
        elif args.command == "status":
            result = status(args.root)
        elif args.command == "install":
            result = activate(args.root, args.archive, args.sha256)
        elif args.command == "install-ocr":
            if bool(args.archive) != bool(args.sha256):
                raise InstallError("ocr_archive_requires_checksum")
            result = (
                install_ocr(args.root, args.archive, args.sha256)
                if args.archive
                else download_ocr(args.root)
            )
        elif args.command == "update":
            result = checked_update(args.root, args.version, args.automatic)
        else:
            result = rollback(args.root)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        # No exception details: transports and private paths may contain credentials.
        output = sys.stderr if args.command in {"serve", "auto-serve"} else sys.stdout
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc) if isinstance(exc, InstallError) else type(exc).__name__,
                }
            ),
            file=output,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
