import json
import os

import httpx
import pytest

from android_use.backend import UseError, state_dir
from android_use.jev import JevConfig, JevPolicy
from android_use.runtime import Runtime
from android_use.settings import read_jev_settings, settings_action


@pytest.fixture
def provider(monkeypatch):
    for name in (
        "TYPESAFE_API_KEY",
        "ANDROID_USE_JEV_ENABLED",
        "TYPESAFE_BASE_URL",
        "TYPESAFE_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "READY",
                        "probabilities": {"READY": 1.0, "BLOCKED": 0.0},
                    }
                },
            },
        )

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        "android_use.jev.JevPolicy", lambda config: JevPolicy(config, transport=transport)
    )
    return requests


def test_settings_work_without_a_phone_and_do_not_enable_implicitly(provider, monkeypatch):
    monkeypatch.setattr(
        "android_use.runtime.choose_serial", lambda *_: pytest.fail("No device access")
    )
    result = Runtime().call("screen_action", action="jev_status")
    assert result["ok"] and not result["enabled"] and not result["configured"]
    assert not provider
    with pytest.raises(UseError, match="Enable Jev"):
        JevConfig.from_env()


def test_ui_save_verifies_synthetic_request_and_new_calls_read_key_without_restart(provider):
    key = "settings-secret-key"
    result = settings_action("jev_save", {"api_key": key})
    assert result["tested"] and result["enabled"] and result["has_saved_key"]
    assert key not in json.dumps(result)
    request = provider[0]
    assert request.headers["Authorization"] == "Bearer " + key
    assert json.loads(request.content)["state"]["ui"] == []
    assert JevConfig.from_env().key == key
    path = state_dir() / "jev.json"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    settings_action("jev_save", {"api_key": "rotated-secret-key"})
    assert JevConfig.from_env().key == "rotated-secret-key"
    assert key not in path.read_text()
    assert "secret" not in repr(JevConfig.from_env())


def test_disable_overrides_environment_and_clear_does_not_reenable(provider, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "environment-secret")
    monkeypatch.setenv("ANDROID_USE_JEV_ENABLED", "1")
    settings_action("jev_save", {"api_key": "local-secret"})
    assert JevConfig.from_env().key == "local-secret"
    disabled = settings_action("jev_disable", {})
    assert not disabled["enabled"] and disabled["has_saved_key"]
    with pytest.raises(UseError) as exc:
        JevConfig.from_env()
    assert exc.value.code == "jev_disabled"
    cleared = settings_action("jev_clear", {})
    assert not cleared["enabled"] and not cleared["has_saved_key"]
    assert cleared["configured"] and cleared["key_source"] == "environment"
    assert "secret" not in json.dumps(cleared)
    assert "api_key" not in read_jev_settings()
    settings_action("jev_save", {})
    assert JevConfig.from_env().key == "environment-secret"


def test_connection_test_does_not_opt_in_or_persist_environment_key(provider, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "environment-secret")
    result = settings_action("jev_test", {})
    assert result["tested"] and not result["enabled"] and not result["has_saved_key"]
    assert not (state_dir() / "jev.json").exists()
    assert len(provider) == 1


def test_failed_key_check_keeps_existing_key_and_does_not_echo_provider_body(provider, monkeypatch):
    settings_action("jev_save", {"api_key": "old-secret"})
    monkeypatch.setattr(
        "android_use.jev.JevPolicy",
        lambda config: JevPolicy(
            config,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(401, json={"error": "rejected-secret"})
            ),
        ),
    )
    result = Runtime().call("screen_action", action="jev_save", api_key="rejected-secret")
    assert not result["ok"] and result["error"]["status"] == 401
    assert "rejected-secret" not in json.dumps(result)
    assert JevConfig.from_env().key == "old-secret"


@pytest.mark.parametrize("key", ["bad\nsecret", "bad\rsecret", "x" * 4097, "非ASCII密钥", 42, {}])
def test_invalid_secret_is_not_saved_or_returned(provider, key):
    result = Runtime().call("screen_action", action="jev_save", api_key=key)
    assert not result["ok"] and not provider
    assert not (state_dir() / "jev.json").exists()
    if isinstance(key, str):
        assert key not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    "value", [[], {"enabled": "true"}, {"api_key": 42}, {"unexpected": "secret"}]
)
def test_invalid_local_state_fails_closed(provider, monkeypatch, value):
    monkeypatch.setenv("ANDROID_USE_JEV_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "environment-secret")
    (state_dir() / "jev.json").write_text(json.dumps(value))
    with pytest.raises(UseError) as exc:
        JevConfig.from_env()
    assert exc.value.code == "jev_settings_unavailable"


def test_failed_atomic_replace_keeps_previous_settings_and_removes_temporary(provider, monkeypatch):
    settings_action("jev_save", {"api_key": "old-secret"})

    def fail(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr("android_use.settings.os.replace", fail)
    result = Runtime().call("screen_action", action="jev_save", api_key="new-secret")
    assert not result["ok"] and "new-secret" not in json.dumps(result)
    assert JevConfig.from_env().key == "old-secret"
    assert not list(state_dir().glob(".jev-*"))


def test_unknown_fields_are_rejected_without_device_access(provider):
    with pytest.raises(UseError) as exc:
        settings_action("jev_save", {"api_key": "valid-secret", "base_url": "https://evil.test"})
    assert exc.value.code == "invalid_arguments"
    assert not provider


def test_status_rejects_credential_bearing_provider_url_without_echo(provider, monkeypatch):
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://user:private-password@example.test/v1")
    result = Runtime().call("screen_action", action="jev_status")
    assert not result["ok"] and "private-password" not in json.dumps(result)
