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
