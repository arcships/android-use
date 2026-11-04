"""Token-protected loopback preview transport; shared by the MCP App and browser sidebar."""

import argparse
import atexit
import json
import os
import re
import secrets
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

from .backend import Backend, UseError
from .runtime import Runtime, save_images
from .screen import (
    PREVIEW_IMPLEMENTATION_VERSION,
    PREVIEW_PROTOCOL_VERSION,
    ScreenHub,
    device_paths,
)
from .settings import JEV_ACTIONS, settings_action


def serve(serial, key):
    hub = ScreenHub(serial, key)
    runtime = Runtime(serial)
    info_path = device_paths(key)[0]
    previous = {}
    try:
        previous = json.loads(info_path.read_text())
    except (OSError, ValueError):
        pass
    previous_token = previous.get("token")
    token = (
        previous_token
        if isinstance(previous_token, str)
        and re.fullmatch(r"[A-Za-z0-9_-]{32,100}", previous_token)
        else secrets.token_urlsafe(32)
    )
    prefix = "/" + token + "/"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _allowed(self):
            host = f"127.0.0.1:{self.server.server_port}"
            origin = self.headers.get("Origin")
            return (
                self.headers.get("Host") == host
                and self.path.startswith(prefix)
                and (not origin or origin == "http://" + host)
            )

        def _send(self, status, body, mime="application/json"):
            raw = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; img-src data: blob:; connect-src 'self'; "
                "base-uri 'none'; frame-ancestors 'self'",
            )
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if not self._allowed() or self.path != prefix:
                return self._send(404, b"Not found", "text/plain")
            html = files("android_use").joinpath("assets/phone-screen.html").read_text()
            html = html.replace(
                "<!--__MODE__-->", "<script>window.__ANDROID_USE_STANDALONE__=true</script>"
            )
            self._send(200, html, "text/html; charset=utf-8")

        def do_POST(self):
            nonlocal runtime
            if not self._allowed():
                return self._send(403, b"Forbidden", "text/plain")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 8192 or "application/json" not in self.headers.get(
                    "Content-Type", ""
                ):
                    return self._send(400, b"Invalid request", "text/plain")
                args = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(args, dict):
                    raise ValueError("Expected object")
                endpoint = self.path.removeprefix(prefix + "api/")
                if endpoint == "frame":
                    after = args.get("after_seq", 0)
                    if isinstance(after, bool) or not isinstance(after, int) or after < 0:
                        raise ValueError("Invalid sequence")
                    result = hub.frame(after)
                elif endpoint == "status":
                    result = {
                        "ok": True,
                        "pid": os.getpid(),
                        "device_key": key,
                        "protocol_version": PREVIEW_PROTOCOL_VERSION,
                        "preview_revision": PREVIEW_IMPLEMENTATION_VERSION,
                    }
                elif endpoint == "switch_serial":
                    new_serial = args.get("serial")
                    if not isinstance(new_serial, str) or not new_serial or len(new_serial) > 200:
                        raise ValueError("Invalid serial")
                    backend = Backend(new_serial)
                    if backend.identity() != key:
                        raise ValueError("Device identity differs")
                    with hub.lock:
                        hub._stop_video()
                        hub.serial, hub.backend = new_serial, backend
                        hub.last_health = 0
                        runtime = Runtime(new_serial)
                        info["serial"] = new_serial
                        temporary.write_text(json.dumps(info))
                        temporary.chmod(0o600)
                        temporary.replace(info_path)
                    result = {"ok": True}
                elif endpoint == "stop_capture":
                    hub.stop_capture()
                    result = {"ok": True}
                elif endpoint == "refresh":
                    hub.stop_capture()
                    with hub.lock:
                        hub.last_health = 0
                        hub.retry_at = 0
                    result = hub.frame()
                elif endpoint == "action":
                    # App-only toolbar/gestures still go through normal validation and device locking.
                    action = args.pop("action", None)
                    if action in JEV_ACTIONS:
                        result = settings_action(action, args)
                    elif action in {"home", "back"} and not args:
                        result = runtime.call("press_button", name=action)
                    elif action == "save" and not args:
                        result = hub.save_frame()
                    elif action == "tap":
                        hub.require_interaction_ready()
                        result = runtime.call("tap", **args)
                    elif action == "swipe":
                        hub.require_interaction_ready()
                        result = runtime.call("swipe", **args)
                    else:
                        raise ValueError("Unknown preview action")
                    result = save_images(result, info_path.parent / "screenshots")
                else:
                    return self._send(404, b"Not found", "text/plain")
                self._send(200, json.dumps(result, ensure_ascii=False))
            except (ValueError, TypeError):
                self._send(400, json.dumps({"ok": False, "error": {"code": "invalid_arguments"}}))
            except Exception as exc:
                error = {"code": exc.code if isinstance(exc, UseError) else "preview_error"}
                if isinstance(exc, UseError) and type(exc.details.get("status")) is int:
                    error["status"] = exc.details["status"]
                self._send(
                    503,
                    json.dumps(
                        {
                            "ok": False,
                            "error": error,
                        }
                    ),
                )

    port = previous.get("port", 0)
    port = port if isinstance(port, int) and 1024 <= port <= 65535 else 0
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    info = {
        "pid": os.getpid(),
        "port": server.server_port,
        "token": token,
        "serial": serial,
        "device_key": key,
        "protocol_version": PREVIEW_PROTOCOL_VERSION,
        "preview_revision": PREVIEW_IMPLEMENTATION_VERSION,
    }
    temporary = info_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(info))
    temporary.chmod(0o600)
    temporary.replace(info_path)

    def cleanup():
        hub.close()
        try:
            if json.loads(info_path.read_text()).get("pid") == os.getpid():
                info_path.unlink()
        except (OSError, ValueError):
            pass

    atexit.register(cleanup)
    signal.signal(
        signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start()
    )
    hub.start()

    def idle_shutdown():
        while time.monotonic() - hub.last_activity < 600:
            time.sleep(5)
        server.shutdown()

    threading.Thread(target=idle_shutdown, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        cleanup()
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    serve(args.serial, args.key)


if __name__ == "__main__":
    main()
