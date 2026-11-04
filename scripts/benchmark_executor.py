"""Repeatable emulator/device pilot, with independent checks and no secret/raw-UI logs.

uv run --env-file .env --frozen python scripts/benchmark_executor.py --serial emulator-5554
"""

import argparse
import json
import math
import os
import statistics
import tempfile
import time
from pathlib import Path

import httpx
from accept_device import PACKAGE, build_app, command

from android_use.backend import UseError
from android_use.executor import execute
from android_use.jev import INSTRUCTIONS, JevConfig, endpoint_url
from android_use.runtime import Runtime


class TextPolicy:
    """One OpenAI-compatible chat-completions request per observed step."""

    def __init__(self):
        self.base = endpoint_url(os.environ["TEXT_MODEL_BASE_URL"])
        self.model = os.environ["TEXT_MODEL"]
        self.client = httpx.Client(
            headers={"Authorization": "Bearer " + os.environ["TEXT_MODEL_API_KEY"]},
            follow_redirects=False,
        )
        self.metrics = {
            "provider": "openai-compatible",
            "requested_model": self.model,
            "models": [],
            "model_calls": 0,
            "model_seconds": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "unpriced_calls": 0,
            "estimated_cost_usd_lower_bound": 0.0,
        }

    def decide(self, state, criteria, timeout):
        self.metrics["model_calls"] += 1
        self.metrics["unpriced_calls"] += 1
        started = time.monotonic()
        try:
            response = self.client.post(
                self.base + "/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": INSTRUCTIONS
                            + ' Return only a JSON object: {"choice":"candidate_id"}.',
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"state": state, "candidates": criteria}, ensure_ascii=False
                            ),
                        },
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=max(0.001, timeout),
            )
            if response.status_code != 200:
                raise UseError(
                    "baseline_http_error", "Baseline request rejected", status=response.status_code
                )
            data = response.json()
            usage = data.get("usage", {})
            inp, out = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if type(inp) is int and inp >= 0 and type(out) is int and out >= 0:
                self.metrics["input_tokens"] += inp
                self.metrics["output_tokens"] += out
                rates = [
                    os.environ.get("TEXT_MODEL_INPUT_USD_PER_MILLION"),
                    os.environ.get("TEXT_MODEL_OUTPUT_USD_PER_MILLION"),
                ]
                if all(r is not None for r in rates):
                    rates = [float(r) for r in rates]
                    if all(math.isfinite(r) and r >= 0 for r in rates):
                        self.metrics["estimated_cost_usd_lower_bound"] += (
                            inp * rates[0] + out * rates[1]
                        ) / 1e6
                        self.metrics["unpriced_calls"] -= 1
            model = data.get("model")
            if isinstance(model, str) and model not in self.metrics["models"]:
                self.metrics["models"].append(model)
            choice = json.loads(data["choices"][0]["message"]["content"])["choice"]
            if not isinstance(choice, str) or choice not in criteria:
                raise ValueError("Invalid choice")
            # Generative models don't provide a calibrated Choice distribution.
            return {"choice": choice, "probability": 1.0, "margin": 1.0}
        except httpx.HTTPError:
            raise UseError("baseline_transport_error", "Baseline API unavailable") from None
        except (KeyError, TypeError, ValueError, IndexError):
            raise UseError(
                "baseline_invalid_response", "Baseline response failed validation"
            ) from None
        finally:
            self.metrics["model_seconds"] += time.monotonic() - started

    def summary(self):
        return {
            **self.metrics,
            "estimated_cost_usd": None
            if self.metrics["unpriced_calls"]
            else self.metrics["estimated_cost_usd_lower_bound"],
            "cost_basis": "configured token prices",
        }

    def close(self):
        self.client.close()


def tap(description):
    return {"op": "tap", "args": {"selector": {"description": description}}}


TASKS = [
    {
        "name": "navigate_wait_en",
        "goal": "Open Status, then Show result. Wait until RESULT_READY is visible.",
        "completion": {"text": "RESULT_READY"},
        "steps": [
            tap("open-status"),
            {
                "op": "tap",
                "args": {
                    "selector": {"description": "show-result"},
                    "wait_for": {"selector": {"text": "RESULT_READY"}},
                },
            },
        ],
    },
    {
        "name": "draft_zh",
        "goal": "打开 Profile，将 Name 设为 安卓助手，然后点击 Apply draft 保存本地草稿。",
        "texts": ["安卓助手"],
        "completion": {"text": "DRAFT=安卓助手"},
        "steps": [
            tap("open-profile"),
            tap("draft-name"),
            {"op": "type_text", "args": {"text": "安卓助手", "verify": True}},
            tap("apply-draft"),
        ],
    },
    {
        "name": "scroll_select_en",
        "goal": "Open Catalog, scroll down its list and select Item 18.",
        "completion": {"text": "SELECTED=Item 18"},
        "steps": [
            tap("open-catalog"),
            {
                "op": "scroll_until",
                "args": {"selector": {"description": "select-18"}, "max_swipes": 12},
            },
            tap("select-18"),
        ],
    },
    {
        "name": "long_press_zh",
        "goal": "打开 Status，长按 Hold to confirm，直到出现 HOLD_CONFIRMED。",
        "actions": ["tap", "long_press", "wait"],
        "completion": {"text": "HOLD_CONFIRMED"},
        "steps": [
            tap("open-status"),
            {"op": "long_press", "args": {"selector": {"description": "hold-confirm"}}},
        ],
    },
]


def aggregate(rows):
    result = {}
    for arm in sorted({r["arm"] for r in rows}):
        selected = [r for r in rows if r["arm"] == arm]
        durations = sorted(r["elapsed_seconds"] for r in selected)
        costs = [r["metrics"].get("estimated_cost_usd") for r in selected]
        successes = sum(r["success"] for r in selected)
        total_cost = sum(costs) if all(c is not None for c in costs) else None
        result[arm] = {
            "runs": len(selected),
            "successes": successes,
            "success_rate": successes / len(selected),
            "median_seconds_all_runs": statistics.median(durations),
            "p95_seconds_all_runs": durations[math.ceil(len(durations) * 0.95) - 1],
            "model_calls": sum(r["metrics"].get("model_calls", 0) for r in selected),
            "input_tokens": sum(r["metrics"].get("input_tokens", 0) for r in selected),
            "output_tokens": sum(r["metrics"].get("output_tokens", 0) for r in selected),
            "estimated_cost_usd": total_cost,
            "cost_per_success_usd": total_cost / successes
            if total_cost is not None and successes
            else None,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--arms",
        nargs="+",
        choices=["deterministic", "jev", "text"],
        default=["deterministic", "jev", "text"],
    )
    parser.add_argument("--output", type=Path, default=Path("test-results/jev-benchmark.json"))
    options = parser.parse_args()
    if not 1 <= options.repeats <= 10:
        parser.error("repeats must be 1..10")
    arms, skipped = list(dict.fromkeys(options.arms)), {}
    if "text" in arms and not all(
        os.environ.get(k) for k in ("TEXT_MODEL", "TEXT_MODEL_BASE_URL", "TEXT_MODEL_API_KEY")
    ):
        arms.remove("text")
        skipped["text"] = "Baseline model configuration absent; no LLM comparison claimed"
    if "jev" in arms:
        JevConfig.from_env()  # Check opt-in and credentials before touching the device.
    if not arms:
        parser.error("No configured benchmark arms")
    runtime = Runtime(options.serial)
    ready = runtime.call("ready")
    if not ready.get("ready"):
        raise RuntimeError("Device not READY; run setup(status/start) and ready first")
    backend = runtime.backend
    original = backend.foreground()
    adb = os.environ.get("ANDROID_USE_ADB", "adb")

    def shell(*args):
        return command([adb, "-s", options.serial, "shell", *args])

    if f"package:{PACKAGE}" in shell("pm", "list", "packages", PACKAGE).splitlines():
        raise RuntimeError("Fixture exists; refusing to replace it")
    sdk = Path(os.environ.get("ANDROID_HOME", str(Path.home() / "Library/Android/sdk")))
    rows = []
    installed = False
    try:
        with tempfile.TemporaryDirectory(prefix="android-use-benchmark-") as temp:
            apk = build_app(sdk, Path(temp))
            command([adb, "-s", options.serial, "install", "-t", apk])
            installed = True
            for repeat in range(options.repeats):
                for task in TASKS:
                    # Rotate order; every arm gets the same freshly recreated activity.
                    ordered = arms[repeat % len(arms) :] + arms[: repeat % len(arms)]
                    for arm in ordered:
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
                        )
                        reset = runtime.call(
                            "wait", selector={"description": "open-profile"}, timeout_seconds=5.0
                        )
                        if not reset.get("condition_met"):
                            raise RuntimeError("Fixture reset failed")
                        began = time.monotonic()
                        if arm == "deterministic":
                            if task["name"] == "scroll_select_en":
                                # Dynamic nested-list bounds require one current observation.
                                result = runtime.call("batch", steps=task["steps"][:1])
                                region_result = runtime.call(
                                    "find", selector={"description": "catalog-list"}
                                )
                                if result.get("ok") and region_result.get("total") == 1:
                                    x, y, w, h = region_result["matches"][0]["rect"]
                                    steps = json.loads(json.dumps(task["steps"][1:]))
                                    steps[0]["args"]["region"] = {
                                        "x": x,
                                        "y": y,
                                        "width": w,
                                        "height": h,
                                    }
                                    result = runtime.call("batch", steps=steps)
                            else:
                                result = runtime.call("batch", steps=task["steps"])
                            result["metrics"] = {"model_calls": 0, "estimated_cost_usd": 0.0}
                        else:
                            args = {
                                "goal": task["goal"],
                                "package": PACKAGE,
                                "completion": task["completion"],
                                "texts": task.get("texts", []),
                                "timeout_seconds": 60.0,
                                "max_steps": 20,
                                "max_model_calls": 30,
                                "actions": task.get(
                                    "actions", ["tap", "swipe", "type_text", "wait"]
                                ),
                            }
                            if arm == "jev":
                                result = runtime.call("execute_goal", **args)
                            else:
                                with runtime._mutex, runtime._guard():
                                    result = execute(runtime, args, TextPolicy())
                                runtime._deadline = None
                        # Independent UI check included in wall time; model DONE is insufficient.
                        check = runtime.call("find", selector=task["completion"])
                        success = any(
                            m["in_viewport"] for m in check.get("matches", [])
                        ) and result.get("ok", False)
                        row = {
                            "arm": arm,
                            "task": task["name"],
                            "repeat": repeat + 1,
                            "success": success,
                            "elapsed_seconds": time.monotonic() - began,
                            "stop_reason": result.get(
                                "stop_reason", result.get("error", {}).get("code")
                            ),
                            "metrics": result.get("metrics", {}),
                            "steps": result.get("steps", []),
                        }
                        rows.append(row)
                        print(
                            json.dumps(
                                {
                                    k: row[k]
                                    for k in (
                                        "arm",
                                        "task",
                                        "repeat",
                                        "success",
                                        "elapsed_seconds",
                                        "stop_reason",
                                    )
                                }
                            ),
                            flush=True,
                        )
                        options.output.parent.mkdir(parents=True, exist_ok=True)
                        options.output.write_text(
                            json.dumps(
                                {
                                    "environment": {
                                        "kind": "emulator"
                                        if options.serial.startswith("emulator-")
                                        else "physical-device",
                                        "api_level": shell("getprop", "ro.build.version.sdk"),
                                        "fixture": "permission-free synthetic native UI; no IME",
                                        "reset_excluded": True,
                                        "final_verification_included": True,
                                        "confidence_gate": "Jev probability>=0.65, margin>=0.1; text model has no calibrated probabilities",
                                    },
                                    "skipped": skipped,
                                    "summary": aggregate(rows),
                                    "runs": rows,
                                },
                                indent=2,
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
    finally:
        if installed:
            command([adb, "-s", options.serial, "uninstall", PACKAGE])
        if original.get("package") and original.get("activity"):
            shell("am", "start", "-n", original["package"] + "/" + original["activity"])
    print(json.dumps(aggregate(rows), indent=2))


if __name__ == "__main__":
    main()
