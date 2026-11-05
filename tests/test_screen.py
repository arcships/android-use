import base64
import io
import time

import pytest
from PIL import Image

from android_use.backend import UseError
from android_use.screen import RECORDER_PROBE, JPEGParser, ScreenHub, unlimited_recording


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


class Video:
    instances = []

    def __init__(self, serial, publish, failed, size=None, time_limit=170):
        self.publish, self.failed, self.size = publish, failed, size
        self.closed = False
        self.time_limit = time_limit
        self.instances.append(self)

    def start(self):
        buffer = io.BytesIO()
        Image.new("RGB", (54, 120)).save(buffer, format="JPEG")
        self.publish(self, buffer.getvalue(), (54, 120))

    def close(self):
        self.closed = True


class Metadata:
    is_locked = False
    offline = False
    orientation = 0
    recording_help = "Set to 0\nto remove the time limit."
    recorder = "/system/bin/screenrecord"
    display_failed = False

    def locked(self):
        if self.offline:
            raise ConnectionError()
        return self.is_locked

    def shell(self, args, **kwargs):
        if self.display_failed and args[0] in ("wm", "dumpsys"):
            raise TimeoutError("Display metadata timed out")
        if args == ["sh", "-c", "screenrecord --help 2>&1"]:
            return self.recording_help
        if args == RECORDER_PROBE:
            return self.recorder
        assert args in (["wm", "size"], ["getprop", "ro.product.model"], ["dumpsys", "display"])
        return (
            "Physical size: 1080x2400"
            if args[0] == "wm"
            else f"orientation={self.orientation}"
            if args[0] == "dumpsys"
            else "Test Android"
        )


def make_hub(monkeypatch, tmp_path):
    monkeypatch.setenv("ANDROID_USE_STATE_DIR", str(tmp_path))
    Video.instances = []
    metadata = Metadata()
    hub = ScreenHub("usb", "hardware", backend=metadata, pipeline_factory=Video)
    return hub, metadata


def test_split_jpeg_and_oversized_input_recovery():
    parser = JPEGParser(limit=100)
    assert parser.feed(b"header\xff") == []
    assert parser.feed(b"\xd8abc\xff") == []
    assert parser.feed(b"\xd9tail") == [b"\xff\xd8abc\xff\xd9"]
    assert parser.feed(b"\xff\xd8" + b"x" * 200) == []
    assert len(parser.buffer) <= 100
    assert parser.feed(b"\xff\xd8ok\xff\xd9") == [b"\xff\xd8ok\xff\xd9"]


def test_open_without_visible_viewer_does_not_start_capture(monkeypatch, tmp_path):
    hub, _ = make_hub(monkeypatch, tmp_path)
    hub.start()
    try:
        time.sleep(0.25)
        assert not Video.instances
        assert hub.frame_data is None
    finally:
        hub.close()


def test_cached_video_does_not_need_phone_action_lock_or_snapshots(monkeypatch, tmp_path):
    from android_use.backend import device_lock

    hub, _ = make_hub(monkeypatch, tmp_path)
    hub.start()
    try:
        hub.frame()
        until(lambda: hub.frame_data is not None)
        with device_lock("hardware"):
            result = hub.frame()
        assert result["transport"] == "adb-screenrecord-h264"
        assert base64.b64decode(result["frame"]["data"]).startswith(b"\xff\xd8")
        assert hub.frame(after_seq=result["seq"])["frame"] is None
    finally:
        hub.close()


def test_pause_clears_frames_stops_stream_and_refuses_late_publish(monkeypatch, tmp_path):
    hub, _ = make_hub(monkeypatch, tmp_path)
    hub.start()
    try:
        hub.frame()
        until(lambda: hub.frame_data is not None)
        pipeline = hub.pipeline
        hub.pause_path.touch()
        data = hub.frame()
        assert data["paused"] and data["pause_reason"] == "authentication"
        assert data["frame"] is None and not data["frame_available"]
        assert pipeline.closed
        hub._publish(pipeline, b"late secret frame", (54, 120))
        assert hub.frame_data is None
        hub.pause_path.unlink()
        hub.frame()
        until(lambda: hub.frame_data is not None)
    finally:
        hub.close()


def test_locked_phone_never_starts_capture(monkeypatch, tmp_path):
    hub, metadata = make_hub(monkeypatch, tmp_path)
    metadata.is_locked = True
    hub.start()
    try:
        hub.frame()
        until(lambda: hub.connected)
        assert hub.frame()["pause_reason"] == "device_locked"
        assert not Video.instances
    finally:
        hub.close()


def test_visibility_lease_expiration_stops_capture(monkeypatch, tmp_path):
    hub, _ = make_hub(monkeypatch, tmp_path)
    hub.start()
    try:
        hub.frame()
        until(lambda: hub.frame_data is not None)
        pipeline = hub.pipeline
        hub.lease_until = time.monotonic() - 1
        until(lambda: pipeline.closed)
        assert hub.frame_data is None
    finally:
        hub.close()


def test_rotation_restarts_encoder_with_new_geometry(monkeypatch, tmp_path):
    hub, metadata = make_hub(monkeypatch, tmp_path)
    hub.start()
    try:
        hub.frame()
        until(lambda: hub.pipeline is not None)
        old = hub.pipeline
        metadata.orientation = 1
        hub.last_health = 0
        until(lambda: hub.viewport == [2400, 1080])
        until(lambda: hub.pipeline is not None and hub.pipeline is not old)
        assert old.closed and hub.pipeline.size == [2400, 1080]
    finally:
        hub.close()


def test_rollover_restarts_automatically_and_error_clears_only_on_new_frame(monkeypatch, tmp_path):
    hub, _ = make_hub(monkeypatch, tmp_path)
    hub.start()
    try:
        hub.frame()
        until(lambda: hub.frame_data is not None)
        old, seq = hub.pipeline, hub.seq
        # The replacement encoder starts but initially has no decoded output.
        monkeypatch.setattr(Video, "start", lambda self: None)
        hub._failed(old)
        data = hub.frame(after_seq=seq)
        assert data["connected"] and data["error"] == "video_stream_ended"
        assert data["frame_available"] and old.closed
        hub._publish(old, b"late", (54, 120))
        assert hub.seq == seq
        until(lambda: hub.pipeline is not None)
        assert hub.frame()["error"] == "video_stream_ended"
        hub._publish(hub.pipeline, b"new", (54, 120))
        data = hub.frame(after_seq=seq)
        assert data["error"] is None and data["seq"] > seq and data["frame"]
    finally:
        hub.close()


def test_documented_unlimited_recording_is_used(monkeypatch, tmp_path):
    hub, _ = make_hub(monkeypatch, tmp_path)
    hub.start()
    try:
        hub.frame()
        until(lambda: hub.frame_data is not None)
        assert hub.pipeline.time_limit == 0
        assert hub.frame()["video"]["recording_mode"] == "unlimited"
    finally:
        hub.close()


def test_legacy_recording_uses_continuous_segments(monkeypatch, tmp_path):
    hub, metadata = make_hub(monkeypatch, tmp_path)
    metadata.recording_help = "Maximum recording time: 180 seconds"
    hub.start()
    try:
        hub.frame()
        until(lambda: hub.frame_data is not None)
        assert hub.pipeline.time_limit == 170
        assert hub.frame()["video"]["recording_mode"] == "continuous_segments"
    finally:
        hub.close()


def test_capability_requires_explicit_help_not_version_assumption():
    assert unlimited_recording("Set to 0\n    to remove the time limit.")
    assert not unlimited_recording("Android 16; Default is 180 seconds.")
    assert not unlimited_recording("")


def test_close_during_health_probe_cannot_restart_capture(monkeypatch, tmp_path):
    import threading

    hub, metadata = make_hub(monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = metadata.locked

    def locked():
        entered.set()
        assert release.wait(2)
        return original()

    metadata.locked = locked
    hub.frame()
    hub.start()
    assert entered.wait(2)
    hub.close()
    release.set()
    time.sleep(0.3)
    assert hub.pipeline is None and not Video.instances and hub.frame_data is None


def test_optional_capability_probe_failure_keeps_healthy_legacy_stream(monkeypatch, tmp_path):
    hub, metadata = make_hub(monkeypatch, tmp_path)
    original = metadata.shell

    def shell(args, **kwargs):
        if args == ["sh", "-c", "screenrecord --help 2>&1"]:
            raise TimeoutError()
        return original(args, **kwargs)

    metadata.shell = shell
    hub.frame()
    hub.start()
    try:
        until(lambda: hub.frame_data is not None)
        assert hub.connected and hub.pipeline.time_limit == 170 and hub.health_failures == 0
    finally:
        hub.close()


def test_pipeline_keeps_one_decoder_and_samples_real_arrival_time(monkeypatch):
    from types import SimpleNamespace

    from android_use.screen import VideoPipeline

    calls, cleanup = [], []

    def popen(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=io.BytesIO(), stderr=None, poll=lambda: 0)

    monkeypatch.setattr("android_use.screen.shutil.which", lambda name: "/fake/" + name)
    monkeypatch.setattr("android_use.screen.subprocess.Popen", popen)
    monkeypatch.setattr(
        "android_use.screen.subprocess.run", lambda args, **kw: cleanup.append(args)
    )
    monkeypatch.setattr(
        "android_use.screen.threading.Thread", lambda **kw: SimpleNamespace(start=lambda: None)
    )
    pipeline = VideoPipeline("emulator-test", lambda *a: None, lambda *a: None, time_limit=0)
    pipeline.start()
    assert len(calls) == 2
    script = calls[0][-1]
    assert "--time-limit 0" in script and "while :; do" in script and "trap cleanup EXIT" in script
    decoder = calls[1]
    assert decoder[: decoder.index("-i")].count("-threads") == 1
    assert decoder[decoder.index("-threads") + 1] == "1"
    assert "time(0)" in decoder[decoder.index("-vf") + 1]
    assert decoder[decoder.index("-fps_mode") + 1] == "passthrough"
    pipeline.close()
    assert pipeline.pid_path + ".owner" in cleanup[0][-1]
    assert "/proc/$p/stat" in cleanup[0][-1]


def test_geometry_failure_keeps_video_but_blocks_controls_until_metadata_recovers(
    monkeypatch, tmp_path
):
    hub, metadata = make_hub(monkeypatch, tmp_path)
    hub.start()
    try:
        hub.frame()
        until(lambda: hub.frame()["frame"])
        pipeline = hub.pipeline
        metadata.display_failed = True
        hub._health()
        result = hub.frame()
        assert result["connected"] and result["frame_available"]
        assert not result["interaction_ready"]
        with pytest.raises(UseError, match="Wait for current video"):
            hub.require_interaction_ready()
        assert hub.pipeline is pipeline and not pipeline.closed
        assert result["video"]["metadata_failures"] == 1
        assert result["video"]["health_failures"] == 0
        metadata.display_failed = False
        hub._health()
        assert hub.frame()["interaction_ready"]
        hub.require_interaction_ready()
        assert hub.pipeline is pipeline
        metadata.offline = True
        hub._health()
        assert not hub.frame()["frame_available"]
        assert pipeline.closed
        assert hub.frame()["video"]["last_health_failure"] == "lock_state"
    finally:
        hub.close()


def test_initial_unknown_geometry_cannot_start_capture(monkeypatch, tmp_path):
    hub, metadata = make_hub(monkeypatch, tmp_path)
    metadata.display_failed = True
    hub.start()
    try:
        hub.frame()
        until(lambda: hub.metadata_failures > 0)
        assert not hub.frame()["interaction_ready"]
        assert not Video.instances
    finally:
        hub.close()


def test_missing_screenrecord_is_reported_without_retrying(monkeypatch, tmp_path):
    hub, metadata = make_hub(monkeypatch, tmp_path)
    metadata.recorder = "missing"
    calls = []
    original = metadata.shell

    def shell(args, **kwargs):
        calls.append(args)
        return original(args, **kwargs)

    metadata.shell = shell
    hub.frame()
    hub.start()
    try:
        until(lambda: hub.frame()["error"] == "preview_unsupported_device")
        time.sleep(1.3)  # Spans another health interval.
        result = hub.frame()
        assert not Video.instances and hub.pipeline is None and not result["capturing"]
        assert result["connected"] and result["video"]["recorder_available"] is False
        assert result["video"]["pipeline_starts"] == 0 and result["video"]["stream_failures"] == 0
        # Probed once and cached; the recording-help probe is skipped entirely.
        assert calls.count(RECORDER_PROBE) == 1
        assert ["sh", "-c", "screenrecord --help 2>&1"] not in calls
        with pytest.raises(UseError):
            hub.require_interaction_ready()
    finally:
        hub.close()


def test_recorder_probe_failure_is_unknown_not_unsupported(monkeypatch, tmp_path):
    hub, metadata = make_hub(monkeypatch, tmp_path)
    original = metadata.shell
    failures = []

    def shell(args, **kwargs):
        if args == RECORDER_PROBE and not failures:
            failures.append(args)
            raise TimeoutError()
        return original(args, **kwargs)

    metadata.shell = shell
    hub.frame()
    hub.start()
    try:
        until(lambda: hub.frame_data is not None)
        until(lambda: hub.recorder is True)
        assert hub.frame()["error"] is None
    finally:
        hub.close()
