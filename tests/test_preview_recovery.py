import errno
import json
import os
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from android_use import preview_daemon
from android_use.backend import UseError
from android_use.runtime import Runtime
from android_use.screen import PREVIEW_BOOTSTRAP_ENV, ScreenClient


def test_only_frame_reads_retry_after_recovering_the_endpoint(monkeypatch):
    calls = []

    def request(endpoint, args):
        calls.append(endpoint)
        assert args == {"after_seq": 19}
        if len(calls) == 1:
            raise UseError("preview_unreachable", "Owner exited")
        return {"ok": True, "seq": 20}

    client = SimpleNamespace(request=request, ensure=lambda: calls.append("ensure"))
    runtime = Runtime("usb")
    monkeypatch.setattr(runtime, "screen_client", lambda: client)
    assert runtime.screen_call("screen_frame", after_seq=19) == {"ok": True, "seq": 20}
    assert calls == ["frame", "ensure", "frame"]


@pytest.mark.parametrize("action", ["tap", "swipe", "home", "back"])
def test_phone_actions_are_never_replayed_after_preview_transport_failure(monkeypatch, action):
    calls = []

    def request(endpoint, args):
        calls.append(endpoint)
        raise UseError("preview_unreachable", "Response lost", uncertain=True)

    client = SimpleNamespace(request=request, ensure=lambda: calls.append("ensure"))
    runtime = Runtime("usb")
    monkeypatch.setattr(runtime, "screen_client", lambda: client)
    result = runtime.screen_call("screen_action", action=action)
    assert result["error"]["uncertain"]
    assert calls == ["action"]


def test_frame_identity_errors_do_not_trigger_recovery(monkeypatch):
    calls = []

    def request(*args):
        calls.append("frame")
        raise UseError("preview_identity_mismatch", "Different owner")

    runtime = Runtime("usb")
    monkeypatch.setattr(
        runtime,
        "screen_client",
        lambda: SimpleNamespace(request=request, ensure=lambda: calls.append("ensure")),
    )
    assert runtime.screen_call("screen_frame")["error"]["code"] == "preview_identity_mismatch"
    assert calls == ["frame"]


@pytest.mark.parametrize(
    "endpoint,args,uncertain",
    [
        ("frame", {}, False),
        ("refresh", {}, False),
        ("action", {"action": "tap", "x": 10, "y": 20}, True),
        ("action", {"action": "home"}, True),
    ],
)
def test_transport_errors_mark_only_phone_writes_as_uncertain(
    monkeypatch, endpoint, args, uncertain
):
    def fail(*a, **kw):
        raise urllib.error.URLError("Connection reset")

    monkeypatch.setattr(
        "android_use.screen.urllib.request.build_opener", lambda *a: SimpleNamespace(open=fail)
    )
    client = ScreenClient("usb", "device")
    client.info = {"port": 12345, "token": "private"}
    with pytest.raises(UseError) as error:
        client.request(endpoint, args)
    assert error.value.uncertain is uncertain


def test_upgrade_bootstrap_survives_old_owner_removing_the_info_file(monkeypatch, tmp_path):
    path = tmp_path / "info.json"
    previous = {"serial": "usb", "device_key": "device", "port": 34567, "token": "a" * 43}
    path.write_text(json.dumps(previous))
    monkeypatch.setenv(PREVIEW_BOOTSTRAP_ENV, json.dumps(previous))
    path.unlink()  # This is what a v0.5.1 daemon did on exit.
    assert preview_daemon.previous_endpoint(path, "usb", "device") == previous
    assert PREVIEW_BOOTSTRAP_ENV not in os.environ


def test_bootstrap_cannot_reuse_another_devices_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv(PREVIEW_BOOTSTRAP_ENV, json.dumps({"serial": "usb", "device_key": "other"}))
    assert preview_daemon.previous_endpoint(tmp_path / "missing", "usb", "device") == {}


def test_upgrade_waits_for_the_previous_listener_instead_of_changing_ports(monkeypatch):
    previous = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = previous.server_port
    attempts = []

    def bind(address, handler):
        attempts.append(address[1])
        if len(attempts) == 1:
            try:
                return ThreadingHTTPServer(address, handler)
            finally:
                previous.server_close()
        return ThreadingHTTPServer(address, handler)

    monkeypatch.setattr(preview_daemon, "ThreadingHTTPServer", bind)
    server = preview_daemon.bind_server(port, BaseHTTPRequestHandler)
    try:
        assert server.server_port == port
        assert attempts == [port, port]
    finally:
        server.server_close()


def test_listener_errors_other_than_port_contention_are_not_hidden(monkeypatch):
    def fail(*args):
        raise OSError(errno.EACCES, "Denied")

    monkeypatch.setattr(preview_daemon, "ThreadingHTTPServer", fail)
    with pytest.raises(OSError):
        preview_daemon.bind_server(12345, BaseHTTPRequestHandler)
