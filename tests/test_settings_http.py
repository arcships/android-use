import json
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import httpx
import pytest

from android_use import preview_daemon
from android_use.backend import UseError
from android_use.screen import device_paths


@pytest.fixture
def preview_factory(monkeypatch):
    idle_age = [0]

    class Hub:
        def __init__(self, *args):
            self.last_activity = time.monotonic() - idle_age[0]

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

    @contextmanager
    def launch(age=0):
        idle_age[0] = age
        before = len(servers)
        thread = threading.Thread(
            target=preview_daemon.serve, args=("unused", "test-settings"), daemon=True
        )
        thread.start()
        info = device_paths("test-settings")[0]
        deadline = time.monotonic() + 3
        while (not info.exists() or len(servers) == before) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert info.exists() and len(servers) > before
        data = json.loads(info.read_text())
        url = f"http://127.0.0.1:{data['port']}/{data['token']}/"
        server = servers[-1]
        try:
            yield url, calls
        finally:
            server.shutdown()
            thread.join(timeout=3)
            assert not thread.is_alive()

    return launch


@pytest.fixture
def preview(preview_factory, request):
    with preview_factory(getattr(request, "param", 0)) as value:
        yield value


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


@pytest.mark.parametrize("preview", [601], indirect=True)
def test_idle_preview_listener_remains_reachable(preview):
    url, _ = preview
    with httpx.Client(trust_env=False, timeout=2) as client:
        result = client.post(url + "api/status", json={})
    assert result.status_code == 200 and result.json()["ok"]


def test_panel_resume_clears_the_shared_pause_flag_and_rejects_arguments(preview):
    url, _ = preview
    paused = preview_daemon.device_paths("test-settings")[1]
    paused.touch()
    with httpx.Client(trust_env=False) as client:
        rejected = client.post(url + "api/action", json={"action": "resume", "x": 1})
        assert rejected.status_code == 400 and paused.exists()
        reply = client.post(url + "api/action", json={"action": "resume"})
        assert reply.status_code == 200 and reply.json() == {"ok": True, "paused": False}
        assert not paused.exists()
        again = client.post(url + "api/action", json={"action": "resume"})
        assert again.status_code == 200  # Idempotent, like aua_resume.


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


def test_stopped_preview_reuses_its_private_address_on_restart(preview_factory):
    info = device_paths("test-settings")[0]
    with preview_factory() as (first_url, _):
        original = json.loads(info.read_text())
    assert info.exists()
    assert json.loads(info.read_text()) == original
    assert info.stat().st_mode & 0o777 == 0o600
    with preview_factory() as (second_url, _):
        assert second_url == first_url
        with httpx.Client(trust_env=False) as client:
            assert client.post(second_url + "api/status", json={}).json()["ok"]


def test_preview_html_uses_utf8_with_windows_legacy_default(preview, monkeypatch):
    from importlib.resources import files

    raw = files("android_use").joinpath("assets/phone-screen.html").read_bytes()

    class LegacyDefault:
        def joinpath(self, _):
            return self

        def read_text(self, encoding="cp1252"):
            return raw.decode(encoding)

    monkeypatch.setattr(preview_daemon, "files", lambda _: LegacyDefault())
    with httpx.Client(trust_env=False) as client:
        reply = client.get(preview[0])
    assert reply.status_code == 200
    assert "Android Use" in reply.text and "__ANDROID_USE_STANDALONE__=true" in reply.text
    assert reply.text == raw.decode("utf-8").replace(
        "<!--__MODE__-->", "<script>window.__ANDROID_USE_STANDALONE__=true</script>"
    )
