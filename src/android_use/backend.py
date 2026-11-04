"""ADB transport plus a pinned UiAutomator2 client with action replay disabled."""

import hashlib
import importlib.resources
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import adbutils
import uiautomator2 as u2
from uiautomator2.core import _jsonrpc_call


class UseError(Exception):
    def __init__(self, code, message, *, uncertain=False, **details):
        super().__init__(message)
        self.code, self.uncertain, self.details = code, uncertain, details

    def as_dict(self):
        return {
            "code": self.code,
            "message": str(self),
            "uncertain": self.uncertain,
            **self.details,
        }


def state_dir():
    path = Path(os.environ.get("ANDROID_USE_STATE_DIR", "~/.local/share/android-use")).expanduser()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def read_config():
    path = state_dir() / "config.json"
    return json.loads(path.read_text()) if path.exists() else {}


def write_config(value):
    # Callers hold the config/device lock. Atomic replacement avoids partial config reads.
    path = state_dir() / "config.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(path)


@contextmanager
def device_lock(serial="configuration"):
    key = hashlib.sha256(serial.encode()).hexdigest()[:24]
    with (state_dir() / (key + ".lock")).open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UseError("device_busy", "Another Android Use operation owns this device") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def adb_command(args, *, serial=None, timeout=15):
    adb = os.environ.get("ANDROID_USE_ADB") or shutil.which("adb")
    if not adb:
        raise UseError("adb_missing", "Install Android SDK Platform Tools and put adb on PATH")
    command = [adb] + (["-s", serial] if serial else []) + list(args)
    result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise UseError("adb_error", result.stderr.decode(errors="replace").strip())
    return result.stdout.decode(errors="replace").strip()


def discover():
    lines = adb_command(["devices", "-l"]).splitlines()[1:]
    result = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 2:
            result.append(
                {
                    "serial": fields[0],
                    "state": fields[1],
                    **dict(s.split(":", 1) for s in fields[2:] if ":" in s),
                }
            )
    return result


def choose_serial(explicit=None):
    devices = discover()
    selected = explicit or os.environ.get("ANDROID_SERIAL") or read_config().get("serial")
    if selected:
        candidate = next((d for d in devices if d["serial"] == selected), None)
        if candidate is None or candidate["state"] != "device":
            raise UseError(
                "device_unavailable",
                "Selected device is disconnected or unauthorized",
                selected=selected,
                devices=devices,
            )
        return selected
    if len(devices) != 1 or devices[0]["state"] != "device":
        raise UseError(
            "device_selection_required",
            "Choose a serial; authorize USB debugging if needed",
            devices=devices,
        )
    return devices[0]["serial"]


class SingleAttemptDevice(u2.Device):
    """Upstream normally restarts/replays RPCs. Neither is allowed inside actions here.

    These private interfaces are deliberately version-pinned and covered by tests.
    Constructor probes an existing server; only setup(start) may launch one.
    """

    def start_uiautomator(self):
        if not self._check_alive():
            raise UseError("service_not_started", "Call aua_setup(action='start') first")

    def jsonrpc_call(self, method, params=None, timeout=10):
        return _jsonrpc_call(self._dev, self._device_server_port, method, params, timeout, False)


def viewport_from_info(info):
    """Return display pixels from one sampled deviceInfo, or request legacy fallback."""
    if isinstance(info, dict):
        width, height = info.get("displayWidth"), info.get("displayHeight")
        if type(width) is int and type(height) is int and width > 0 and height > 0:
            return width, height
    return None


class Backend:
    def __init__(self, serial):
        self.serial = serial
        self._device = None

    @property
    def d(self):
        if self._device is None:
            self._device = SingleAttemptDevice(adbutils.adb.device(self.serial))
        return self._device

    def shell(self, args, timeout=15):
        # adb joins remote shell arguments; quote every argument at that boundary.
        return adb_command(["shell", shlex.join(args)], serial=self.serial, timeout=timeout)

    def identity(self):
        # USB and wireless ADB endpoints may address the same physical phone.
        return self.shell(["getprop", "ro.serialno"]) or self.serial

    def start(self):
        if self._device is not None and not self._device._check_alive():
            self._device = None
        try:
            _ = self.d
            return {"started": False, "reused": True}
        except UseError as exc:
            if exc.code != "service_not_started":
                raise
        jar = importlib.resources.files("uiautomator2").joinpath("assets/u2.jar")
        with importlib.resources.as_file(jar) as local:
            adb_command(
                ["push", str(local), "/data/local/tmp/android-use-u2.jar"],
                serial=self.serial,
                timeout=30,
            )
        # Detached server survives individual CLI calls; no keyboard APK or root is needed.
        command = (
            "CLASSPATH=/data/local/tmp/android-use-u2.jar nohup app_process / "
            "com.wetest.uia2.Main -p 9008 "
            "> /data/local/tmp/android-use-u2.log 2>&1 < /dev/null &"
        )
        adb_command(["shell", command], serial=self.serial)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                _ = self.d
                return {"started": True, "reused": False}
            except UseError:
                time.sleep(0.3)
        raise UseError(
            "service_start_failed",
            "UiAutomator did not become ready",
            log=self.shell(["tail", "-c", "4000", "/data/local/tmp/android-use-u2.log"]),
        )

    def locked(self):
        policy = self.shell(["dumpsys", "window", "policy"])
        locked = bool(
            re.search(r"(?:showing|mShowingLockscreen|isStatusBarKeyguard)\s*=\s*true", policy)
        )
        return locked

    def size(self):
        # UiDevice reports current display pixels including rotation/override. The
        # upstream window_size path separately shells out for wm size and rotation.
        return viewport_from_info(self.d.info) or self.d.window_size()

    def foreground(self):
        # Newer Android puts focus under display contents; the upstream windows
        # query can miss it and fall through to two more activity dumpsys calls.
        try:
            output = self.shell(["dumpsys", "window", "displays"])
        except UseError:
            output = ""
        displays = set(re.findall(r"\bmDisplayId=(\d+)", output))
        focused = re.findall(r"mCurrentFocus=Window\{[^\n{}]*\s([^\s/{}]+)/([^\s{}]+)\}", output)
        if len(focused) == 1 and (not displays or displays == {"0"}):
            package, activity = focused[0]
            return {"package": package, "activity": activity}
        return self.d.app_current()

    def tree(self):
        # One bounded read; bypass upstream's empty-hierarchy retry loop.
        return self.d.jsonrpc.dumpWindowHierarchy(False, 50)

    def screenshot(self):
        return self.d.screenshot()

    def tap(self, x, y):
        return self.d.jsonrpc.click(x, y)

    def long_press(self, x, y, duration):
        return self.d.jsonrpc.click(x, y, int(duration * 1000))

    def double_tap(self, x, y, interval):
        # One intentional pair. If either call fails, never replay the first click.
        if self.d.jsonrpc.click(x, y) is False:
            return False
        time.sleep(interval)
        return self.d.jsonrpc.click(x, y)

    def drag(self, start, end, duration):
        return self.d.jsonrpc.drag(*start, *end, max(1, int(duration * 200)))

    def swipe(self, start, end, duration):
        return self.d.jsonrpc.swipe(*start, *end, max(1, int(duration * 200)))

    def set_text(self, text, selector):
        from uiautomator2._selector import Selector

        return self.d.jsonrpc.setText(Selector(**selector), text)

    def get_text(self, selector):
        from uiautomator2._selector import Selector

        value = self.d.jsonrpc.getText(Selector(**selector))
        # UiObject.getText returns JSON null for an empty Android text field.
        return "" if value is None else value

    def press(self, name):
        keys = {
            "home": 3,
            "back": 4,
            "recent": 187,
            "enter": 66,
            "volume_up": 24,
            "volume_down": 25,
        }
        return self.d.jsonrpc.pressKeyCode(keys[name])

    def launch(self, package):
        component = self.shell(
            [
                "cmd",
                "package",
                "resolve-activity",
                "--brief",
                "-a",
                "android.intent.action.MAIN",
                "-c",
                "android.intent.category.LAUNCHER",
                package,
            ]
        ).splitlines()[-1]
        if "/" not in component or " " in component:
            raise UseError("app_not_launchable", "No installed launcher activity", package=package)
        output = self.shell(["am", "start", "-n", component])
        if "Error:" in output or "Exception" in output:
            raise UseError("launch_failed", output, uncertain=True)
        return output

    def apps(self, query):
        output = self.shell(["pm", "list", "packages", "-3"])
        return [
            p.removeprefix("package:")
            for p in output.splitlines()
            if query.casefold() in p.casefold()
        ]
