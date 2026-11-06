"""Opt-in real-device acceptance via MCP and an isolated temporary Android app.

Requires Android SDK platform/build tools and a JDK. Installs only the fixture package,
uninstalls it in finally, and restores the original foreground activity when possible.
Run: uv run python scripts/accept_device.py --serial <authorized-device>
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from zipfile import ZipFile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PACKAGE = "io.github.androiduse.acceptance"
ROOT = Path(__file__).resolve().parents[1]


def command(args, **kwargs):
    result = subprocess.run(
        [str(arg) for arg in args],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
        **kwargs,
    )
    if result.returncode:
        raise RuntimeError(
            f"{Path(args[0]).name} failed: {(result.stderr or result.stdout)[-4000:]}"
        )
    return result.stdout.strip()


def newest(directory):
    return max(
        (p for p in directory.iterdir() if p.is_dir() and p.name[0].isdigit()),
        key=lambda p: tuple(int(n) for n in p.name.split(".") if n.isdigit()),
    )


def build_app(sdk, output):
    tools = newest(sdk / "build-tools")
    # This fixture uses API 23; prefer the oldest installed stable SDK >= 28 so a
    # preview platform's newer Java class format doesn't require a preview JDK.
    platform = min(
        (
            p
            for p in (sdk / "platforms").glob("android-*/android.jar")
            if p.parent.name.removeprefix("android-").isdigit()
            and int(p.parent.name.removeprefix("android-")) >= 28
        ),
        key=lambda p: int(p.parent.name.split("-")[1].split(".")[0]),
    )
    fixture = ROOT / "tests/device_app"
    command(
        [
            tools / "aapt",
            "package",
            "-f",
            "-M",
            fixture / "AndroidManifest.xml",
            "-I",
            platform,
            "-F",
            output / "base.apk",
        ]
    )
    classes = output / "classes"
    classes.mkdir()
    command(
        [
            "javac",
            "-source",
            "8",
            "-target",
            "8",
            "-classpath",
            platform,
            "-d",
            classes,
            fixture / "MainActivity.java",
        ]
    )
    dex = output / "dex"
    dex.mkdir()
    command(
        [
            tools / "d8",
            "--min-api",
            "23",
            "--lib",
            platform,
            "--output",
            dex,
            *classes.rglob("*.class"),
        ]
    )
    with ZipFile(output / "base.apk", "a") as archive:
        archive.write(dex / "classes.dex", "classes.dex")
    command([tools / "zipalign", "-f", "4", output / "base.apk", output / "test.apk"])
    command(
        [
            "keytool",
            "-genkeypair",
            "-noprompt",
            "-keystore",
            output / "test.jks",
            "-storepass",
            "androiduse",
            "-keypass",
            "androiduse",
            "-alias",
            "test",
            "-keyalg",
            "RSA",
            "-validity",
            "1",
            "-dname",
            "CN=AndroidUseAcceptance",
        ]
    )
    command(
        [
            tools / "apksigner",
            "sign",
            "--ks",
            output / "test.jks",
            "--ks-pass",
            "pass:androiduse",
            output / "test.apk",
        ]
    )
    return output / "test.apk"


async def exercise(serial, apk, jev=False, variation=False):
    adb = os.environ.get("ANDROID_USE_ADB") or shutil.which("adb")
    if not adb:
        raise RuntimeError("ADB is required")

    def shell(*args):
        return command([adb, "-s", serial, "shell", *args])

    def restart_benchmark():
        shell("am", "force-stop", PACKAGE)
        shell("am", "start", "-W", "-n", PACKAGE + "/.MainActivity", "--ez", "benchmark", "true")
        # -W returns at first draw; launcher animations (e.g. HyperOS) can still
        # swallow an immediate tap. Fixture setup only, not a behavior under test.
        time.sleep(1.0)

    if f"package:{PACKAGE}" in shell("pm", "list", "packages", PACKAGE).splitlines():
        raise RuntimeError("Fixture package already exists; refusing to replace it")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "android_use.server"],
        env={**os.environ, "ANDROID_SERIAL": serial},
    )
    passed = []
    variation_audit = []
    variation_calls = 0
    jev_metrics = None
    handoff_metrics = None
    original = None
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            async def call(name, **args):
                nonlocal variation_calls

                def add_variation(op, parameters):
                    nonlocal variation_calls
                    if op in {"tap", "long_press", "double_tap", "drag", "swipe", "scroll_until"} and not parameters.get("visual"):
                        parameters["variation"] = {
                            "seed": 41 + variation_calls, "delay_max_seconds": .1,
                        }
                        variation_calls += 1

                if variation:
                    add_variation(name, args)
                    if name == "batch":
                        for step in args["steps"]:
                            add_variation(step["op"], step["args"])
                response = await client.call_tool("aua_" + name, args)
                data = response.structuredContent
                if response.isError:
                    raise AssertionError(f"{name}: {data}")
                if "variation" in data:
                    variation_audit.append({"op": name, **data["variation"]})
                if "variations" in data:
                    variation_audit.extend({"op": name, **entry} for entry in data["variations"])
                if name == "batch":
                    for step, result in zip(args["steps"], data["results"], strict=True):
                        if "variation" in result:
                            variation_audit.append({"op": step["op"], **result["variation"]})
                return data

            status = await call("setup", action="status")
            if not status.get("service_reachable"):
                await call("setup", action="start")
            ready = await call("ready")
            original = ready["observation"]
            try:
                command([adb, "-s", serial, "install", "-t", apk])
                await call(
                    "launch_app",
                    package=PACKAGE,
                    wait_for={
                        "selector": {"description": "show-target"},
                        "timeout_seconds": 10,
                    },
                )
                # The selector appears at first draw; launch animations (e.g. HyperOS)
                # can still swallow an immediate tap. Fixture setup only.
                await asyncio.sleep(1.0)
                start = time.monotonic()
                shown = await call(
                    "tap",
                    selector={"description": "show-target"},
                    wait_for={"selector": {"text": "TARGET_VISIBLE"}},
                    observe="tree",
                )
                assert shown["wait"]["condition_met"]
                hidden = await call(
                    "tap",
                    selector={"description": "hide-target"},
                    wait_for={
                        "selector": {"text": "TARGET_VISIBLE"},
                        "state": "gone",
                    },
                )
                assert hidden["wait"]["condition_met"] and not hidden["wait"]["found"]
                passed += ["tap_wait_visible", "tap_wait_gone"]
                await call(
                    "long_press",
                    selector={"description": "long-target"},
                    wait_for={
                        "selector": {"text": "LONG_PRESSES=1"},
                    },
                )
                passed.append("long_press_event")
                await call(
                    "double_tap",
                    selector={"description": "gesture-pad"},
                    wait_for={
                        "selector": {"text": "DOUBLE_TAPS=1"},
                    },
                )
                passed.append("double_tap_event")
                pad = (await call("find", selector={"description": "gesture-pad"}))["matches"][0]
                x, y, w, h = pad["rect"]
                await call(
                    "drag",
                    start={"x": x + w // 4, "y": y + h // 2},
                    end={"x": x + 3 * w // 4, "y": y + h // 2},
                    wait_for={
                        "selector": {"text": "DRAGS=1"},
                    },
                )
                passed.append("drag_event")
                rect = (await call("find", selector={"description": "scroll-list"}))["matches"][0][
                    "rect"
                ]
                region = dict(zip(("x", "y", "width", "height"), rect, strict=True))
                found = await call(
                    "scroll_until",
                    selector={"text": "Row 12"},
                    region=region,
                    max_swipes=20,
                    observe="tree",
                )
                assert found["found"] and found["swipes"] > 0, found
                passed.append("scroll_find_offscreen_target")
                again = await call("scroll_until", selector={"text": "Row 12"}, region=region)
                assert again["found"] and again["swipes"] == 0
                passed.append("scroll_visible_target_zero_swipes")
                end = await call(
                    "scroll_until", selector={"text": "Row 19"}, region=region, max_swipes=20
                )
                assert end["found"]
                missed = await call(
                    "scroll_until", selector={"text": "never-present"}, region=region
                )
                assert not missed["found"] and missed["stop_reason"] == "no_progress"
                passed.append("scroll_no_progress_stop")
                pair = await call(
                    "batch",
                    steps=[
                        {
                            "op": "long_press",
                            "args": {
                                "selector": {"description": "long-target"},
                                "wait_for": {"selector": {"text": "LONG_PRESSES=2"}},
                            },
                        },
                        {
                            "op": "double_tap",
                            "args": {
                                "selector": {"description": "gesture-pad"},
                                "wait_for": {"selector": {"text": "DOUBLE_TAPS=2"}},
                            },
                        },
                    ],
                )
                assert pair["completed_steps"] == 2
                passed.append("gesture_batch_with_waits")
                failed = await client.call_tool(
                    "aua_batch",
                    {
                        "steps": [
                            {
                                "op": "long_press",
                                "args": {
                                    "selector": {"description": "long-target"},
                                    "wait_for": {
                                        "selector": {"text": "never-present"},
                                        "timeout_seconds": 0.5,
                                    },
                                },
                            },
                            {
                                "op": "double_tap",
                                "args": {"selector": {"description": "gesture-pad"}},
                            },
                        ]
                    },
                )
                assert (
                    failed.isError
                    and failed.structuredContent["stop_reason"] == "postcondition_timeout"
                )
                assert failed.structuredContent["stopped_at"] == 1
                assert (await call("wait", selector={"text": "LONG_PRESSES=3"}))["condition_met"]
                assert (await call("wait", selector={"text": "DOUBLE_TAPS=2"}))["condition_met"]
                passed.append("timeout_no_replay_and_batch_stop")
                waiting = asyncio.create_task(
                    client.call_tool(
                        "aua_wait",
                        {
                            "selector": {"text": "never-present"},
                            "timeout_seconds": 10,
                        },
                    )
                )
                await asyncio.sleep(0.4)
                try:
                    assert (await call("pause"))["paused"]
                    cancelled = await waiting
                    assert cancelled.isError
                    assert cancelled.structuredContent["error"]["code"] == "user_takeover"
                finally:
                    assert (await call("resume"))["paused"] is False
                passed.append("pause_cancels_active_wait")
                if jev:
                    goal = await call(
                        "execute_goal",
                        goal="Tap Show and wait for TARGET_VISIBLE.",
                        package=PACKAGE,
                        actions=["tap", "wait"],
                        completion={"text": "TARGET_VISIBLE"},
                        timeout_seconds=30.0,
                    )
                    assert goal["verified"] and goal["status"] == "completed"
                    assert (await call("wait", selector={"text": "TARGET_VISIBLE"}))[
                        "condition_met"
                    ]
                    jev_metrics = goal["metrics"]
                    assert goal["roles"] == {
                        "planner": "host_agent",
                        "decision": "jev",
                        "executor": "android_use",
                    }
                    passed.append("jev_goal_over_real_stdio_mcp")
                    # No completion selector: Jev's parallel goal head must stop the run
                    # once the target shows, before any further mutation.
                    await call(
                        "tap",
                        selector={"description": "hide-target"},
                        wait_for={"selector": {"text": "TARGET_VISIBLE"}, "state": "gone"},
                    )
                    judged = await call(
                        "execute_goal",
                        goal="Tap Show once so that TARGET_VISIBLE appears.",
                        package=PACKAGE,
                        actions=["tap", "wait"],
                        timeout_seconds=30.0,
                    )
                    judged_summary = {
                        k: judged.get(k) for k in ("stop_reason", "steps", "decisions")
                    }
                    assert judged["stop_reason"] == "model_goal_achieved", judged_summary
                    assert [s["op"] for s in judged["steps"]] == ["tap"], judged_summary
                    assert judged["handoff"]["next"] == "verify_outcome"
                    assert (await call("wait", selector={"text": "TARGET_VISIBLE"}))[
                        "condition_met"
                    ]
                    passed.append("jev_goal_judgment_stops_without_selector")
                    restart_benchmark()
                    assert (await call("wait", selector={"description": "open-profile"}))[
                        "condition_met"
                    ]
                    handed = await call(
                        "execute_goal",
                        goal="Open Profile, enter 接管验收 into Name, then Apply draft.",
                        package=PACKAGE,
                        texts=["接管验收"],
                        completion={"text": "DRAFT=接管验收"},
                        max_steps=1,
                        timeout_seconds=30.0,
                    )
                    assert handed["status"] == "handoff" and handed["stop_reason"] == "max_steps"
                    assert handed["handoff"]["completed_actions"] == 1
                    assert handed["handoff"]["uncertain_steps"] == []
                    assert handed["observation"]["foreground"] == PACKAGE
                    assert (await call("find", selector={"description": "draft-name"}))[
                        "total"
                    ] == 1
                    # Continue at the handed-off field; don't replay the navigation prefix.
                    await call(
                        "batch",
                        steps=[
                            {"op": "tap", "args": {"selector": {"description": "draft-name"}}},
                            {"op": "type_text", "args": {"text": "接管验收", "verify": True}},
                            {
                                "op": "tap",
                                "args": {
                                    "selector": {"description": "apply-draft"},
                                    "wait_for": {"selector": {"text": "DRAFT=接管验收"}},
                                },
                            },
                        ],
                    )
                    assert (await call("wait", selector={"text": "DRAFT=接管验收"}))[
                        "condition_met"
                    ]
                    handoff_metrics = handed["metrics"]
                    passed.append("jev_handoff_then_host_continues_without_replay")
                    # Reset the field so focus + exact Unicode input needs one decision.
                    restart_benchmark()
                    await call(
                        "tap",
                        selector={"description": "open-profile"},
                        wait_for={"selector": {"description": "draft-name"}},
                    )
                    macro = await call(
                        "execute_goal",
                        package=PACKAGE,
                        goal="Set Name to 宏动作🐻 then Apply draft to save locally.",
                        texts=["宏动作🐻"],
                        completion={"text_contains": "DRAFT=宏动作"},
                        max_steps=2,
                        timeout_seconds=30.0,
                    )
                    # AX may replace emoji with dots; native getText is authoritative.
                    macros = [s for s in macro["steps"] if s["op"] == "focus_type_text"]
                    assert len(macros) == 1 and macros[0]["state"] == "executed", macro
                    assert macros[0]["substeps"][1]["verified"]
                    assert macro["metrics"]["model_calls"] == 2
                    assert macro["verified"], {
                        k: macro.get(k) for k in ("stop_reason", "steps", "decisions", "error")
                    }
                    assert (await call("find", selector={"text_contains": "DRAFT=宏动作"}))[
                        "total"
                    ] == 1
                    passed.append("jev_focus_type_unicode_macro_native_readback")
                    from android_use.backend import Backend

                    probe = Backend(serial)
                    try:
                        for orientation, rotation in [("l", 1), ("n", 0)]:
                            probe.d.set_orientation(orientation)
                            end = time.monotonic() + 5
                            while (
                                probe.d.info["displayRotation"] != rotation
                                and time.monotonic() < end
                            ):
                                await asyncio.sleep(0.1)
                            size = tuple(probe.size())
                            assert size == tuple(probe.d.window_size()) == probe.screenshot().size
                            assert (size[0] > size[1]) == (rotation == 1)
                            observation = await call("observe", mode="tree")
                            assert tuple(observation["viewport"]) == size
                    finally:
                        probe.d.set_orientation("n")
                        probe.d.freeze_rotation(False)
                    passed.append("portrait_landscape_geometry_matches_legacy_and_screenshot")
                return {
                    "passed": passed,
                    "tool_count": len((await client.list_tools()).tools),
                    "scroll_swipes": found["swipes"],
                    "no_progress_swipes": missed["swipes"],
                    "elapsed_seconds": round(time.monotonic() - start, 2),
                    **({"variation": {"enabled": True, "actions": variation_audit}} if variation else {}),
                    **(
                        {"jev_metrics": jev_metrics, "handoff_metrics": handoff_metrics}
                        if jev
                        else {}
                    ),
                }
            finally:
                # Runs on assertion/tool failure too; the app holds no persistent user data.
                try:
                    if (
                        f"package:{PACKAGE}"
                        in shell("pm", "list", "packages", PACKAGE).splitlines()
                    ):
                        command([adb, "-s", serial, "uninstall", PACKAGE])
                finally:
                    activity = original.get("activity")
                    package = original.get("foreground")
                    if package and activity:
                        try:
                            shell("am", "start", "-n", f"{package}/{activity}")
                        except RuntimeError:
                            # A non-exported activity can't be started from adb; removing the
                            # fixture usually returns its task to the top. Check that instead.
                            pass
                        restored = await call("observe", mode="tree")
                        assert restored["foreground"] == package, (
                            "Original foreground did not restore"
                        )
                assert not shell("pm", "list", "packages", PACKAGE), "Fixture was not removed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--jev", action="store_true", help="Also exercise opt-in Jev via real MCP")
    parser.add_argument("--variation", action="store_true", help="Exercise opt-in gesture variation")
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
    if options.jev:
        from android_use.jev import JevConfig

        JevConfig.from_env()
    with tempfile.TemporaryDirectory(prefix="android-use-acceptance-") as directory:
        apk = build_app(options.sdk, Path(directory))
        print(json.dumps(asyncio.run(exercise(options.serial, apk, options.jev, options.variation)), indent=2))


if __name__ == "__main__":
    main()
