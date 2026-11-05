"""Local Jev preferences. Credentials never appear in public status or errors."""

import json
import os
import tempfile
import time

from .backend import UseError, device_lock, state_dir

JEV_ACTIONS = {
    "jev_status",
    "jev_save",
    "jev_test",
    "jev_disable",
    "jev_clear",
    "jev_images_on",
    "jev_images_off",
}


def read_jev_settings():
    path = state_dir() / "jev.json"
    try:
        if not path.exists():
            return {}
        if path.stat().st_size > 8192:
            raise ValueError()
        data = json.loads(path.read_text())
        if (
            not isinstance(data, dict)
            or set(data) - {"enabled", "api_key", "images"}
            or ("enabled" in data and type(data["enabled"]) is not bool)
            or ("images" in data and type(data["images"]) is not bool)
            or ("api_key" in data and not valid_key(data["api_key"]))
        ):
            raise ValueError()
        return data
    except (OSError, ValueError, TypeError):
        raise UseError("jev_settings_unavailable", "Cannot read local Jev settings") from None


def valid_key(key):
    return isinstance(key, str) and 0 < len(key) <= 4096 and all(33 <= ord(c) <= 126 for c in key)


def effective_jev_settings():
    saved = read_jev_settings()
    local_key = saved.get("api_key", "")
    key = local_key or os.environ.get("TYPESAFE_API_KEY", "").strip()
    return {
        "enabled": saved.get("enabled", os.environ.get("ANDROID_USE_JEV_ENABLED") == "1"),
        "key": key,
        "key_source": "local" if local_key else "environment" if key else "none",
        "has_saved_key": bool(local_key),
        # Screenshots leave the device only with this separate opt-in; Jev alone is text-only.
        "images": saved.get("images", os.environ.get("ANDROID_USE_JEV_IMAGES") == "1"),
        "model": os.environ.get("TYPESAFE_MODEL", "jev-1.14.0"),
        "base_url": os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1"),
    }


def jev_status():
    from .jev import endpoint_url

    data = effective_jev_settings()
    return {
        "ok": True,
        "enabled": data["enabled"] and bool(data["key"]),
        "configured": bool(data["key"]),
        "key_source": data["key_source"],
        "has_saved_key": data["has_saved_key"],
        "images_enabled": data["images"],
        "model": data["model"],
        "base_url": endpoint_url(data["base_url"]),
    }


def write_jev_settings(data):
    folder = state_dir()
    descriptor, temporary = tempfile.mkstemp(prefix=".jev-", dir=folder)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, folder / "jev.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def settings_action(action, arguments):
    if action not in JEV_ACTIONS or set(arguments) - {"api_key"}:
        raise UseError("invalid_arguments", "Unknown Jev settings action or field")
    if action != "jev_save" and arguments:
        raise UseError("invalid_arguments", "This settings action takes no fields")
    if action == "jev_status":
        return jev_status()
    with device_lock("jev-settings"):
        saved = read_jev_settings()
        if action in {"jev_save", "jev_test"}:
            from .jev import JevConfig, JevPolicy, endpoint_url

            data = effective_jev_settings()
            candidate = arguments.get("api_key", "")
            if not isinstance(candidate, str):
                raise UseError("invalid_arguments", "API key must be text")
            candidate = candidate.strip()
            key = candidate or data["key"]
            if not valid_key(key):
                raise UseError("jev_configuration", "Enter a valid TypeSafe API key")
            policy = JevPolicy(JevConfig(key, data["model"], endpoint_url(data["base_url"])))
            began = time.monotonic()
            try:
                answer = policy.decide(
                    {
                        "goal": "Connection test only: choose READY. No device actions exist.",
                        "ui": [],
                    },
                    {
                        "READY": "The connection test is ready",
                        "BLOCKED": "Cannot complete the test",
                    },
                    8.0,
                )
                if answer["choice"] != "READY":
                    raise UseError("jev_validation_failed", "Connection test was not confirmed")
            finally:
                policy.close()
            if action == "jev_save":
                if candidate:
                    saved["api_key"] = candidate
                saved["enabled"] = True
                write_jev_settings(saved)
            return {
                **jev_status(),
                "tested": True,
                "latency_ms": round((time.monotonic() - began) * 1000),
            }
        if action in {"jev_images_on", "jev_images_off"}:
            saved["images"] = action == "jev_images_on"
            write_jev_settings(saved)
            return jev_status()
        saved["enabled"] = False
        if action == "jev_clear":
            saved.pop("api_key", None)
        write_jev_settings(saved)
        return jev_status()
