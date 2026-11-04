"""Paired Jev executor ablation: pinned old source, sampling only, sampling + macro.

No scripted next-action decisions: all three arms use the real Jev policy. The host
supplies the same bounded task and independently verifies each result. No extra
text-model API is needed. Secrets and raw UI are excluded from saved records.
"""

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from accept_device import PACKAGE, ROOT, build_app, command
from benchmark_agent import TASKS

from android_use.jev import JevConfig
from android_use.runtime import Runtime

BASELINE = "2b930af652c7dd267233e6c26bb19e7d6cc5c740"
WORKER = """
import json, sys
from android_use.runtime import Runtime
request = json.load(sys.stdin)
runtime = Runtime(request['serial'])
runtime._connect()
counts = {'device_info': 0, 'legacy_window_size': 0}
device = runtime.backend.d
class Meter:
    def __getattr__(self, name):
        if name == 'info':
            counts['device_info'] += 1
        return getattr(device, name)
    def window_size(self):
        counts['legacy_window_size'] += 1
        return device.window_size()
runtime.backend._device = Meter()
result = runtime.call('execute_goal', **request['args'])
print(json.dumps({
    'ok': result.get('ok'), 'verified': result.get('verified'),
    'stop_reason': result.get('stop_reason'), 'error': result.get('error'),
    'steps': result.get('steps', []), 'decisions': result.get('decisions', []),
    'metrics': result.get('metrics', {}), 'rpc_counts': counts,
}))
"""


def source_hash(source):
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*.py")):
        digest.update(str(path.relative_to(source)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def summarize(rows):
    output = {}
    for task in sorted({r["task"] for r in rows}):
        output[task] = {}
        for arm in sorted({r["arm"] for r in rows}):
            selected = [r for r in rows if r["task"] == task and r["arm"] == arm]
            if not selected:
                continue
            output[task][arm] = {
                "successes": sum(r["success"] for r in selected),
                "runs": len(selected),
                "median_wall_seconds": statistics.median(r["elapsed_seconds"] for r in selected),
                **{
                    "median_" + metric: statistics.median(
                        r["metrics"].get(metric, 0) for r in selected
                    )
                    for metric in (
                        "elapsed_seconds",
                        "device_read_seconds",
                        "device_action_seconds",
                        "model_seconds",
                        "model_calls",
                        "stale_decisions",
                    )
                },
                "median_device_info_reads": statistics.median(
                    r["rpc_counts"]["device_info"] for r in selected
                ),
                "median_native_actions": statistics.median(r["native_actions"] for r in selected),
            }
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 4))
    parser.add_argument("--output", type=Path, default=Path("test-results/optimizations-v4.json"))
    options = parser.parse_args()
    JevConfig.from_env()
    runtime = Runtime(options.serial)
    if not runtime.call("ready").get("ready"):
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
        with tempfile.TemporaryDirectory(prefix="android-use-ablation-") as directory:
            temp = Path(directory)
            archive = temp / "baseline.tar"
            command(["git", "archive", BASELINE, "src", "--output", archive], cwd=ROOT)
            old = temp / "baseline"
            old.mkdir()
            command(["tar", "-xf", archive, "-C", old])
            environment = {
                "kind": "emulator" if options.serial.startswith("emulator-") else "device",
                "baseline_commit": BASELINE,
                "baseline_source_sha256": source_hash(old / "src/android_use"),
                "current_source_sha256": source_hash(ROOT / "src/android_use"),
                "fixture_sha256": hashlib.sha256(
                    (ROOT / "tests/device_app/MainActivity.java").read_bytes()
                ).hexdigest(),
                "tasks": "Chinese draft and UI-dependent four-step color workflow",
                "design": "Matched seeds, three arms, rotating order; same real Jev model and budgets",
                "timing": "Wall includes worker startup and independent verification; executor metrics exclude both. Install/reset/cleanup excluded.",
                "limits": "Synthetic native UI; no IME; small sample; network variability; no general app speed/accuracy claim",
            }
            apk = build_app(sdk, temp)
            command([adb, "-s", options.serial, "install", "-t", apk])
            installed = True
            tasks = [t for t in TASKS if t["name"] in {"draft_zh", "dynamic_workflow_en"}]
            for repeat in range(options.repeats):
                for index, task in enumerate(tasks):
                    arms = ["baseline", "sampling_only", "sampling_macro"]
                    offset = (repeat + index) % len(arms)
                    arms = arms[offset:] + arms[:offset]
                    for arm in arms:
                        seed = 41 + repeat * 997
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
                            str(seed),
                        )
                        if not runtime.call("wait", selector={"description": "open-profile"})[
                            "condition_met"
                        ]:
                            raise RuntimeError("Fixture reset failed")
                        args = {
                            "goal": task["goal"],
                            "package": PACKAGE,
                            "texts": task.get("texts", []),
                            "completion": task["completion"],
                            "actions": ["tap", "type_text", "wait"],
                            "max_steps": 20,
                            "max_model_calls": 30,
                            "timeout_seconds": 60.0,
                            "observe": "tree",
                        }
                        if arm != "baseline":
                            args["input_macros"] = arm == "sampling_macro"
                        env = dict(os.environ)
                        env["PYTHONPATH"] = str((old if arm == "baseline" else ROOT) / "src")
                        began = time.monotonic()
                        worker = subprocess.run(
                            [sys.executable, "-c", WORKER],
                            input=json.dumps({"serial": options.serial, "args": args}),
                            capture_output=True,
                            text=True,
                            env=env,
                            timeout=90,
                            check=False,
                        )
                        if worker.returncode:
                            # Abort bootstrap failures; prior completed trials remain on disk.
                            # The worker never prints requests, credentials or raw UI.
                            raise RuntimeError("Worker bootstrap failed: " + worker.stderr[-4000:])
                        result = json.loads(worker.stdout)
                        check = runtime.call("find", selector=task["completion"])
                        success = result.get("verified") is True and any(
                            n["in_viewport"] for n in check.get("matches", [])
                        )
                        leaves = [
                            child
                            for step in result["steps"]
                            for child in step.get("substeps", [step])
                        ]
                        row = {
                            "task": task["name"],
                            "repeat": repeat + 1,
                            "seed": seed,
                            "arm": arm,
                            "success": success,
                            "elapsed_seconds": time.monotonic() - began,
                            "native_actions": sum(
                                s["op"] != "wait_local" and s["state"] == "executed" for s in leaves
                            ),
                            **result,
                        }
                        rows.append(row)
                        options.output.parent.mkdir(parents=True, exist_ok=True)
                        options.output.write_text(
                            json.dumps(
                                {
                                    "environment": environment,
                                    "summary": summarize(rows),
                                    "runs": rows,
                                },
                                ensure_ascii=False,
                                indent=2,
                            )
                            + "\n"
                        )
                        print(
                            json.dumps(
                                {
                                    k: row[k]
                                    for k in ("task", "repeat", "arm", "success", "stop_reason")
                                }
                            ),
                            flush=True,
                        )
    finally:
        if installed:
            command([adb, "-s", options.serial, "uninstall", PACKAGE])
        if original.get("package") and original.get("activity"):
            shell("am", "start", "-n", original["package"] + "/" + original["activity"])
    print(json.dumps(summarize(rows), indent=2))


if __name__ == "__main__":
    main()
