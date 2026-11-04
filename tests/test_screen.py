import base64
import io
import time

from PIL import Image

from android_use.screen import JPEGParser, ScreenHub


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


class Video:
    instances = []

    def __init__(self, serial, publish, failed, size=None):
        self.publish, self.failed, self.size = publish, failed, size
        self.closed = False
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

    def locked(self):
        if self.offline:
            raise ConnectionError()
        return self.is_locked

    def shell(self, args, **kwargs):
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
