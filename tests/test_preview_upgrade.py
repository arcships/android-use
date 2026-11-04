import os
from types import SimpleNamespace

import pytest

from android_use.backend import UseError
from android_use.screen import PREVIEW_PROTOCOL_VERSION, ScreenClient


def client(monkeypatch, protocol, *, mismatch=False):
    current = ScreenClient("usb", "hardware-key")
    started, killed, calls = [], [], []

    def load():
        current.info = {
            "pid": 900002 if started else 900001,
            "serial": "usb",
            "port": 12345,
            "token": "private-token",
            "device_key": "hardware-key",
        }
        return True

    def request(endpoint, arguments=None):
        calls.append(endpoint)
        return {
            "ok": True,
            "pid": 900003 if mismatch else current.info["pid"],
            "device_key": "hardware-key",
            "protocol_version": PREVIEW_PROTOCOL_VERSION if started else protocol,
        }

    def spawn(*args, **kwargs):
        started.append(1)
        return SimpleNamespace(pid=900002)

    monkeypatch.setattr(current, "_load", load)
    monkeypatch.setattr(current, "request", request)
    monkeypatch.setattr("android_use.screen.subprocess.Popen", spawn)
    monkeypatch.setattr("android_use.screen.os.kill", lambda pid, _: killed.append(pid))
    monkeypatch.setattr("android_use.screen.shutil.which", lambda _: "/test/ffmpeg")
    return current, started, killed, calls


def test_healthy_preview_is_reused_without_restart(monkeypatch):
    preview, started, killed, _ = client(monkeypatch, PREVIEW_PROTOCOL_VERSION)
    assert preview.ensure().startswith("http://127.0.0.1:")
    assert not started and not killed


def test_legacy_authenticated_preview_is_replaced_and_new_process_confirmed(monkeypatch):
    preview, started, killed, calls = client(monkeypatch, None)
    preview.ensure()
    assert killed == [900001] and len(started) == 1
    assert calls == ["status", "stop_capture", "status"]
    assert preview.info["pid"] == 900002


def test_mismatched_process_is_not_terminated_or_reused(monkeypatch):
    preview, started, killed, _ = client(monkeypatch, None, mismatch=True)
    with pytest.raises(UseError) as exc:
        preview.ensure()
    assert exc.value.code == "preview_identity_mismatch"
    assert not started and not killed


def test_client_process_can_never_be_terminated_as_old_preview(monkeypatch):
    preview, started, killed, _ = client(monkeypatch, None)
    original = preview._load

    def load():
        original()
        preview.info["pid"] = os.getpid()
        return True

    monkeypatch.setattr(preview, "_load", load)
    with pytest.raises(UseError):
        preview.ensure()
    assert not started and not killed
