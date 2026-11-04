import json
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest

from android_use import preview_daemon
from android_use.backend import UseError
from android_use.screen import device_paths


@pytest.fixture
def preview(monkeypatch):
    class Hub:
        def __init__(self, *args):
            self.last_activity = time.monotonic()

        def start(self):
            pass

        def close(self):
            self.last_activity = 0

        def require_interaction_ready(self):
            raise UseError("preview_buffering", "Display geometry unavailable")

    servers, calls = [], []

    def make_server(*args):
        server = ThreadingHTTPServer(*args)
        servers.append(server)
        return server

    class Policy:
        def __init__(self, config):
            self.key = config.key

        def decide(self, *args):
            calls.append(1)
            if self.key == "rejected-secret":
                raise UseError("jev_http_error", "Request rejected", status=401)
            return {"choice": "READY"}

        def close(self):
            pass

    for name in ("TYPESAFE_API_KEY", "ANDROID_USE_JEV_ENABLED", "TYPESAFE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(preview_daemon, "ScreenHub", Hub)
    monkeypatch.setattr(preview_daemon, "ThreadingHTTPServer", make_server)
    monkeypatch.setattr(preview_daemon.signal, "signal", lambda *_: None)
    monkeypatch.setattr("android_use.jev.JevPolicy", Policy)
    thread = threading.Thread(
        target=preview_daemon.serve, args=("unused", "test-settings"), daemon=True
    )
    thread.start()
    info = device_paths("test-settings")[0]
    deadline = time.monotonic() + 3
    while not info.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert info.exists()
    data = json.loads(info.read_text())
    url = f"http://127.0.0.1:{data['port']}/{data['token']}/"
    try:
        yield url, calls
    finally:
        servers[0].shutdown()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_credential_settings_require_private_token_host_and_same_origin(preview):
    url, calls = preview
    origin = url.rsplit("/", 2)[0]
    args = {"action": "jev_save", "api_key": "http-private-secret"}
    with httpx.Client(trust_env=False) as client:
        for target, headers in (
            (url + "api/action", {"Origin": "https://foreign.test"}),
            (url + "api/action", {"Host": "foreign.test"}),
            (origin + "/wrong-token/api/action", {}),
        ):
            assert client.post(target, json=args, headers=headers).status_code == 403
        assert not calls
        reply = client.post(url + "api/action", json=args, headers={"Origin": origin})
        assert reply.status_code == 200 and reply.json()["enabled"]
        assert "http-private-secret" not in reply.text and len(calls) == 1
        status = client.post(url + "api/action", json={"action": "jev_status"})
        assert "http-private-secret" not in status.text and status.json()["configured"]


def test_provider_error_has_safe_status_without_echoing_secret(preview):
    url, _ = preview
    with httpx.Client(trust_env=False) as client:
        reply = client.post(
            url + "api/action", json={"action": "jev_save", "api_key": "rejected-secret"}
        )
    assert reply.status_code == 503 and reply.json()["error"]["status"] == 401
    assert "rejected-secret" not in reply.text


@pytest.mark.parametrize("action", ["tap", "swipe"])
def test_preview_coordinate_actions_require_geometry_even_for_old_clients(
    preview, monkeypatch, action
):
    url, _ = preview

    def forbidden(*args, **kwargs):
        pytest.fail("A phone action ran while preview geometry was unavailable")

    monkeypatch.setattr(preview_daemon.Runtime, "call", forbidden)
    with httpx.Client(trust_env=False) as client:
        reply = client.post(url + "api/action", json={"action": action})
    assert reply.status_code == 503
    assert reply.json()["error"]["code"] == "preview_buffering"
