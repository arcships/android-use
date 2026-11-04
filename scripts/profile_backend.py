"""Read-only warm query comparison; requires a ready, unlocked Android service."""

import argparse
import json
import statistics
import time
from pathlib import Path

from android_use.runtime import Runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--output", type=Path, default=Path("test-results/backend-profile.json"))
    args = parser.parse_args()
    if not 3 <= args.samples <= 30:
        parser.error("samples must be 3..30")
    runtime = Runtime(args.serial)
    if not runtime.call("ready").get("ready"):
        raise RuntimeError("Run setup(status/start), then ready")
    backend = runtime.backend
    methods = {
        "legacy_size": backend.d.window_size,
        "fast_size": backend.size,
        "legacy_foreground": backend.d.app_current,
        "fast_foreground": backend.foreground,
    }
    report = {"samples": args.samples, "method": "sequential warm read-only queries", "methods": {}}
    values = {}
    for name, method in methods.items():
        times = []
        for _ in range(args.samples):
            runtime._usable()
            start = time.monotonic()
            values[name] = method()
            times.append(time.monotonic() - start)
        report["methods"][name] = {
            "median_seconds": statistics.median(times),
            "min_seconds": min(times),
            "max_seconds": max(times),
        }
    assert tuple(values["legacy_size"]) == tuple(values["fast_size"])

    def component(value):
        package, activity = value["package"], value["activity"]
        return package, package + activity if activity.startswith(".") else activity

    assert component(values["legacy_foreground"]) == component(values["fast_foreground"])
    report["outputs_equivalent"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
