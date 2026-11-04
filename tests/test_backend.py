import pytest

from android_use import backend


def test_multiple_or_unauthorized_devices_never_auto_select(monkeypatch, tmp_path):
    monkeypatch.setenv("ANDROID_USE_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    devices = [{"serial": "one", "state": "device"}, {"serial": "two", "state": "device"}]
    monkeypatch.setattr(backend, "discover", lambda: devices)
    with pytest.raises(backend.UseError, match="Choose a serial"):
        backend.choose_serial()
    assert backend.choose_serial("two") == "two"
    devices[1]["state"] = "unauthorized"
    with pytest.raises(backend.UseError):
        backend.choose_serial("two")


def test_saved_device_never_falls_back_to_other_phone(monkeypatch, tmp_path):
    monkeypatch.setenv("ANDROID_USE_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    backend.write_config({"serial": "disconnected"})
    monkeypatch.setattr(backend, "discover", lambda: [{"serial": "other", "state": "device"}])
    with pytest.raises(backend.UseError):
        backend.choose_serial()


def test_pinned_adapter_sends_one_rpc_without_restart(monkeypatch):
    calls = []

    def fail(*args):
        calls.append(args)
        raise TimeoutError("socket timed out after possible write")

    monkeypatch.setattr(backend, "_jsonrpc_call", fail)
    device = object.__new__(backend.SingleAttemptDevice)
    device._dev = "fake"
    device._device_server_port = 9008
    with pytest.raises(TimeoutError):
        device.jsonrpc_call("click", [10, 20])
    assert len(calls) == 1


def test_start_reprobes_a_dead_cached_connection(monkeypatch):
    from types import SimpleNamespace

    client = backend.Backend("demo")
    dead = SimpleNamespace(_check_alive=lambda: False)
    healthy = SimpleNamespace(_check_alive=lambda: True)
    client._device = dead
    monkeypatch.setattr(backend.adbutils.adb, "device", lambda serial: serial)
    monkeypatch.setattr(backend, "SingleAttemptDevice", lambda device: healthy)
    assert client.start()["reused"]
    assert client._device is healthy


def test_native_empty_field_null_is_empty_text():
    from types import SimpleNamespace

    client = backend.Backend("demo")
    client._device = SimpleNamespace(jsonrpc=SimpleNamespace(getText=lambda selector: None))
    assert client.get_text({"focused": True}) == ""


@pytest.mark.parametrize("width,height", [(1080, 2400), (2400, 1080), (800, 600)])
def test_size_uses_current_device_info_without_adb_queries(width, height):
    from types import SimpleNamespace

    client = backend.Backend("demo")
    client._device = SimpleNamespace(info={"displayWidth": width, "displayHeight": height})
    assert client.size() == (width, height)


@pytest.mark.parametrize(
    "info",
    [{}, None, {"displayWidth": True, "displayHeight": 4}, {"displayWidth": 0, "displayHeight": 4}],
)
def test_size_falls_back_for_unusable_info(info):
    from types import SimpleNamespace

    client = backend.Backend("demo")
    client._device = SimpleNamespace(info=info, window_size=lambda: (120, 240))
    assert client.size() == (120, 240)


def test_display_focus_fast_path_and_popup_ownership(monkeypatch):
    client = backend.Backend("demo")
    monkeypatch.setattr(
        client,
        "shell",
        lambda args: (
            "Display: mDisplayId=0\n"
            "mCurrentFocus=Window{abc u0 com.android.permissioncontroller/.GrantPermissionsActivity}"
        ),
    )
    assert client.foreground() == {
        "package": "com.android.permissioncontroller",
        "activity": ".GrantPermissionsActivity",
    }


@pytest.mark.parametrize(
    "output",
    [
        "mCurrentFocus=null",
        "unsupported command",
        "",
        "Display: mDisplayId=0\nDisplay: mDisplayId=1\nmCurrentFocus=Window{x u0 other.app/.Main}",
        "mCurrentFocus=Window{x u0 one.app/.Main}\nmCurrentFocus=Window{x u0 two.app/.Main}",
    ],
)
def test_ambiguous_or_unsupported_display_focus_falls_back(monkeypatch, output):
    from types import SimpleNamespace

    client = backend.Backend("demo")
    client._device = SimpleNamespace(
        app_current=lambda: {"package": "legacy.app", "activity": "Main"}
    )
    monkeypatch.setattr(client, "shell", lambda args: output)
    assert client.foreground()["package"] == "legacy.app"


def test_display_query_failure_preserves_legacy_read_path(monkeypatch):
    from types import SimpleNamespace

    client = backend.Backend("demo")
    client._device = SimpleNamespace(
        app_current=lambda: {"package": "legacy.app", "activity": "Main"}
    )

    def unsupported(args):
        raise backend.UseError("adb_error", "Unsupported dumpsys command")

    monkeypatch.setattr(client, "shell", unsupported)
    assert client.foreground()["package"] == "legacy.app"
