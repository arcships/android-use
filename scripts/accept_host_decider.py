"""Opt-in real-device acceptance for the keyless host decider, via real stdio MCP.

Installs only the isolated fixture package, uninstalls it in finally, and restores the
original foreground activity. Uses a temporary state directory, so no saved Jev key is
read and decider=auto must resolve to the host. Choices are scripted from the returned
state/options, standing in for the host Agent.
Run: uv run python scripts/accept_host_decider.py --serial <authorized-device>
"""

import argparse
import asyncio
import json
import os
import shutil
import signal
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from accept_device import PACKAGE, build_app, command  # noqa: E402

NAME = "宿主决策🐻"


def stop_previews(state):
    """Preview daemons outlive the server by design; the temporary state dir does not."""
    stopped = []
    for path in state.glob("*.preview.json"):
        try:
            info = json.loads(path.read_text())
            request = urllib.request.Request(
                f"http://127.0.0.1:{info['port']}/{info['token']}/api/status",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=5) as response:
                status = json.load(response)
        except (OSError, ValueError, KeyError):
            continue
        # Signal only the authenticated owner of this endpoint, never a recycled PID.
        pid = status.get("pid")
        if type(pid) is int and pid > 1 and pid == info.get("pid"):
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
    return stopped


def node_text(result, option):
    """Visible label of an option's target node, as the host Agent would read it."""
    target = result["decision"]["options"][option]
    if not isinstance(target, dict) or not isinstance(target.get("target"), dict):
        return ""
    node = target["target"].get("node")
    for item in result["decision"]["state"]["ui"]:
        if item["id"] == node:
            # Themes may render button text in caps; compare case-insensitively.
            return (item.get("text") or item.get("description") or "").casefold()
    return ""


def pick(result, action, label=None):
    for option, value in result["decision"]["options"].items():
        if isinstance(value, dict) and value.get("action") == action:
            if label is None or node_text(result, option) == label.casefold():
                return option
    raise AssertionError(f"No {action} {label!r} option: {json.dumps(result['decision'])}")


async def exercise(serial, apk, state_dir):
    adb = os.environ.get("ANDROID_USE_ADB") or shutil.which("adb")
    if not adb:
        raise RuntimeError("ADB is required")

    def shell(*args):
        return command([adb, "-s", serial, "shell", *args])

    def launch():
        shell("am", "force-stop", PACKAGE)
        shell("am", "start", "-W", "-n", PACKAGE + "/.MainActivity", "--ez", "benchmark", "true")
        # Fixture setup only: -W returns at first draw, while launcher animations
        # (e.g. HyperOS) can still swallow an immediate tap.
        time.sleep(1.0)

    if f"package:{PACKAGE}" in shell("pm", "list", "packages", PACKAGE).splitlines():
        raise RuntimeError("Fixture package already exists; refusing to replace it")
    env = {**os.environ, "ANDROID_SERIAL": serial, "ANDROID_USE_STATE_DIR": str(state_dir)}
    for name in ("ANDROID_USE_JEV_ENABLED", "TYPESAFE_API_KEY"):
        env.pop(name, None)
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "android_use.server"], env=env
    )
    passed, rounds = [], {}
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
                    raise AssertionError(f"{name}: {data}")
                return data

            async def decide(result, choice):
                return await call("goal_decide", token=result["decision"]["token"], choice=choice)

            tools = {t.name for t in (await client.list_tools()).tools}
            assert {"aua_execute_goal", "aua_goal_decide"} <= tools
            if not (await call("setup", action="status"))["service_reachable"]:
                await call("setup", action="start")
            ready = await call("ready")
            original = ready["observation"]
            try:
                command([adb, "-s", serial, "install", "-t", apk])

                # 1. auto -> host; three host decisions complete a Unicode draft.
                launch()
                assert (await call("wait", selector={"description": "open-profile"}))[
                    "condition_met"
                ]
                began = time.monotonic()
                result = await call(
                    "execute_goal",
                    goal=f"Open Profile, set Name to {NAME}, then Apply draft to save locally.",
                    package=PACKAGE,
                    texts=[NAME],
                    completion={"text_contains": "DRAFT=宿主决策"},
                    max_steps=6,
                    timeout_seconds=30.0,
                )
                assert result["status"] == "decision_required", result
                assert result["roles"]["decision"] == "host_agent"
                assert result["metrics"]["provider"] == "host_agent"
                plan = [("tap", "Profile"), ("focus_type_text", None), ("tap", "Apply draft")]
                for action, label in plan:
                    assert result["status"] == "decision_required", result
                    result = await decide(result, pick(result, action, label))
                assert result["status"] == "completed" and result["verified"], result
                assert [s["op"] for s in result["steps"]] == ["tap", "focus_type_text", "tap"]
                assert all(s["state"] == "executed" for s in result["steps"])
                macro = result["steps"][1]["substeps"][1]
                assert macro["verified"] and macro["verification_source"] == "native_getText"
                assert result["metrics"]["model_calls"] == 3
                rounds["profile"] = {
                    "host_round_trips": 4,
                    "wall_seconds": round(time.monotonic() - began, 2),
                    "device_seconds": round(result["metrics"]["elapsed_seconds"], 2),
                }
                passed.append("auto_host_profile_unicode_three_decisions")

                # 2. A second goal for the token checks below.
                launch()
                await call("wait", selector={"description": "open-profile"})
                first = await call(
                    "execute_goal",
                    goal="Open Status and show the result.",
                    package=PACKAGE,
                    completion={"text": "RESULT_READY"},
                    decider="host",
                )
                last_token = first["decision"]["token"]
                status_option = pick(first, "tap", "Status")
                # 3. Unknown ID keeps the token.
                failed, invalid = await raw("goal_decide", token=last_token, choice="zz")
                assert failed and invalid["error"]["code"] == "invalid_choice", invalid
                passed.append("unknown_choice_rejected_token_kept")

                # 4. UI changed by someone else between calls: choice discarded, no action.
                await call(
                    "tap",
                    selector={"description": "open-catalog"},
                    wait_for={"selector": {"description": "catalog-list"}},
                )
                stale = await decide(first, status_option)
                assert stale["status"] == "decision_required", stale
                assert stale["decision"]["previous_choice_stale"] is True
                assert stale["steps"] == [] and stale["metrics"]["stale_decisions"] == 1
                assert any(
                    i.get("text") == "NOT_SELECTED" for i in stale["decision"]["state"]["ui"]
                )
                passed.append("stale_choice_discarded_after_external_ui_change")

                failed, replay = await raw("goal_decide", token=last_token, choice=status_option)
                assert failed and replay["error"]["code"] == "goal_session_expired", replay
                assert (await call("find", selector={"text": "NOT_SELECTED"}))["total"] == 1
                passed.append("consumed_token_cannot_replay")

                # 5. Swipe candidates on a real list, then the target tap.
                launch()
                await call(
                    "tap",
                    selector={"description": "open-catalog"},
                    wait_for={"selector": {"description": "catalog-list"}},
                )
                result = await call(
                    "execute_goal",
                    goal="Select Item 20 in the catalog.",
                    package=PACKAGE,
                    actions=["tap", "swipe"],
                    completion={"text": "SELECTED=Item 20"},
                    max_steps=10,
                    decider="host",
                )
                swipes = 0
                while result["status"] == "decision_required":
                    labels = {node_text(result, o) for o in result["decision"]["options"]}
                    if "item 20" in labels:
                        result = await decide(result, pick(result, "tap", "Item 20"))
                    else:
                        swipe = next(
                            o
                            for o, v in result["decision"]["options"].items()
                            if isinstance(v, dict)
                            and v.get("action") == "swipe"
                            and v.get("finger_motion") == "up"
                        )
                        swipes += 1
                        result = await decide(result, swipe)
                assert result["verified"] and swipes >= 1, result
                assert result["steps"][-1]["op"] == "tap"
                rounds["catalog"] = {
                    "swipe_choices": swipes,
                    "executed_swipes": sum(s["op"] == "swipe" for s in result["steps"]),
                    "stale_decisions": result["metrics"]["stale_decisions"],
                    "steps": len(result["steps"]),
                }
                passed.append("host_swipe_then_tap_on_real_list")

                # 6. Pause between decisions: no read or action; ledger preserved.
                launch()
                await call("wait", selector={"description": "open-profile"})
                first = await call(
                    "execute_goal",
                    goal="Open Status and show the result.",
                    package=PACKAGE,
                    completion={"text": "RESULT_READY"},
                    decider="host",
                )
                second = await decide(first, pick(first, "tap", "Status"))
                assert second["status"] == "decision_required" and len(second["steps"]) == 1
                await call("pause")
                try:
                    failed, stopped = await raw(
                        "goal_decide",
                        token=second["decision"]["token"],
                        choice=pick(second, "tap", "Show result"),
                    )
                finally:
                    await call("resume")
                assert failed and stopped["error"]["code"] == "user_takeover", stopped
                assert stopped["steps"][0]["state"] == "executed"
                assert stopped["handoff"]["next"] == "user_authentication"
                assert "observation" not in stopped
                assert (await call("find", selector={"text": "PENDING"}))["total"] == 1
                passed.append("pause_between_decisions_hands_off_with_ledger")

                return {
                    "passed": passed,
                    "tool_count": len(tools),
                    "rounds": rounds,
                }
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
    with tempfile.TemporaryDirectory(prefix="android-use-host-") as directory:
        apk = build_app(options.sdk, Path(directory))
        state = Path(directory) / "state"
        state.mkdir()
        try:
            result = asyncio.run(exercise(options.serial, apk, state))
        finally:
            stopped = stop_previews(state)
        print(json.dumps({**result, "stopped_previews": stopped}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
