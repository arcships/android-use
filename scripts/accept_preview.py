"""Opt-in emulator-only moving-frame acceptance; never selects a physical device.

Runs an uninterrupted stream beyond the former 170-second cutoff, then forces
short legacy segments through the same ADB pipe and decoder. No UI tree polling,
phone screenshots, personal accounts, API keys or saved video are used.
"""

import argparse
import hashlib
import json
import os
import re
import statistics
import tempfile
import threading
import time
from pathlib import Path

from accept_device import PACKAGE, build_app, command

from android_use.backend import Backend
from android_use.screen import ScreenHub


def measure(serial, seconds, limit, inject_display_failure=False):
    published = []
    began = time.monotonic()

    class AcceptanceBackend(Backend):
        def shell(self, args, **kwargs):
            if (
                inject_display_failure
                and 5 < time.monotonic() - began < 8
                and (args[0] == "wm" or args == ["dumpsys", "display"])
            ):
                raise TimeoutError("Injected display-metadata failure")
            return super().shell(args, **kwargs)

    class MeasuredHub(ScreenHub):
        def _publish(self, pipeline, raw, size):
            with self.lock:
                seq = self.seq
                super()._publish(pipeline, raw, size)
                if self.seq > seq:
                    published.append(time.monotonic())

    hub = MeasuredHub(
        serial, "preview-acceptance", recording_time_limit=limit, backend=AcceptanceBackend(serial)
    )
    blocked_pictures = 0
    frames, changes, times, pipelines, decoders, encoders, errors = (
        [],
        set(),
        [],
        set(),
        set(),
        set(),
        set(),
    )
    stopped = threading.Event()

    def sample_encoders():
        # Metadata collection must not stall the viewer's frame-request loop.
        while not stopped.wait(0.25):
            pipeline = hub.pipeline
            if pipeline:
                try:
                    value = hub.backend.shell(["cat", pipeline.pid_path], timeout=2)
                    if value.split():
                        encoders.add(value.split()[0])
                except Exception:
                    pass

    sampler = threading.Thread(target=sample_encoders, daemon=True)
    hub.start()
    sampler.start()
    began = time.monotonic()
    last_seq = 0
    first = None
    try:
        while time.monotonic() - began < seconds:
            data = hub.frame(after_seq=last_seq)
            if data["error"]:
                errors.add(data["error"])
            if data["frame"]:
                now = time.monotonic()
                if first is None:
                    print(
                        json.dumps({"phase_limit_seconds": limit, "first_frame": True}), flush=True
                    )
                first = first or now
                frames.append(data["seq"])
                times.append(now)
                last_seq = data["seq"]
                changes.add(hashlib.sha256(data["frame"]["data"].encode()).hexdigest())
                if not data["interaction_ready"]:
                    blocked_pictures += 1
            if first is None and time.monotonic() - began > 15:
                raise AssertionError(
                    {
                        "connected": data["connected"],
                        "paused": data["paused"],
                        "error": data["error"],
                    }
                )
            pipeline = hub.pipeline
            if pipeline:
                pipelines.add(pipeline.adb.pid)
                decoders.add(pipeline.decoder.pid)
            time.sleep(0.03)
        gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
        publish_gaps = [b - a for a, b in zip(published, published[1:], strict=False)]
        result = {
            "mode": "unlimited" if hub.recording_time_limit == 0 else "continuous_segments",
            "recording_limit_seconds": hub.recording_time_limit,
            "duration_seconds": round(time.monotonic() - began, 3),
            "first_frame_seconds": round(first - began, 3) if first else None,
            "frames": len(frames),
            "distinct_pictures": len(changes),
            "adb_pipelines": len(pipelines),
            "decoder_processes": len(decoders),
            "encoder_processes_observed": len(encoders),
            "frame_gap_median_seconds": round(statistics.median(gaps), 3) if gaps else None,
            "frame_gap_max_seconds": round(max(gaps), 3) if gaps else None,
            "publisher_gap_max_seconds": round(max(publish_gaps), 3) if publish_gaps else None,
            "errors": sorted(errors),
            "health_failures": hub.health_failures,
            "metadata_failures": hub.metadata_failures,
            "last_health_failure": hub.last_health_failure,
            "injected_display_failure": inject_display_failure,
            "pictures_with_controls_blocked": blocked_pictures,
            "interaction_ready_at_end": hub.frame()["interaction_ready"],
            "stream_failures": hub.stream_failures,
        }
        result["passed"] = bool(
            len(frames) > seconds
            and len(changes) > seconds
            and len(pipelines) == len(decoders) == 1
            and not errors
            and gaps
            and max(gaps) < 2
            and max(publish_gaps) < 2
            and (not limit or len(encoders) >= 3)
            and (limit is not None or hub.recording_time_limit == 0)
            and (
                not inject_display_failure
                or (hub.metadata_failures > 0 and blocked_pictures > 0 and hub.geometry_ready)
            )
        )
        return result
    finally:
        stopped.set()
        hub.close()
        sampler.join(timeout=3)
        assert not sampler.is_alive(), "Encoder metadata sampler did not stop"
        assert hub.pipeline is None, "Preview pipeline was not closed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--seconds", type=int, default=190)
    parser.add_argument("--legacy-seconds", type=int, default=25)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inject-display-failure", action="store_true")
    options = parser.parse_args()
    if not options.serial.startswith("emulator-"):
        parser.error("This acceptance only permits explicit emulator serials")
    if not 10 <= options.seconds <= 600 or not 10 <= options.legacy_seconds <= 120:
        parser.error("Acceptance duration out of bounds")
    backend = Backend(options.serial)
    assert backend.shell(["getprop", "ro.kernel.qemu"]) == "1", "Not a review emulator"
    assert not backend.shell(["pm", "list", "packages", PACKAGE]), "Fixture already installed"
    sdk = Path(os.environ.get("ANDROID_SDK_ROOT", str(Path.home() / "Library/Android/sdk")))
    adb = os.environ.get("ANDROID_USE_ADB") or str(sdk / "platform-tools/adb")
    os.environ["ANDROID_USE_ADB"] = adb
    with tempfile.TemporaryDirectory(prefix="android-use-preview-acceptance-") as folder:
        os.environ["ANDROID_USE_STATE_DIR"] = str(Path(folder) / "state")
        apk = build_app(sdk, Path(folder))
        try:
            command([adb, "-s", options.serial, "install", "-t", str(apk)])
            command(
                [
                    adb,
                    "-s",
                    options.serial,
                    "shell",
                    "am",
                    "start",
                    "-W",
                    "-n",
                    PACKAGE + "/.MainActivity",
                    "--ez",
                    "video",
                    "true",
                ]
            )
            fixture_pid = backend.shell(["pidof", PACKAGE])
            assert fixture_pid.isdecimal(), "Fixture PID is ambiguous"
            report = {
                "kind": "emulator moving-frame continuity acceptance",
                "unlimited": measure(
                    options.serial, options.seconds, None, options.inject_display_failure
                ),
                "legacy": measure(options.serial, options.legacy_seconds, 2),
            }
            # This tag is emitted only by this permission-free fixture, never by
            # phone apps. It helps distinguish source UI stalls from capture gaps.
            fixture_log = backend.shell(
                [
                    "logcat",
                    "--pid",
                    fixture_pid,
                    "-d",
                    "-v",
                    "monotonic",
                    "-s",
                    "AndroidUsePreview:I",
                    "*:S",
                ]
            )
            report["fixture_ui_gaps_over_500ms"] = [
                int(value) for value in re.findall(r"UI_CALLBACK_GAP_MS=(\d+)", fixture_log)
            ]
            options.output.parent.mkdir(parents=True, exist_ok=True)
            options.output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            assert all(report[name]["passed"] for name in ("unlimited", "legacy"))
        finally:
            command([adb, "-s", options.serial, "uninstall", PACKAGE])
            assert not backend.shell(["pm", "list", "packages", PACKAGE])


if __name__ == "__main__":
    main()
