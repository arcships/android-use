"""Independent, memory-only H.264 preview. Never uses UI snapshots or screenshots."""

import base64
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

from PIL import Image

from .backend import Backend, UseError, device_lock, state_dir

LEASE_SECONDS = 5
MAX_FRAME_BYTES = 3 * 1024 * 1024
# Replace older daemons so their browser UI/recovery behavior is upgraded too.
PREVIEW_PROTOCOL_VERSION = 3
PREVIEW_IMPLEMENTATION_VERSION = 4
PREVIEW_BOOTSTRAP_ENV = "ANDROID_USE_PREVIEW_BOOTSTRAP"
# Exit status is always 0, so a missing binary is distinguishable from an ADB failure.
RECORDER_PROBE = ["sh", "-c", "command -v screenrecord || echo missing"]


def unlimited_recording(help_text):
    """Only enable the documented capability, not an Android-version guess."""
    return bool(re.search(r"set to 0\s+to remove the time limit", help_text, re.I))


def device_paths(key):
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return state_dir() / (digest + ".preview.json"), state_dir() / (digest + ".paused")


class JPEGParser:
    def __init__(self, limit=MAX_FRAME_BYTES):
        self.buffer = bytearray()
        self.limit = limit

    def feed(self, data):
        self.buffer.extend(data)
        result = []
        while True:
            start = self.buffer.find(b"\xff\xd8")
            if start < 0:
                self.buffer = self.buffer[-1:] if self.buffer[-1:] == b"\xff" else bytearray()
                break
            if start:
                del self.buffer[:start]
            end = self.buffer.find(b"\xff\xd9", 2)
            if end < 0:
                if len(self.buffer) > self.limit:
                    self.buffer = bytearray()
                break
            raw = bytes(self.buffer[: end + 2])
            del self.buffer[: end + 2]
            if len(raw) <= self.limit:
                result.append(raw)
        return result


class VideoPipeline:
    def __init__(self, serial, publish, failed, size=None, time_limit=170):
        if type(time_limit) is not int or not 0 <= time_limit <= 170:
            raise ValueError("Invalid recording limit")
        self.serial, self.publish, self.failed = serial, publish, failed
        self.adb = self.decoder = None
        self.stopped = threading.Event()
        self.size = size or [1080, 2400]
        self.time_limit = time_limit
        self.pid_path = "/data/local/tmp/android-use-video-" + uuid.uuid4().hex + ".pid"
        self.decoded_frames = 0
        self.bytes_received = 0

    def start(self):
        adb = os.environ.get("ANDROID_USE_ADB") or shutil.which("adb")
        ffmpeg = os.environ.get("ANDROID_USE_FFMPEG") or shutil.which("ffmpeg")
        if not adb or not ffmpeg:
            raise UseError(
                "preview_dependency_missing", "Live preview requires adb and ffmpeg on PATH"
            )
        # Bound bandwidth while retaining display aspect ratio. No video files are written.
        scale = min(1, 1200 / max(self.size))
        width, height = [max(2, int(n * scale) // 2 * 2) for n in self.size]
        # Keep the ADB pipe and decoder alive even on legacy screenrecord builds.
        # Each segment supplies new SPS/PPS/keyframes on the same Annex-B stream.
        script = (
            "p=; cleanup() { trap - EXIT; "
            'if [ -n "$p" ] && '
            '[ "$(cat /proc/$p/comm 2>/dev/null)" = screenrecord ] && '
            '[ "$(cut -d " " -f 4 /proc/$p/stat 2>/dev/null)" = "$$" ]; '
            'then kill "$p" 2>/dev/null; wait "$p" 2>/dev/null; fi; '
            f"rm -f {self.pid_path} {self.pid_path}.owner; }}; "
            "trap cleanup EXIT; trap 'exit 0' HUP INT TERM; "
            's=$(cut -d " " -f 22 /proc/$$/stat); '
            f'printf "%s %s\\n" "$$" "$s" > {self.pid_path}.owner; '
            "while :; do "
            "read u rest < /proc/uptime; began=${u%.*}; "
            f"screenrecord --output-format=h264 --bit-rate 2000000 --size {width}x{height} "
            f"--time-limit {self.time_limit} - & p=$!; "
            's=$(cut -d " " -f 22 /proc/$p/stat); '
            f'printf "%s %s\\n" "$p" "$s" > {self.pid_path}; '
            'wait "$p"; r=$?; p=; [ "$r" -eq 0 ] || exit "$r"; '
            "read u rest < /proc/uptime; ended=${u%.*}; "
            # An immediate successful exit is still a failed capture. Avoid a
            # device-side spawn loop; let the host's bounded backoff handle it.
            f"[ {self.time_limit} -gt 0 ] && "
            f'[ "$((ended - began))" -ge {max(1, self.time_limit - 1)} ] || exit 1; done'
        )
        self.adb = subprocess.Popen(
            [
                adb,
                "-s",
                self.serial,
                "exec-out",
                "sh",
                "-c",
                script,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            self.decoder = subprocess.Popen(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-probesize",
                    "32",
                    "-analyzeduration",
                    "0",
                    "-flags",
                    "low_delay",
                    # Decoder frame threading otherwise queues sparse screen
                    # updates behind multiple frames on high-core-count hosts.
                    "-threads",
                    "1",
                    "-f",
                    "h264",
                    "-i",
                    "pipe:0",
                    "-vf",
                    # Raw Annex-B can have synthetic 25fps timestamps even if
                    # content changes at 10fps. Sample decoder arrival time,
                    # never manufacture duplicate frames to fill a clock gap.
                    "select=if(gte(time(0)-ld(0)\\,0.1)+lt(time(0)\\,ld(0))"
                    "\\,st(0\\,time(0))*0+1\\,0),scale=out_range=full",
                    "-pix_fmt",
                    "yuvj420p",
                    "-threads",
                    "1",
                    "-fps_mode",
                    "passthrough",
                    "-flush_packets",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "mjpeg",
                    "-q:v",
                    "5",
                    "pipe:1",
                ],
                stdin=self.adb.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            self.close()
            raise
        self.adb.stdout.close()
        threading.Thread(target=self._read, daemon=True, name="android-use-video").start()

    def _read(self):
        parser = JPEGParser()
        try:
            while not self.stopped.is_set():
                chunk = os.read(self.decoder.stdout.fileno(), 65536)
                if not chunk:
                    break
                self.bytes_received += len(chunk)
                for raw in parser.feed(chunk):
                    with Image.open(io.BytesIO(raw)) as picture:
                        size = picture.size
                    self.decoded_frames += 1
                    self.publish(self, raw, size)
        except (OSError, ValueError):
            pass
        finally:
            if not self.stopped.is_set():
                self.failed(self)

    def close(self):
        self.stopped.set()
        # ADB socket closure need not kill screenrecord. Match this pipeline's private
        # PID record, kernel start time and executable before stopping the device child.
        adb = os.environ.get("ANDROID_USE_ADB") or shutil.which("adb")
        if self.adb is not None and adb:
            cleanup = (
                # Stop the owner first so no new segment can be launched while
                # cleaning up its last child. PID/start-time checks prevent reuse.
                f"if [ -r {self.pid_path}.owner ]; then read p s < {self.pid_path}.owner; "
                'case "$p:$s" in *[!0-9:]*|:*) exit 0;; esac; '
                'if [ -n "$p" ] && [ -n "$s" ] && [ "$p" -gt 1 ] && '
                '[ "$(cat /proc/$p/comm 2>/dev/null)" = sh ] && '
                '[ "$(cut -d " " -f 22 /proc/$p/stat 2>/dev/null)" = "$s" ]; '
                'then kill "$p"; fi; fi; '
                f"if [ -r {self.pid_path} ]; then read p s < {self.pid_path}; "
                'case "$p:$s" in *[!0-9:]*|:*) exit 0;; esac; '
                'if [ -n "$p" ] && [ -n "$s" ] && [ "$p" -gt 1 ] && '
                '[ "$(cat /proc/$p/comm 2>/dev/null)" = screenrecord ] && '
                '[ "$(cut -d " " -f 22 /proc/$p/stat 2>/dev/null)" = "$s" ]; '
                f'then kill "$p"; fi; fi; rm -f {self.pid_path} {self.pid_path}.owner'
            )
            try:
                subprocess.run(
                    [adb, "-s", self.serial, "shell", cleanup],
                    capture_output=True,
                    timeout=2,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        # Kill only children started by this pipeline, never processes found by port/name.
        for child in (self.adb, self.decoder):
            if child is not None:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=1)
                for stream in (child.stdout, child.stderr):
                    if stream:
                        stream.close()


class ScreenHub:
    def __init__(
        self,
        serial,
        key,
        *,
        backend=None,
        pipeline_factory=VideoPipeline,
        recording_time_limit=None,
    ):
        self.serial, self.key = serial, key
        self.backend = backend or Backend(serial)
        self.pipeline_factory = pipeline_factory
        self.pause_path = device_paths(key)[1]
        self.lock = threading.RLock()
        self.pipeline = None
        self.frame_data = None
        self.frame_size = None
        self.seq = 0
        self.stream_id = uuid.uuid4().hex
        self.received = None
        self.lease_until = 0
        self.last_activity = time.monotonic()
        self.last_health = 0
        self.locked = True  # Fail closed until the independent metadata check completes.
        self.connected = False
        self.closed = threading.Event()
        self.retry_at = 0
        self.error = None
        self.model = "Android"
        self.viewport = None
        self.geometry_ready = False
        self.recording_time_limit = recording_time_limit
        self.recorder = None  # None until probed; False when the build lacks screenrecord.
        self.failures = 0
        self.pipeline_starts = 0
        self.health_failures = 0
        self.metadata_failures = 0
        self.last_health_failure = None
        self.stream_failures = 0

    def start(self):
        threading.Thread(target=self._watch, daemon=True, name="android-use-preview-watch").start()

    def _publish(self, pipeline, raw, size):
        with self.lock:
            if (
                pipeline is self.pipeline
                and not self.closed.is_set()
                and not self.pause_path.exists()
                and not self.locked
                and time.monotonic() < self.lease_until
            ):
                self.seq += 1
                self.frame_data, self.frame_size = raw, size
                self.received = time.monotonic()
                # Starting an encoder is not recovery: only a decoded frame is.
                self.error = None
                self.failures = 0

    def _failed(self, pipeline):
        with self.lock:
            if pipeline is self.pipeline:
                self.stream_failures += 1
                self.error = "video_stream_ended"
                # First recovery is prompt; repeated start failures remain bounded.
                self.retry_at = time.monotonic() + min(3, 0.1 * 2 ** min(self.failures, 5))
                self.failures += 1
                self._stop_video(clear=False)

    def _stop_video(self, *, clear=True):
        pipeline, self.pipeline = self.pipeline, None
        if pipeline:
            pipeline.close()
        if clear:
            self.frame_data = self.frame_size = self.received = None

    def _health(self):
        try:
            locked = self.backend.locked()
        except Exception:
            with self.lock:
                self.health_failures += 1
                self.last_health_failure = "lock_state"
                self.connected = False
                self.geometry_ready = False
                self.last_health = time.monotonic()
            return
        if locked:
            with self.lock:
                self.locked, self.connected = True, True
                self.geometry_ready = False
                self._stop_video()
                self.last_health = time.monotonic()
            return
        size = None
        try:
            # These are display/device metadata reads, never UiAutomator snapshots.
            raw = self.backend.shell(["wm", "size"], timeout=2)
            sizes = re.findall(r"(\d+)x(\d+)", raw)
            size = [int(x) for x in sizes[-1]] if sizes else None
            rotation = re.search(
                r"orientation=(\d+)", self.backend.shell(["dumpsys", "display"], timeout=2)
            )
            if size and rotation and int(rotation[1]) % 2:
                size.reverse()
            if not size or not rotation or int(rotation[1]) not in range(4):
                raise ValueError("Unknown display geometry")
        except Exception:
            with self.lock:
                self.metadata_failures += 1
                self.last_health_failure = "display_metadata"
            size = None
        if self.model == "Android":
            try:
                self.model = (
                    self.backend.shell(["getprop", "ro.product.model"], timeout=2) or self.model
                )
            except Exception:
                pass  # A missing label cannot disconnect a healthy video stream.
        if self.recorder is None:
            try:
                # Vendor builds such as HarmonyOS 4.x can omit screenrecord entirely;
                # its shell error would otherwise be piped into the decoder as video.
                found = self.backend.shell(RECORDER_PROBE, timeout=2)
                self.recorder = found not in ("", "missing")
            except Exception:
                pass  # Unknown, not absent: keep the previous behavior and probe again.
        if self.recording_time_limit is None and self.recorder is not False:
            try:
                # AOSP prints usage on stderr; redirect it on the device.
                help_text = self.backend.shell(["sh", "-c", "screenrecord --help 2>&1"], timeout=2)
            except Exception:
                help_text = ""
            self.recording_time_limit = 0 if unlimited_recording(help_text) else 170
        with self.lock:
            if size is not None and size != self.viewport:
                self._stop_video()
            if size is not None:
                self.viewport = size
                self.last_health_failure = None
            # A geometry timeout does not mean USB disconnected. Preserve the
            # video, but disable pixel-based controls until metadata recovers.
            self.locked, self.connected, self.geometry_ready = False, True, size is not None
            self.last_health = time.monotonic()

    def _watch(self):
        while not self.closed.is_set():
            now = time.monotonic()
            with self.lock:
                active = now < self.lease_until
                paused = self.pause_path.exists()
            if active and not paused and now - self.last_health > 1:
                self._health()
            with self.lock:
                # A health probe may finish after close(), pause or lease expiry.
                # Recheck under the lock before starting any new capture child.
                if self.closed.is_set():
                    self._stop_video()
                    break
                now = time.monotonic()
                active = now < self.lease_until
                paused = self.pause_path.exists()
                if paused or not active or self.locked or not self.connected:
                    self._stop_video()
                elif self.recorder is False:
                    # Permanent for this device: no encoder starts and no retry loop.
                    self._stop_video()
                    self.error = "preview_unsupported_device"
                elif self.pipeline is None and self.geometry_ready and now >= self.retry_at:
                    try:
                        pipeline = self.pipeline_factory(
                            self.serial,
                            self._publish,
                            self._failed,
                            size=self.viewport,
                            time_limit=self.recording_time_limit,
                        )
                        self.pipeline = pipeline
                        self.pipeline_starts += 1
                        pipeline.start()
                    except Exception as exc:
                        self._stop_video()
                        self.error = exc.code if isinstance(exc, UseError) else "video_start_failed"
                        self.retry_at = now + 3
            self.closed.wait(0.2)

    def frame(self, after_seq=0):
        with self.lock:
            self.last_activity = time.monotonic()
            self.lease_until = self.last_activity + LEASE_SECONDS
            paused = self.pause_path.exists()
            if paused or self.locked or not self.connected:
                self._stop_video()
            data = None
            if self.frame_data and not paused and not self.locked and self.seq != after_seq:
                data = {
                    "seq": self.seq,
                    "data": base64.b64encode(self.frame_data).decode(),
                    "width": self.frame_size[0],
                    "height": self.frame_size[1],
                    "mimeType": "image/jpeg",
                }
            return {
                "ok": True,
                "stream_id": self.stream_id,
                "seq": self.seq,
                "frame": data,
                "frame_available": self.frame_data is not None and not paused and not self.locked,
                "frame_age_ms": int((time.monotonic() - self.received) * 1000)
                if self.received
                else None,
                "paused": paused or (self.locked and self.connected),
                "pause_reason": "authentication"
                if paused
                else "device_locked"
                if self.locked and self.connected
                else None,
                "connected": self.connected,
                "interaction_ready": self.geometry_ready
                and self.connected
                and not self.locked
                and not paused,
                "capturing": self.pipeline is not None,
                "device": {"model": self.model},
                "viewport": self.viewport,
                "error": self.error,
                "transport": "adb-screenrecord-h264",
                "video": {
                    "pipeline_starts": self.pipeline_starts,
                    "health_failures": self.health_failures,
                    "metadata_failures": self.metadata_failures,
                    "last_health_failure": self.last_health_failure,
                    "stream_failures": self.stream_failures,
                    "recorder_available": self.recorder,
                    "recording_time_limit_seconds": self.recording_time_limit,
                    "recording_mode": "unlimited"
                    if self.recording_time_limit == 0
                    else "continuous_segments",
                    "decoded_frames": getattr(self.pipeline, "decoded_frames", 0),
                    "bytes_received": getattr(self.pipeline, "bytes_received", 0),
                },
            }

    def stop_capture(self):
        with self.lock:
            self._stop_video()

    def require_interaction_ready(self):
        with self.lock:
            if (
                not self.geometry_ready
                or not self.connected
                or self.locked
                or self.pause_path.exists()
                or self.pipeline is None
                or self.frame_data is None
                or self.error
            ):
                raise UseError("preview_buffering", "Wait for current video and display geometry")

    def save_frame(self):
        with self.lock:
            if self.pause_path.exists() or self.locked or not self.frame_data:
                raise UseError("preview_paused", "No active frame is available to save")
            directory = state_dir() / "screenshots"
            directory.mkdir(mode=0o700, exist_ok=True)
            path = directory / (uuid.uuid4().hex + ".jpg")
            path.write_bytes(self.frame_data)
            path.chmod(0o600)
            return {"ok": True, "saved_path": str(path)}

    def close(self):
        self.closed.set()
        self.stop_capture()


class ScreenClient:
    """Private loopback daemon shared by CLI, MCP widgets and the Codex browser panel."""

    def __init__(self, serial, key):
        self.serial, self.key = serial, key
        self.info_path, self.pause_path = device_paths(key)
        self.info = None

    def _load(self):
        try:
            info = json.loads(self.info_path.read_text())
            if info.get("device_key") == self.key:
                self.info = info
                return True
        except (OSError, ValueError):
            pass
        return False

    @property
    def url(self):
        return f"http://127.0.0.1:{self.info['port']}/{self.info['token']}/"

    def request(self, endpoint, arguments=None):
        if not self.info and not self._load():
            raise UseError("preview_not_started", "Open the Android screen first")
        request = urllib.request.Request(
            self.url + "api/" + endpoint,
            data=json.dumps(arguments or {}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            # Never forward private preview requests to an environment HTTP proxy.
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                request, timeout=8
            ) as r:
                return json.load(r)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise UseError(
                "preview_unreachable",
                "Live preview process is unavailable",
                uncertain=endpoint == "action"
                and (arguments or {}).get("action") in {"tap", "swipe", "home", "back"},
            ) from exc

    def ensure(self):
        with device_lock(self.key + "-preview-start"):
            previous = None
            if self._load():
                previous = dict(self.info)
                try:
                    status = self.request("status")
                    if (
                        status.get("pid") != self.info.get("pid")
                        or status.get("device_key") != self.key
                    ):
                        raise UseError("preview_identity_mismatch", "Preview identity changed")
                    revision = status.get("preview_revision")
                    if (
                        status.get("protocol_version") == PREVIEW_PROTOCOL_VERSION
                        and type(revision) is int
                        and revision >= PREVIEW_IMPLEMENTATION_VERSION
                    ):
                        if self.info["serial"] != self.serial:
                            changed = self.request("switch_serial", {"serial": self.serial})
                            if not changed.get("ok"):
                                raise UseError(
                                    "preview_transport_changed",
                                    "Could not switch preview transport",
                                )
                            self._load()
                        return self.url
                    # Upgrade only our authenticated preview daemon, not the phone service.
                    pid = self.info["pid"]
                    if type(pid) is not int or pid <= 1 or pid == os.getpid():
                        raise UseError("preview_identity_mismatch", "Invalid preview owner")
                    self.request("stop_capture")
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    self.info = None
                except UseError as exc:
                    if exc.code == "preview_identity_mismatch":
                        raise
            if not (os.environ.get("ANDROID_USE_FFMPEG") or shutil.which("ffmpeg")):
                raise UseError("preview_dependency_missing", "Install ffmpeg for live preview")
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "android_use.preview_daemon",
                    "--serial",
                    self.serial,
                    "--key",
                    self.key,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                # An older owner can remove its info file during shutdown. Pass
                # the authenticated endpoint independently so upgrades keep the
                # same browser URL. The child consumes this private value once.
                env={
                    **{k: v for k, v in os.environ.items() if k != PREVIEW_BOOTSTRAP_ENV},
                    **(
                        {
                            PREVIEW_BOOTSTRAP_ENV: json.dumps(
                                {**previous, "serial": self.serial, "device_key": self.key}
                            )
                        }
                        if previous
                        else {}
                    ),
                },
            )
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if self._load() and self.info.get("pid") == process.pid:
                    try:
                        status = self.request("status")
                        if (
                            status.get("protocol_version") == PREVIEW_PROTOCOL_VERSION
                            and status.get("preview_revision") == PREVIEW_IMPLEMENTATION_VERSION
                        ):
                            return self.url
                    except UseError:
                        pass
                time.sleep(0.1)
        raise UseError("preview_start_failed", "Live preview process did not start")

    def stop_capture_if_running(self):
        if self._load():
            self.request("stop_capture")
