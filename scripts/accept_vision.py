"""Opt-in real-device acceptance for Jev Box location (aua_locate, tap visual), via stdio MCP.

Sends screenshots of the isolated fixture app only: every request uses a region that excludes
the status and navigation bars. Requires Jev enabled with a key; screenshot sending is enabled
for this server process only via ANDROID_USE_JEV_IMAGES=1 unless the user saved a choice.
Installs only the fixture package, uninstalls it in finally, and restores the foreground.
Run: uv run python scripts/accept_vision.py --serial <authorized-device>
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from accept_device import PACKAGE, build_app, command  # noqa: E402


def inside(point, node):
    x, y, w, h = node["rect"]
    return x <= point[0] < x + w and y <= point[1] < y + h


async def exercise(serial, apk):
    adb = os.environ.get("ANDROID_USE_ADB") or shutil.which("adb")
    if not adb:
        raise RuntimeError("ADB is required")

    def shell(*args):
        return command([adb, "-s", serial, "shell", *args])

    def launch():
        shell("am", "force-stop", PACKAGE)
        shell("am", "start", "-W", "-n", PACKAGE + "/.MainActivity", "--ez", "benchmark", "true")
        time.sleep(1.0)  # Launcher animations (e.g. HyperOS) can swallow an immediate tap.

    if f"package:{PACKAGE}" in shell("pm", "list", "packages", PACKAGE).splitlines():
        raise RuntimeError("Fixture package already exists; refusing to replace it")
    env = {**os.environ, "ANDROID_SERIAL": serial, "ANDROID_USE_JEV_IMAGES": "1"}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "android_use.server"], env=env
    )
    passed, calls = [], []
    original = None
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            async def raw(name, **args):
                response = await client.call_tool("aua_" + name, args)
                return response.isError, response.structuredContent

            async def call(name, **args):
                failed, data = await raw(name, **args)
                if failed:
                    raise AssertionError(f"{name}: {json.dumps(data, ensure_ascii=False)}")
                return data

            def record(label, data):
                located = data.get("visual") or data.get("locate") or data
                metrics = located.get("metrics", {})
                calls.append(
                    {
                        "case": label,
                        "status": located.get("status"),
                        "presence": located.get("presence"),
                        "count": (located.get("count") or {}).get("choice"),
                        "confidence": located.get("confidence"),
                        "pixel_change_ratio": located.get("pixel_change_ratio"),
                        "input_tokens": metrics.get("input_tokens"),
                        "model_seconds": round(metrics.get("model_seconds", 0), 2),
                        "elapsed_seconds": round(metrics.get("elapsed_seconds", 0), 2),
                    }
                )

            tools = {t.name for t in (await client.list_tools()).tools}
            assert "aua_locate" in tools
            status = await call("screen_action", action="jev_status")
            assert status["enabled"], "Enable Jev with a key first"
            assert status["images_enabled"], "A saved choice turned screenshot sending off"
            if not (await call("setup", action="status"))["service_reachable"]:
                await call("setup", action="start")
            ready = await call("ready")
            original = ready["observation"]
            width, height = ready["observation"]["viewport"]
            # Fixture content only: exclude status bar (notifications) and navigation bar.
            region = {
                "x": 0,
                "y": round(height * 0.08),
                "width": width,
                "height": round(height * 0.84),
            }

            def target(description, **extra):
                return {"package": PACKAGE, "description": description, "region": region, **extra}

            try:
                command([adb, "-s", serial, "install", "-t", apk])
                launch()
                await call("wait", selector={"description": "open-status"})

                # 1. Read-only location lands inside the real Status button.
                status_node = (await call("find", selector={"description": "open-status"}))[
                    "matches"
                ][0]
                found = await call("locate", **target("the button labeled Status"))
                record("locate_status_button", found)
                assert found["status"] == "located" and inside(found["tap"], status_node), found
                assert not found["action_attempted"]
                passed.append("locate_inside_real_button")

                # 2. An absent target is not located and has no tap point.
                missing = await call("locate", **target("a shopping cart icon"))
                record("locate_absent_icon", missing)
                assert missing["status"] == "not_found" and missing["tap"] is None, missing
                passed.append("absent_target_not_found")

                # 3. Several equal matches are not a target.
                several = await call("locate", **target("a gray button"))
                record("locate_one_of_several", several)
                assert several["tap"] is None or several["status"] == "located", several
                passed.append(f"several_buttons_status_{several['status']}")

                # 4. Visual tap opens Status once, with wait_for.
                tapped = await call(
                    "tap",
                    visual=target("the button labeled Status"),
                    wait_for={"selector": {"description": "show-result"}},
                )
                record("visual_tap_status", tapped)
                assert tapped["wait"]["condition_met"], tapped
                passed.append("visual_tap_with_wait_for")

                # 5. Unconfirmed visual target: error, nothing tapped.
                failed, refused = await raw("tap", visual=target("a shopping cart icon"))
                record("visual_tap_absent", refused["error"])
                assert failed and refused["error"]["code"] == "visual_target_not_found", refused
                assert refused["action_attempted"] is False
                assert (await call("find", selector={"text": "PENDING"}))["total"] == 1
                passed.append("absent_visual_target_never_taps")

                # 6. Second visual tap completes the fixture flow.
                shown = await call(
                    "tap",
                    visual=target("the button labeled Show result"),
                    wait_for={"selector": {"text": "RESULT_READY"}},
                )
                record("visual_tap_show_result", shown)
                assert shown["wait"]["condition_met"], shown
                passed.append("visual_tap_completes_flow")

                return {"passed": passed, "tool_count": len(tools), "calls": calls}
            finally:
                try:
                    if (
                        f"package:{PACKAGE}"
                        in shell("pm", "list", "packages", PACKAGE).splitlines()
                    ):
                        command([adb, "-s", serial, "uninstall", PACKAGE])
                finally:
                    activity = original and original.get("activity")
                    package = original and original.get("foreground")
                    if package and activity:
                        try:
                            shell("am", "start", "-n", f"{package}/{activity}")
                        except RuntimeError:
                            pass  # Not exported; the task usually returns once the fixture is gone.
                assert not shell("pm", "list", "packages", PACKAGE), "Fixture was not removed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument(
        "--sdk",
        type=Path,
        default=Path(
            os.environ.get("ANDROID_SDK_ROOT")
            or os.environ.get("ANDROID_HOME")
            or Path.home() / "Library/Android/sdk"
        ),
    )
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="android-use-vision-") as directory:
        apk = build_app(options.sdk, Path(directory))
        print(json.dumps(asyncio.run(exercise(options.serial, apk)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
