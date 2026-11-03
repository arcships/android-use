import base64
import hashlib
import io
import os
import threading
import time
import uuid
from pathlib import Path

from pydantic import ValidationError

from .backend import (
    Backend,
    UseError,
    choose_serial,
    device_lock,
    discover,
    read_config,
    state_dir,
    write_config,
)
from .models import ARG_MODELS, BatchStep, Selector, WaitCondition
from .tree import compact, in_viewport, matches, parse_tree, tap_point


class Runtime:
    def __init__(self, serial=None, backend_factory=Backend):
        self.serial = serial
        self.backend_factory = backend_factory
        self.backend = None
        self.device_key = None
        self.observations = {}
        self.mutation_attempted = False
        self._mutex = threading.Lock()
        self._device_locked = False
        self._screen_client = None
        self._deadline = None

    def screen_client(self):
        from .screen import ScreenClient

        if self.backend is None:
            self._connect()
        if self._screen_client is None or self._screen_client.serial != self.serial:
            self._screen_client = ScreenClient(self.serial, self.device_key)
        return self._screen_client

    def screen_call(self, op, **args):
        # Cached video access is independent of the phone action mutex and operation lock.
        try:
            client = self.screen_client()
            if op == "screen":
                url = client.ensure()
                return {
                    "ok": True,
                    "url": url,
                    "resource_uri": "ui://android-use/phone-0.2.1.html",
                    "transport": "adb-screenrecord-h264",
                }
            if op == "screen_frame":
                return client.request("frame", args)
            if op == "screen_action":
                action = args.get("action")
                if action == "refresh":
                    client.ensure()
                return client.request("refresh" if action == "refresh" else "action", args)
            raise ValueError("Unknown screen operation")
        except UseError as exc:
            return {"ok": False, "error": exc.as_dict()}
        except Exception as exc:
            return {
                "ok": False,
                "error": {
                    "code": "preview_error",
                    "uncertain": False,
                    "message": "Preview unavailable; control channel is independent",
                    "cause": type(exc).__name__,
                },
            }

    def _connect(self):
        selected = choose_serial(self.serial)
        if self.backend is None:
            candidate = self.backend_factory(selected)
            key = candidate.identity()
            self.serial = selected
            self.backend = candidate
            self.device_key = key
        return self.backend

    def _pause_path(self):
        key = hashlib.sha256(self.device_key.encode()).hexdigest()[:24]
        return state_dir() / (key + ".paused")

    def _check_paused(self):
        if self._pause_path().exists():
            raise UseError("user_takeover", "Device is paused. Resume only after user confirmation")

    def _usable(self):
        self._check_paused()
        if self.backend.locked():
            raise UseError("phone_locked", "Unlock the device on the phone, then call ready")
        if not self.backend.d.info.get("screenOn", False):
            raise UseError("screen_off", "Wake and unlock the device, then call ready")

    def call(self, op, **args):
        if op in {"screen", "screen_frame", "screen_action"}:
            return self.screen_call(op, **args)
        if op == "pause":
            # Cancellation must bypass both locks without changing a running
            # operation's mutation/uncertainty bookkeeping.
            try:
                if self.backend is None:
                    self._connect()
                self._pause_path().touch(mode=0o600)
                from .screen import ScreenClient

                ScreenClient(self.serial, self.device_key).stop_capture_if_running()
                return {"ok": True, "paused": True}
            except Exception as exc:
                return {
                    "ok": False,
                    "error": exc.as_dict()
                    if isinstance(exc, UseError)
                    else {
                        "code": "pause_failed",
                        "uncertain": False,
                        "message": "Inspect pause state before continuing",
                        "cause": type(exc).__name__,
                    },
                }
        if not self._mutex.acquire(blocking=False):
            return {
                "ok": False,
                "error": {
                    "code": "device_busy",
                    "uncertain": False,
                    "message": "Another call owns this runtime",
                },
            }
        try:
            return self._call(op, **args)
        finally:
            self._deadline = None
            self._mutex.release()

    def _call(self, op, **args):
        self.mutation_attempted = False
        try:
            if op == "doctor":
                return {
                    "ok": True,
                    "devices": discover(),
                    "configured": bool(read_config()),
                    "python": os.sys.version.split()[0],
                    "state_dir": str(state_dir()),
                }
            if op == "setup":
                return self._setup(**args)
            if op in ARG_MODELS:
                args = ARG_MODELS[op].model_validate(args).model_dump()
            elif op == "batch":
                steps = [BatchStep.model_validate(step) for step in args.get("steps", [])]
                if not 1 <= len(steps) <= 20:
                    raise ValueError("Batch must contain 1..20 steps")
                # Validate the entire batch before the first phone action.
                args = {
                    "steps": [
                        (s.op, ARG_MODELS[s.op].model_validate(s.args).model_dump()) for s in steps
                    ]
                }
            self._connect()
            with self._guard():
                if op == "resume":
                    self._pause_path().unlink(missing_ok=True)
                    return {"ok": True, "paused": False}
                self._usable()
                if op == "batch":
                    return self._batch(**args)
                result = self._dispatch(op, args)
                self._check_paused()
                return result
        except (ValidationError, ValueError, TypeError) as exc:
            # Validation errors may contain requested text; do not echo input payloads.
            return self._error(
                UseError("invalid_arguments", "Invalid arguments", reason=type(exc).__name__)
            )
        except UseError as exc:
            return self._error(exc)
        except Exception as exc:
            return self._error(
                UseError(
                    "action_uncertain" if self.mutation_attempted else "transport_error",
                    "Device call failed; inspect current state",
                    uncertain=self.mutation_attempted,
                    cause=type(exc).__name__,
                )
            )

    def _error(self, exc):
        error = {
            "ok": False,
            "error": exc.as_dict(),
            "action_attempted": self.mutation_attempted,
            "replay_action": False,
        }
        if self.mutation_attempted:
            error["error"]["uncertain"] = True
        # Screenshot fallback only for target/input issues; never capture during takeover/lock.
        if exc.code in {
            "target_not_found",
            "ambiguous_target",
            "offscreen_target",
            "disabled_target",
            "no_focused_field",
            "input_verification_failed",
        }:
            try:
                if self._device_locked:
                    self._usable()
                    error["observation"] = self._observe("screenshot")
                else:
                    with self._guard():
                        self._usable()
                        error["observation"] = self._observe("screenshot")
            except Exception as snapshot_error:
                error["screenshot_unavailable"] = type(snapshot_error).__name__
        return error

    def _guard(self):
        from contextlib import contextmanager

        @contextmanager
        def guard():
            with device_lock(self.device_key):
                self._device_locked = True
                try:
                    yield
                finally:
                    self._device_locked = False

        return guard()

    def _setup(self, action="status", serial=None):
        if action == "discover":
            return {"ok": True, "devices": discover()}
        if action == "configure":
            selected = choose_serial(serial)
            with device_lock():
                config = read_config()
                config["serial"] = selected
                write_config(config)
            self.serial, self.backend = selected, None
            self.device_key, self._screen_client = None, None
            return {"ok": True, "configured": True, "serial": selected}
        if action not in {"status", "start"}:
            raise ValueError("Unknown setup action")
        self._connect()
        with device_lock(self.device_key):
            if action == "start":
                result = self.backend.start()
                return {
                    "ok": True,
                    "serial": self.serial,
                    **result,
                    "next": "Call aua_ready; service startup alone does not prove UI readiness",
                }
            try:
                _ = self.backend.d.info
                service = True
            except Exception:
                service = False
            return {
                "ok": True,
                "serial": self.serial,
                "service_reachable": service,
                "configured": bool(read_config()),
                "paused": self._pause_path().exists(),
            }

    def _dispatch(self, op, args):
        if op == "ready":
            observation = self._observe("both" if args.get("screenshot", False) else "tree")
            if observation["tree"]["total_nodes"] == 0:
                raise UseError("empty_tree", "Accessibility tree is empty; device is not READY")
            return {
                "ok": True,
                "ready": True,
                "state": "ready",
                "serial": self.serial,
                "observation": observation,
            }
        if op == "observe":
            return {"ok": True, **self._observe(**args)}
        if op == "find":
            nodes = matches(self._nodes(), Selector.model_validate(args["selector"]))
            size = self.backend.size()
            return {
                "ok": True,
                "matches": [
                    {
                        **n,
                        "index": i,
                        "in_viewport": in_viewport(n, size),
                        "tap": tap_point(n, size),
                    }
                    for i, n in enumerate(nodes[: args["limit"]])
                ],
                "total": len(nodes),
            }
        if op == "wait":
            result, _ = self._wait_condition(args)
            return {"ok": True, **result}
        if op == "apps":
            packages = self.backend.apps(args.get("query", ""))
            limit = args.get("limit", 100)
            if not 1 <= limit <= 500:
                raise ValueError("limit must be 1..500")
            return {
                "ok": True,
                "packages": packages[:limit],
                "total": len(packages),
                "truncated": len(packages) > limit,
                "installed_verified": True,
                "scope": "Third-party packages; query matches package ID",
            }
        if op in {"tap", "long_press", "double_tap"}:
            return self._tap(kind=op, **args)
        if op == "drag":
            return self._drag(**args)
        if op == "scroll_until":
            return self._scroll_until(**args)
        if op == "swipe":
            return self._swipe(**args)
        if op == "type_text":
            return self._type(**args)
        if op == "press_button":
            self._write(self.backend.press, args["name"])
            return self._after(args["observe"], args["wait_for"])
        if op == "launch_app":
            self._write(self.backend.launch, args["package"])
            return self._after(args["observe"], args["wait_for"])
        raise ValueError("Unknown operation")

    def _nodes(self):
        return parse_tree(self.backend.tree())

    def _context(self):
        fg = self.backend.foreground()
        return {
            "serial": self.serial,
            "package": fg.get("package"),
            "activity": fg.get("activity"),
            "size": list(self.backend.size()),
        }

    def _observe(self, mode="tree", max_nodes=200, *, nodes=None):
        context = self._context()
        token = uuid.uuid4().hex
        self.observations[token] = context
        if len(self.observations) > 50:
            del self.observations[next(iter(self.observations))]
        result = {
            "observation_id": token,
            "foreground": context["package"],
            "activity": context["activity"],
            "viewport": context["size"],
            "coordinate_system": "Android display pixels",
        }
        if mode in {"tree", "both"}:
            result["tree"] = compact(
                self._nodes() if nodes is None else nodes, context["size"], max_nodes
            )
        if mode in {"screenshot", "both"}:
            self._usable()
            picture = self.backend.screenshot()
            original = picture.size
            if tuple(context["size"]) != original:
                raise UseError("display_changed", "Display geometry changed; observe again")
            picture.thumbnail((1080, 1600))
            buffer = io.BytesIO()
            picture.convert("RGB").save(buffer, format="JPEG", quality=80)
            result["image"] = {
                "mime_type": "image/jpeg",
                "width": picture.width,
                "height": picture.height,
                "pixel_to_device": [original[0] / picture.width, original[1] / picture.height],
            }
            # Internal payload removed from JSON and emitted as an MCP ImageContent block.
            result["_image_data"] = base64.b64encode(buffer.getvalue()).decode()
        self._check_paused()
        return result

    def _check_observation(self, token):
        if token is None:
            return
        if self.observations.get(token) != self._context():
            raise UseError("stale_observation", "Observation is unknown or app/display changed")

    def _resolve(self, selector, *, editable=False):
        found = matches(self._nodes(), Selector.model_validate(selector))
        size = self.backend.size()
        if not found:
            raise UseError("target_not_found", "No matching target")
        if len(found) > 1:
            raise UseError(
                "ambiguous_target",
                "Choose a candidate with selector.index",
                candidates=[
                    {**n, "index": i, "tap": tap_point(n, size)} for i, n in enumerate(found[:30])
                ],
                total=len(found),
            )
        node = found[0]
        if not in_viewport(node, size):
            raise UseError("offscreen_target", "Target lies outside the display")
        if not node["enabled"]:
            raise UseError("disabled_target", "Target is disabled")
        if editable and (
            node["password"] or not node["focused"] or not node["class_name"].endswith("EditText")
        ):
            raise UseError("no_focused_field", "Focus an editable non-password field first")
        return node

    def _write(self, fn, *args):
        self.mutation_attempted = True
        result = fn(*args)
        if result is False:
            raise UseError("action_uncertain", "Device did not acknowledge action", uncertain=True)
        return result

    def _limit_deadline(self, timeout):
        deadline = time.monotonic() + timeout
        return min(deadline, self._deadline) if self._deadline is not None else deadline

    def _wait_condition(self, condition):
        condition = WaitCondition.model_validate(condition)
        selector = condition.selector
        deadline = self._limit_deadline(condition.timeout_seconds)
        start = time.monotonic()
        while True:
            self._usable()
            nodes = self._nodes()
            self._usable()
            size = self.backend.size()
            found = matches([n for n in nodes if in_viewport(n, size)], selector)
            # An empty/failed hierarchy is not evidence that a target disappeared.
            met = bool(found) if condition.state == "visible" else bool(nodes) and not found
            timed_out = not met and time.monotonic() >= deadline
            if met or timed_out:
                return {
                    "condition_met": met,
                    "state": condition.state,
                    "found": bool(found),
                    "matches": found[:20],
                    "timed_out": timed_out,
                    "elapsed_seconds": round(time.monotonic() - start, 3),
                }, nodes
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                return {
                    "condition_met": False,
                    "state": condition.state,
                    "found": bool(found),
                    "matches": found[:20],
                    "timed_out": True,
                    "elapsed_seconds": round(time.monotonic() - start, 3),
                }, nodes

    def _after(self, observe, wait_for=None):
        result = {"ok": True, "action_executed": True, "verified": False}
        nodes = None
        if wait_for is not None:
            waited, nodes = self._wait_condition(wait_for)
            result["wait"] = waited
            if not waited["condition_met"]:
                result.update(
                    ok=False,
                    error={
                        "code": "postcondition_timeout",
                        "message": "Action acknowledged; requested UI condition did not arrive",
                        "uncertain": False,
                    },
                    replay_action=False,
                )
        if observe != "none":
            self._usable()
            result["observation"] = self._observe(observe, nodes=nodes)
        return result

    def _target_point(self, selector, x, y):
        if selector:
            x, y = tap_point(self._resolve(selector), self.backend.size())
        self._validate_point((x, y))
        return x, y

    def _validate_point(self, point):
        w, h = self.backend.size()
        if not (0 <= point[0] < w and 0 <= point[1] < h):
            raise ValueError("Coordinates outside display")

    def _tap(
        self,
        selector,
        x,
        y,
        observation_id,
        observe,
        wait_for=None,
        kind="tap",
        duration=None,
        interval=None,
    ):
        self._check_observation(observation_id)
        x, y = self._target_point(selector, x, y)
        if kind == "long_press":
            self._write(self.backend.long_press, x, y, duration)
        elif kind == "double_tap":
            self._write(self.backend.double_tap, x, y, interval)
        else:
            self._write(self.backend.tap, x, y)
        return self._after(observe, wait_for)

    def _drag(self, start, end, duration, observation_id, observe, wait_for=None):
        self._check_observation(observation_id)
        start, end = (start["x"], start["y"]), (end["x"], end["y"])
        self._validate_point(start)
        self._validate_point(end)
        self._write(self.backend.drag, start, end, duration)
        return self._after(observe, wait_for)

    def _swipe(self, direction, region, duration, observation_id, observe, wait_for=None):
        self._check_observation(observation_id)
        w, h = self.backend.size()
        r = region or {
            "x": int(w * 0.1),
            "y": int(h * 0.2),
            "width": int(w * 0.8),
            "height": int(h * 0.6),
        }
        if r["x"] + r["width"] > w or r["y"] + r["height"] > h:
            raise ValueError("Region outside display")
        x, y, rw, rh = (r[k] for k in ("x", "y", "width", "height"))
        left, right = (x + int(rw * 0.2), y + rh // 2), (x + int(rw * 0.8), y + rh // 2)
        top, bottom = (x + rw // 2, y + int(rh * 0.2)), (x + rw // 2, y + int(rh * 0.8))
        start, end = {
            "up": (bottom, top),
            "down": (top, bottom),
            "left": (right, left),
            "right": (left, right),
        }[direction]
        if start == end:
            raise ValueError("Swipe region is too small for this direction")
        self._write(self.backend.swipe, start, end, duration)
        return self._after(observe, wait_for)

    def _scroll_until(
        self,
        selector,
        direction,
        region,
        duration,
        max_swipes,
        timeout_seconds,
        settle_seconds,
        observe,
    ):
        deadline = self._limit_deadline(timeout_seconds)
        context = self._context()
        w, h = context["size"]
        region = region or {
            "x": int(w * 0.1),
            "y": int(h * 0.2),
            "width": int(w * 0.8),
            "height": int(h * 0.6),
        }
        if region["x"] + region["width"] > w or region["y"] + region["height"] > h:
            raise ValueError("Region outside display")
        selector = Selector.model_validate(selector)
        previous = None
        unchanged = 0
        swipes = 0
        while True:
            self._usable()
            if context != self._context():
                raise UseError("scroll_context_changed", "App/activity/display changed; inspect UI")
            nodes = self._nodes()
            self._usable()
            if context != self._context():
                raise UseError("scroll_context_changed", "App/activity/display changed; inspect UI")
            visible = [n for n in nodes if in_viewport(n, (w, h))]
            found = matches(visible, selector)
            fingerprint = self._scroll_fingerprint(visible, region)
            if previous is not None:
                unchanged = unchanged + 1 if fingerprint == previous else 0
            previous = fingerprint
            if found:
                reason = "found"
            elif not nodes:
                reason = "empty_tree"
            elif time.monotonic() >= deadline:
                reason = "timeout"
            elif unchanged >= 2:
                reason = "no_progress"
            elif swipes >= max_swipes:
                reason = "max_swipes"
            else:
                # Recheck after the read and before each individual intentional swipe.
                self._usable()
                if context != self._context():
                    raise UseError(
                        "scroll_context_changed", "App/activity/display changed; inspect UI"
                    )
                if time.monotonic() >= deadline:
                    reason = "timeout"
                else:
                    self._swipe(direction, region, duration, None, "none")
                    swipes += 1
                    time.sleep(min(settle_seconds, max(0, deadline - time.monotonic())))
                    continue
            result = {
                "ok": True,
                "found": bool(found),
                "matches": found[:20],
                "swipes": swipes,
                "stop_reason": reason,
                "action_executed": swipes > 0,
                "verified": False,
                "replay_action": False,
            }
            if observe != "none":
                self._usable()
                result["observation"] = self._observe(observe, nodes=nodes)
            return result

    @staticmethod
    def _scroll_fingerprint(nodes, region):
        # Exclude clocks/status bars outside the requested scroll region.
        x, y, w, h = (region[k] for k in ("x", "y", "width", "height"))
        return tuple(
            (n["class_name"], n["resource_id"], n["text"], n["description"], tuple(n["rect"]))
            for n in nodes
            if n["rect"][0] < x + w
            and n["rect"][1] < y + h
            and n["rect"][0] + n["rect"][2] > x
            and n["rect"][1] + n["rect"][3] > y
        )

    def _type(self, text, selector, replace, allow_newlines, verify, observe, wait_for=None):
        node = self._resolve(
            selector or {"focused": True, "class_name": "android.widget.EditText"}, editable=True
        )
        native = {"className": node["class_name"], "focused": True}
        field_filter = {"class_name": node["class_name"], "focused": True}
        if node["package"]:
            native["packageName"] = node["package"]
            field_filter["package"] = node["package"]
        if node["resource_id"]:
            native["resourceId"] = node["resource_id"]
            field_filter["resource_id"] = node["resource_id"]
        # The Android XML dumper replaces supplementary Unicode (e.g. emoji) with dots.
        # Native getText preserves it; using XML for append would silently corrupt drafts.
        expected = text if replace else self.backend.get_text(native) + text
        self._write(self.backend.set_text, expected, native)
        if verify:
            self._resolve(field_filter, editable=True)
            readback = self.backend.get_text(native)
            if readback != expected:
                raise UseError(
                    "input_verification_failed",
                    "Field text differs; inspect before retrying",
                    uncertain=True,
                    characters_expected=len(expected),
                    characters_observed=len(readback),
                )
        result = self._after(observe, wait_for)
        result.update(input_complete=True, verified=verify, characters=len(expected))
        if verify:
            result["verification_source"] = "native_getText"
        return result

    def _batch(self, steps):
        results = []
        self._deadline = time.monotonic() + 25
        for i, (op, args) in enumerate(steps):
            if time.monotonic() >= self._deadline:
                return {
                    "ok": True,
                    "completed_steps": i,
                    "stopped_at": i,
                    "stop_reason": "time_budget",
                    "results": results,
                }
            self.mutation_attempted = False
            try:
                self._usable()
                if time.monotonic() >= self._deadline:
                    return {
                        "ok": True,
                        "completed_steps": i,
                        "stopped_at": i,
                        "stop_reason": "time_budget",
                        "results": results,
                    }
                result = self._dispatch(op, args)
                self._check_paused()
            except Exception as exc:
                if not isinstance(exc, UseError):
                    exc = UseError(
                        "action_uncertain" if self.mutation_attempted else "transport_error",
                        "Batch step failed",
                        uncertain=self.mutation_attempted,
                        cause=type(exc).__name__,
                    )
                results.append(self._error(exc))
                return {
                    "ok": False,
                    "completed_steps": i,
                    "stopped_at": i,
                    "stop_reason": "error",
                    "results": results,
                    "replay_action": False,
                }
            results.append(result)
            if (
                not result["ok"]
                or (op == "wait" and not result["condition_met"])
                or (op == "scroll_until" and not result["found"])
            ):
                return {
                    "ok": False,
                    "completed_steps": i + 1,
                    "stopped_at": i + 1,
                    "stop_reason": (
                        "postcondition_timeout"
                        if not result["ok"]
                        else "scroll_not_found"
                        if op == "scroll_until"
                        else "wait_timeout"
                    ),
                    "results": results,
                    "replay_action": False,
                }
            if op == "press_button" and args["name"] == "enter" and i + 1 < len(steps):
                return {
                    "ok": True,
                    "completed_steps": i + 1,
                    "stopped_at": i + 1,
                    "stop_reason": "submission_requires_observation",
                    "results": results,
                }
        return {
            "ok": True,
            "completed_steps": len(steps),
            "stop_reason": "complete",
            "results": results,
        }


def extract_images(value):
    """Return JSON-safe results and image bytes, including nested batch/error observations."""
    images = []

    def walk(item):
        if isinstance(item, dict):
            if "_image_data" in item:
                return clean_image(item)
            return {k: walk(v) for k, v in item.items()}
        if isinstance(item, list):
            return [walk(v) for v in item]
        return item

    def clean_image(item):
        images.append(item["_image_data"])
        return {k: walk(v) for k, v in item.items() if k != "_image_data"}

    return walk(value), images


def save_images(value, directory):
    data, images = extract_images(value)
    paths = []
    if images:
        directory = Path(directory).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for encoded in images:
            path = directory / (uuid.uuid4().hex + ".jpg")
            path.write_bytes(base64.b64decode(encoded))
            path.chmod(0o600)
            paths.append(str(path))
    if paths:
        data["image_paths"] = paths
    return data
