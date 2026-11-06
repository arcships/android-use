"""Opt-in acceptance of built native artifacts; no phone or model calls.

The test driver uses Python/MCP. Tested executables use only their bundled runtime,
with Python, uv, adb and ffmpeg removed from PATH. Download transport is stubbed only
for the packaged bootstrap, so no unpublished release or external device is needed.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from android_use.portable import extract, target  # noqa: E402


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(command, env):
    result = subprocess.run(
        [str(p) for p in command], env=env, capture_output=True, text=True, timeout=120
    )
    return result, json.loads(result.stdout)


async def connected(command, env):
    params = StdioServerParameters(command=command[0], args=command[1:], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=30)
        ) as session:
            started = await session.initialize()
            assert len((await session.list_tools()).tools) == 26
            resource = (await session.list_resources()).resources[0]
            content = (await session.read_resource(resource.uri)).contents[0].text
            assert "Android Use" in content and len(content) > 10000
            return started.serverInfo.version


async def exercise(archive, upgrade_archive):
    passed = []
    with tempfile.TemporaryDirectory(prefix="android-use-native-acceptance-") as folder:
        folder = Path(folder)
        unpacked = folder / "download with spaces"
        extract(archive, unpacked)
        version = json.loads((unpacked / "runtime.json").read_text())["version"]
        exe = (
            unpacked
            / "app"
            / ("android-use-runtime.exe" if os.name == "nt" else "android-use-runtime")
        )
        root = folder / "stable runtime with spaces"
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("TYPESAFE_", "TEXT_MODEL_"))
            and k
            not in {
                "PYTHONPATH",
                "PYTHONHOME",
                "ANDROID_USE_OCR",
                "ANDROID_USE_FFMPEG",
                "ANDROID_USE_ADB",
            }
        }
        env.update(
            PATH=str(folder / "empty-path"),
            ANDROID_USE_STATE_DIR=str(folder / "private-state"),
            ANDROID_USE_AUTO_UPDATE="0",
        )
        result, health = run([exe, "health"], env)
        assert result.returncode == 0 and health["ok"], health
        assert {"adb", "ffmpeg"} <= health["dependencies"].keys()
        passed.append("health_without_system_python_uv_adb_ffmpeg")
        result, first = run(
            [exe, "--root", root, "install", "--archive", archive, "--sha256", digest(archive)], env
        )
        assert result.returncode == 0 and first["tool_count"] == 26, first
        config = first["mcp_config"]
        command = [config["command"], *config["args"]]
        assert await connected(command, env) == version
        passed.append("first_install_stable_stdio_and_embedded_ui_resource")
        preference = folder / "private-state/jev.json"
        preference.parent.mkdir(exist_ok=True)
        preference.write_text('{"fixture":"private-configuration-preserved"}')
        preference_before = preference.read_bytes()
        pointer = (root / "current.path").read_bytes()
        result, bad = run(
            [exe, "--root", root, "install", "--archive", archive, "--sha256", "0" * 64], env
        )
        assert result.returncode and bad["error"] == "runtime_checksum_mismatch"
        assert (root / "current.path").read_bytes() == pointer
        passed.append("corrupt_download_keeps_previous_runtime")
        # Keep an initialized old MCP session alive during promotion and rollback.
        params = StdioServerParameters(command=command[0], args=command[1:], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=30)
            ) as active:
                assert (await active.initialize()).serverInfo.version == version
                result, upgraded = run(
                    [
                        exe,
                        "--root",
                        root,
                        "install",
                        "--archive",
                        upgrade_archive,
                        "--sha256",
                        digest(upgrade_archive),
                    ],
                    env,
                )
                assert result.returncode == 0 and upgraded["previous"] == first["release"], upgraded
                assert upgraded["mcp_config"] == config
                assert len((await active.list_tools()).tools) == 26
                assert await connected(command, env) == upgraded["version"]
                passed.append("upgrade_switches_new_sessions_without_interrupting_old_session")
                shutil.rmtree(unpacked)
                assert await connected(command, env) == upgraded["version"]
                passed.append("deleted_original_extraction_does_not_break_stable_entry")
                # Fail actual native health verification by giving the archive a mismatched version.
                broken = folder / "bad-upgrade.zip"
                with (
                    ZipFile(upgrade_archive) as source,
                    ZipFile(broken, "w", ZIP_DEFLATED) as output,
                ):
                    for entry in source.infolist():
                        data = source.read(entry)
                        if entry.filename == "runtime.json":
                            info = json.loads(data)
                            info["version"] = "999.0.0"
                            data = json.dumps(info).encode()
                        output.writestr(entry, data)
                result, failed = run(
                    [*command[:-1], "install", "--archive", broken, "--sha256", digest(broken)], env
                )
                assert result.returncode and failed["error"] == "runtime_health_failed", failed
                assert await connected(command, env) == upgraded["version"]
                passed.append("native_health_failure_preserves_active_version")
                result, rolled = run([*command[:-1], "rollback"], env)
                assert result.returncode == 0 and rolled["version"] == version, rolled
                assert await connected(command, env) == version
                assert len((await active.list_tools()).tools) == 26
                assert json.loads((root / "update-policy.json").read_text())["paused"]
                passed.append("rollback_reprobes_previous_runtime_and_pauses_auto_update")
        assert preference.read_bytes() == preference_before
        passed.append("private_settings_preserved")
        result, schemas = run([*command[:-1], "cli", "tools"], env)
        assert result.returncode == 0 and "tap" in schemas
        passed.append("bundled_cli_dispatch")
        native = root / "releases" / first["release"]
        daemon = subprocess.run(
            [native / "app" / exe.name, "preview-daemon", "--help"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert daemon.returncode == 0 and "--serial" in daemon.stdout
        passed.append("frozen_preview_daemon_dispatch")
        ffmpeg = native / "tools" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        sample = ROOT / "tests/fixtures/runtime-sample.h264"
        frame = subprocess.run(
            [
                ffmpeg,
                "-loglevel",
                "error",
                "-f",
                "h264",
                "-i",
                "pipe:0",
                "-vf",
                "select=if(gte(time(0)-ld(0)\\,0.1)\\,st(0\\,time(0))*0+1\\,0),scale=out_range=full",
                "-pix_fmt",
                "yuvj420p",
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "pipe:1",
            ],
            env=env,
            input=sample.read_bytes(),
            capture_output=True,
            timeout=15,
        )
        assert frame.returncode == 0 and frame.stdout.startswith(b"\xff\xd8"), frame.stderr.decode(
            errors="replace"
        )
        passed.append("bundled_ffmpeg_h264_filter_scale_and_jpeg_decode")
        if target().startswith("macos"):
            assert not (native / "tools/ocr").exists(), "Default runtime must not include OCR"
            addon = archive.parent / f"android-use-ocr-{target()}.zip"
            result, installed_ocr = run(
                [*command[:-1], "install-ocr", "--archive", addon, "--sha256", digest(addon)], env
            )
            assert result.returncode == 0 and installed_ocr["ocr_installed"], installed_ocr
            ocr_id = (root / "ocr.path").read_text().strip()
            ocr_binary = root / "components" / ocr_id / "ocr"
            before_ocr = (root / "ocr.path").read_bytes()
            result, rejected = run(
                [*command[:-1], "install-ocr", "--archive", addon, "--sha256", "0" * 64], env
            )
            assert result.returncode and rejected["error"] == "runtime_checksum_mismatch"
            assert (root / "ocr.path").read_bytes() == before_ocr
            passed.append(
                "ocr_omitted_by_default_optional_component_verified_and_bad_update_refused"
            )
            from PIL import Image, ImageDraw

            image = Image.new("RGB", (800, 180), "white")
            ImageDraw.Draw(image).text((20, 40), "TOTAL 22.90", fill="black", font_size=65)
            path = folder / "ocr.png"
            image.save(path)
            result = subprocess.run(
                [ocr_binary, path], env=env, capture_output=True, text=True, timeout=15
            )
            assert result.returncode == 0 and "22.90" in result.stdout, result.stderr
            passed.append("precompiled_ocr_without_swift_compiler")
        # The lightweight plugin's initial download uses a deterministic local transport.
        plugin_zip = archive.parent / f"android-use-{version}-{target()}-dim-plugin.zip"
        if plugin_zip.exists():
            plugin = folder / "plugin"
            extract(plugin_zip, plugin)
            bootstrap_env = {
                **env,
                "ANDROID_USE_RUNTIME_DIR": str(folder / "bootstrap-runtime"),
                "PATH": os.environ.get("PATH", ""),
            }
            sums = folder / "SHA256SUMS"
            sums.write_text(digest(archive) + "  " + archive.name + "\n")
            if os.name == "nt":
                wrapper = folder / "bootstrap-test.ps1"

                def quote(value):
                    return str(value).replace("'", "''")

                wrapper.write_text(f"""function Invoke-WebRequest {{
param([Parameter(Position=0)][string]$Uri,[switch]$UseBasicParsing,[int]$TimeoutSec,[string]$OutFile)
if ($Uri -eq 'https://github.com/arcships/android-use/releases/latest/download/SHA256SUMS') {{ Copy-Item -LiteralPath '{quote(sums)}' -Destination $OutFile }}
elseif ($Uri -eq 'https://github.com/arcships/android-use/releases/latest/download/{archive.name}') {{ Copy-Item -LiteralPath '{quote(archive)}' -Destination $OutFile }}
else {{ throw 'Unexpected download URL' }}
}}
& '{quote(plugin / "scripts/runtime.ps1")}'
""")
                launch = [
                    str(
                        Path(os.environ["SystemRoot"])
                        / "System32/WindowsPowerShell/v1.0/powershell.exe"
                    ),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(wrapper),
                ]
            else:
                fakebin = folder / "fakebin"
                fakebin.mkdir()
                curl = fakebin / "curl"
                curl.write_text(f"""#!{Path(sys.executable).resolve()}
import sys,shutil
url=next(a for a in sys.argv if a.startswith('https://'))
assert url.startswith('https://github.com/arcships/android-use/releases/latest/download/')
source={str(sums)!r} if url.endswith('/SHA256SUMS') else {str(archive)!r}
shutil.copyfile(source,sys.argv[sys.argv.index('-o')+1])
""")
                curl.chmod(0o755)
                bootstrap_env["PATH"] = str(fakebin) + ":/usr/bin:/bin"
                launch = ["/bin/sh", str(plugin / "scripts/runtime.sh")]
            assert await connected(launch, bootstrap_env) == version
            passed.append(
                "packaged_plugin_first_install_and_clean_stdio_with_stubbed_download_transport"
            )
        return {
            "platform": target(),
            "version": version,
            "upgraded_version": upgraded["version"],
            "passed": passed,
            "phone_actions": False,
            "model_calls": False,
            "runtime_system_tools_removed_from_PATH": True,
            "bootstrap_download_transport": "local stub; production URLs checked",
            "runtime_sha256": digest(archive),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--upgrade-archive", type=Path)
    args = parser.parse_args()
    result = asyncio.run(
        exercise(args.archive.resolve(), (args.upgrade_archive or args.archive).resolve())
    )
    output = args.archive.parent / "acceptance.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
