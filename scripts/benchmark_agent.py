"""Current host Agent vs current host Agent + Jev, over an interactive JSONL bridge.

The agent reads each decision event and writes ONE JSON response to stdin. No scripted
action selection or extra text-model API is used. Host token usage/cost stay unknown.
"""

import argparse
import json
import os
import select
import statistics
import sys
import tempfile
import time
from pathlib import Path

from accept_device import PACKAGE, build_app, command
from benchmark_executor import TASKS as FIXED_TASKS

from android_use.backend import UseError
from android_use.executor import candidates, completed, snapshot
from android_use.jev import JevConfig
from android_use.models import ExecuteGoalArgs
from android_use.runtime import Runtime

TASKS = [
    *FIXED_TASKS,
    {
        "name": "dynamic_workflow_en",
        "goal": "Open Workflow and follow each displayed Next color instruction until WORKFLOW_DONE. Never choose a wrong color.",
        "completion": {"text": "WORKFLOW_DONE"},
    },
]
METER = None
HOST_TOOLS = {
    "tap",
    "long_press",
    "swipe",
    "type_text",
    "press_button",
    "batch",
    "scroll_until",
    "wait",
}


def ask(event, timeout):
    payload = json.dumps(event, ensure_ascii=False)
    start = time.monotonic()
    if METER is not None:
        METER["host_requests"] += 1
        METER["host_input_utf8_bytes"] += len(payload.encode())
    print(payload, flush=True)
    try:
        if not select.select([sys.stdin], [], [], max(0, timeout))[0]:
            raise UseError("host_timeout", "Host decision budget exhausted")
        raw = sys.stdin.readline()
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
        if METER is not None:
            METER["host_replies"] += 1
            METER["host_output_utf8_bytes"] += len(raw.encode())
        return result
    except (ValueError, EOFError):
        raise UseError("host_invalid_response", "Expected one JSON response") from None
    finally:
        if METER is not None:
            METER["host_wait_seconds"] += time.monotonic() - start


def verify_host(event, timeout):
    try:
        return ask(event, timeout)
    except UseError as exc:
        # A timed-out final check is a failed run, not a reason to lose the record.
        return {"verified": False, "error": exc.code}


def run_host(runtime, args, label, deadline, last_result=None):
    """The host keeps ordinary MCP helpers, including batch/wait/scroll_until."""
    parsed = ExecuteGoalArgs.model_validate(args)
    decisions = 0
    previous = last_result
    for _ in range(20):
        snap = snapshot(runtime, PACKAGE)
        if completed(snap, parsed):
            return {
                "status": "completed",
                "verified": True,
                "stop_reason": "completion_selector",
            }, decisions
        if time.monotonic() >= deadline:
            return {
                "status": "handoff",
                "verified": False,
                "stop_reason": "host_timeout",
            }, decisions
        options = candidates(snap, parsed)
        response = ask(
            {
                "event": "choose",
                "run": label,
                "goal": args["goal"],
                "ui": [
                    {k: n[k] for k in ("text", "description", "class_name", "rect")}
                    for n in snap["nodes"]
                    if n["text"] or n["description"] or n["clickable"] or n["scrollable"]
                ],
                "candidates": {
                    k: {
                        "op": v["op"],
                        "target": v["label"],
                        **{
                            name: value
                            for name, value in v["args"].items()
                            if name in {"direction", "text"}
                        },
                    }
                    for k, v in options.items()
                },
                "last_result": previous,
                "instructions": "Reply {choice: candidate_id} OR {tool: MCP operation, args: {...}}. Ordinary batch, scroll_until, action-plus-wait remain available.",
            },
            max(0, deadline - time.monotonic()),
        )
        decisions += 1
        choice = response.get("choice")
        if isinstance(choice, str) and choice in options:
            selected = options[choice]
            if selected["op"] == "wait_local":
                time.sleep(0.35)
                continue
            op, params = selected["op"], selected["args"]
        else:
            op, params = response.get("tool"), response.get("args", {})
        if not isinstance(op, str) or not isinstance(params, dict):
            raise UseError("host_invalid_response", "Invalid host operation")
        if op not in HOST_TOOLS:
            raise UseError("host_invalid_response", "Unknown host operation")
        result = runtime.call(op, **params)
        previous = {k: result.get(k) for k in ("ok", "error", "stop_reason", "wait") if k in result}
    return {"status": "handoff", "verified": False, "stop_reason": "host_max_steps"}, decisions


def summarize(rows):
    result = {}
    for arm in sorted({r["arm"] for r in rows}):
        selected = [r for r in rows if r["arm"] == arm]
        result[arm] = {
            "runs": len(selected),
            "successes": sum(r["success"] for r in selected),
            "median_seconds": statistics.median(r["elapsed_seconds"] for r in selected),
            "host_decisions": sum(r["host_decisions"] for r in selected),
            "jev_calls": sum(r["jev_calls"] for r in selected),
            "jev_estimated_cost_usd": sum(r["jev_estimated_cost_usd"] for r in selected)
            if all(r["jev_estimated_cost_usd"] is not None for r in selected)
            else None,
            "host_cost_usd": None,
            "total_cost_usd": None,
            "host_wait_seconds": sum(r.get("host_wait_seconds", 0) for r in selected),
            "median_non_host_seconds": statistics.median(
                r.get("non_host_seconds", r["elapsed_seconds"]) for r in selected
            ),
            "host_input_utf8_bytes": sum(r.get("host_input_utf8_bytes", 0) for r in selected),
            "host_output_utf8_bytes": sum(r.get("host_output_utf8_bytes", 0) for r in selected),
        }
    return result


def main():
    global METER
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("test-results/agent-jev-v2.json"))
    parser.add_argument("--tasks", nargs="+", choices=[t["name"] for t in TASKS])
    parser.add_argument("--seed-base", type=int, default=41)
    parser.add_argument("--host-budget", type=float, default=180.0)
    options = parser.parse_args()
    if not 30 <= options.host_budget <= 900:
        parser.error("host-budget must be 30..900 seconds")
    if not 1 <= options.repeats <= 3:
        parser.error("repeats must be 1..3")
    JevConfig.from_env()
    runtime = Runtime(options.serial)
    ready = runtime.call("ready")
    if not ready.get("ready"):
        raise RuntimeError("Run setup(status/start), then ready")
    original = runtime.backend.foreground()
    adb = os.environ.get("ANDROID_USE_ADB", "adb")

    def shell(*args):
        return command([adb, "-s", options.serial, "shell", *args])

    if f"package:{PACKAGE}" in shell("pm", "list", "packages", PACKAGE).splitlines():
        raise RuntimeError("Fixture exists; refusing to replace it")
    sdk = Path(os.environ.get("ANDROID_HOME", str(Path.home() / "Library/Android/sdk")))
    rows, installed = [], False
    try:
        with tempfile.TemporaryDirectory(prefix="android-use-agent-") as temp:
            apk = build_app(sdk, Path(temp))
            command([adb, "-s", options.serial, "install", "-t", apk])
            installed = True
            for repeat in range(options.repeats):
                for index, task in enumerate(
                    t for t in TASKS if not options.tasks or t["name"] in options.tasks
                ):
                    arms = ["agent_only", "agent_jev"]
                    if (repeat + index) % 2:
                        arms.reverse()
                    for arm in arms:
                        shell("am", "force-stop", PACKAGE)
                        shell(
                            "am",
                            "start",
                            "-W",
                            "-n",
                            PACKAGE + "/.MainActivity",
                            "--ez",
                            "benchmark",
                            "true",
                            "--ei",
                            "seed",
                            str(options.seed_base + repeat * 997),
                        )
                        if not runtime.call("wait", selector={"description": "open-profile"})[
                            "condition_met"
                        ]:
                            raise RuntimeError("Reset failed")
                        label = f"{repeat + 1}/{task['name']}/{arm}"
                        args = {
                            "goal": task["goal"],
                            "package": PACKAGE,
                            "completion": task["completion"],
                            "texts": task.get("texts", []),
                            "max_steps": 20,
                            "max_model_calls": 30,
                            "timeout_seconds": 60.0,
                            "actions": task.get("actions", ["tap", "swipe", "type_text", "wait"]),
                        }
                        began = time.monotonic()
                        METER = {
                            "host_requests": 0,
                            "host_replies": 0,
                            "host_wait_seconds": 0.0,
                            "host_input_utf8_bytes": 0,
                            "host_output_utf8_bytes": 0,
                        }
                        host_decisions, jev_calls, jev_cost = 0, 0, 0.0
                        routing = "ordinary_tools"
                        jev_metrics = {}
                        success = False
                        verification = {"verified": False}
                        result = {"status": "handoff", "stop_reason": "not_started"}
                        try:
                            if arm == "agent_only":
                                result, count = run_host(
                                    runtime, args, label, began + options.host_budget
                                )
                                host_decisions += count
                            else:
                                snap = snapshot(runtime, PACKAGE)
                                plan = ask(
                                    {
                                        "event": "delegate",
                                        "run": label,
                                        "task": args,
                                        "visible_text": [
                                            n["text"] or n["description"]
                                            for n in snap["nodes"]
                                            if n["text"] or n["description"]
                                        ],
                                        "instructions": "Agent plans and routes: use {tool: operation, args: {...}} for a known path, {delegate: <execute_goal args>} for repeated next-action decisions, or {stop: true}. batch/scroll_until remain available.",
                                    },
                                    max(0, options.host_budget - (time.monotonic() - began)),
                                )
                                host_decisions += 1
                                if "delegate" in plan:
                                    routing = "jev_decisions"
                                    delegated = ExecuteGoalArgs.model_validate(
                                        plan["delegate"]
                                    ).model_dump()
                                    if delegated["package"] != PACKAGE:
                                        raise ValueError("Fixture scope only")
                                    delegated["timeout_seconds"] = min(
                                        delegated["timeout_seconds"],
                                        max(
                                            0.001, options.host_budget - (time.monotonic() - began)
                                        ),
                                    )
                                    result = runtime.call("execute_goal", **delegated)
                                    metrics = result.get("metrics", {})
                                    jev_metrics = metrics
                                    jev_calls += metrics.get("model_calls", 0)
                                    jev_cost = metrics.get("estimated_cost_usd")
                                    if not result.get("verified"):
                                        # A real handoff returns to this SAME host agent for recovery.
                                        recovery, count = run_host(
                                            runtime,
                                            args,
                                            label + "/recovery",
                                            began + options.host_budget,
                                            last_result={
                                                k: result.get(k)
                                                for k in (
                                                    "stop_reason",
                                                    "handoff",
                                                    "steps",
                                                    "error",
                                                )
                                            },
                                        )
                                        host_decisions += count
                                        result = {
                                            **recovery,
                                            "delegation_stop_reason": result.get("stop_reason"),
                                        }
                                elif plan.get("tool") in HOST_TOOLS:
                                    initial = runtime.call(plan["tool"], **plan.get("args", {}))
                                    result, count = run_host(
                                        runtime,
                                        args,
                                        label,
                                        began + options.host_budget,
                                        last_result={
                                            k: initial.get(k)
                                            for k in ("ok", "error", "stop_reason")
                                        },
                                    )
                                    host_decisions += count
                                else:
                                    result = {
                                        "ok": True,
                                        "verified": False,
                                        "stop_reason": "host_stopped",
                                    }
                            check = runtime.call("find", selector=task["completion"])
                            success = any(n["in_viewport"] for n in check.get("matches", []))
                            verification = verify_host(
                                {
                                    "event": "verify",
                                    "run": label,
                                    "independent_selector": task["completion"],
                                    "visible_match": success,
                                    "executor_status": result.get("status"),
                                    "stop_reason": result.get("stop_reason"),
                                    "instructions": "Reply {verified: true|false} from actual outcome.",
                                },
                                max(0, options.host_budget - (time.monotonic() - began)),
                            )
                            host_decisions += int("error" not in verification)
                        except Exception as exc:
                            result = {
                                "status": "handoff",
                                "stop_reason": (
                                    exc.code if isinstance(exc, UseError) else "benchmark_error"
                                ),
                                "error_type": type(exc).__name__,
                            }
                            verification = {"verified": False, "error": result["stop_reason"]}
                        row = {
                            "arm": arm,
                            "routing": routing,
                            "task": task["name"],
                            "repeat": repeat + 1,
                            "success": success and verification.get("verified") is True,
                            "elapsed_seconds": time.monotonic() - began,
                            "host_decisions": host_decisions,
                            "jev_calls": jev_calls,
                            "jev_estimated_cost_usd": jev_cost,
                            "jev_metrics": jev_metrics,
                            "host_cost_usd": None,
                            "total_cost_usd": None,
                            "stop_reason": result.get("stop_reason"),
                            "delegation_stop_reason": result.get("delegation_stop_reason"),
                            "verification_error": verification.get("error"),
                            "seed": options.seed_base + repeat * 997,
                            **METER,
                            "non_host_seconds": max(
                                0.0, time.monotonic() - began - METER["host_wait_seconds"]
                            ),
                        }
                        rows.append(row)
                        options.output.parent.mkdir(parents=True, exist_ok=True)
                        options.output.write_text(
                            json.dumps(
                                {
                                    "environment": {
                                        "kind": "emulator"
                                        if options.serial.startswith("emulator-")
                                        else "physical-device",
                                        "agent": "current host chat, live JSONL decisions; no scripted selector",
                                        "timing": "Includes host planning/decisions, CLI bridge latency, executor, final host verification; excludes reset/install",
                                        "limits": "same live host chat; familiar fixed tasks plus seeded UI-dependent workflow; host token usage/cost unavailable",
                                        "roles": {
                                            "planner": "host_agent",
                                            "next_action_decision": "host_agent or Jev",
                                            "executor": "Android Use",
                                        },
                                        "host_wait": "separately measured from prompt emission to JSON reply; includes inference, bridge and polling",
                                        "payload_bytes": "UTF-8 protocol size only, not billed tokens",
                                        "host_budget_seconds": options.host_budget,
                                        "seed_base": options.seed_base,
                                    },
                                    "summary": summarize(rows),
                                    "runs": rows,
                                },
                                ensure_ascii=False,
                                indent=2,
                            )
                            + "\n"
                        )
                        print(json.dumps({"event": "result", **row}), flush=True)
    finally:
        if installed:
            command([adb, "-s", options.serial, "uninstall", PACKAGE])
        if original.get("package") and original.get("activity"):
            shell("am", "start", "-n", original["package"] + "/" + original["activity"])
    print(json.dumps({"event": "complete", "summary": summarize(rows)}), flush=True)


if __name__ == "__main__":
    main()
