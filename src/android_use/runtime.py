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
    viewport_from_info,
    write_config,
)
from .interaction import Variation
from .models import ARG_MODELS, BatchStep, Selector, WaitCondition
from .tree import compact, in_viewport, matches, parse_tree, tap_point

# Host-decider sessions: idle lifetime between aua_goal_decide calls, and how many may wait.
GOAL_SESSION_SECONDS = 300
GOAL_SESSION_LIMIT = 8
TAP_OPS = {"tap", "long_press", "double_tap"}


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
        self.goal_sessions = {}

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
            if op == "screen_action":
                from .settings import JEV_ACTIONS, settings_action

                if args.get("action") in JEV_ACTIONS:
                    return settings_action(
                        args["action"], {k: v for k, v in args.items() if k != "action"}
                    )
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
                try:
                    return client.request("frame", args)
                except UseError as exc:
                    if exc.code != "preview_unreachable":
                        raise
                    # Only a read may be retried after recovering the endpoint.
                    # Never replay a toolbar/phone action after a transport error.
                    client.ensure()
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

    def _prune_goal_sessions(self):
        now = time.monotonic()
        for token in [t for t, s in self.goal_sessions.items() if s["expires"] <= now]:
            del self.goal_sessions[token]

    def _goal_result(self, result, policy, run):
        if result.get("status") == "decision_required":
            self._prune_goal_sessions()
            while len(self.goal_sessions) >= GOAL_SESSION_LIMIT:
                del self.goal_sessions[next(iter(self.goal_sessions))]  # Oldest first.
            token = uuid.uuid4().hex
            self.goal_sessions[token] = {
                "policy": policy,
                "run": run,
                "device_key": self.device_key,
                "options": set(result["decision"]["options"]),
                "expires": time.monotonic() + GOAL_SESSION_SECONDS,
            }
            result["decision"].update(token=token, expires_in_seconds=GOAL_SESSION_SECONDS)
        return result

    def _take_goal_session(self, token, choice):
        # Tokens are one-shot and in-memory: a CLI process or server restart cannot resume.
        self._prune_goal_sessions()
        session = self.goal_sessions.get(token)
        if session is None or (self.device_key and session["device_key"] != self.device_key):
            raise UseError(
                "goal_session_expired", "Unknown or expired decision token; inspect and replan"
            )
        if choice not in session["options"]:
            # Keep the token: nothing was read or executed for an invalid ID.
            raise UseError("invalid_choice", "Choice must be one of the offered option IDs")
        del self.goal_sessions[token]
        session["policy"].answer = choice
        return session

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
        info = self.backend.d.info
        if not info.get("screenOn", False):
            raise UseError("screen_off", "Wake and unlock the device, then call ready")
        self._check_paused()
        return info

    def _checked_context(self):
        # Scoped reuse only: never keep deviceInfo across inference, actions or waits.
        info = self._usable()
        context = self._context(device_info=info)
        self._check_paused()
        return context

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
                from .installation import diagnose

                return {
                    "ok": True,
                    "devices": discover(),
                    "configured": bool(read_config()),
                    "python": os.sys.version.split()[0],
                    "state_dir": str(state_dir()),
                    "installation": diagnose(),
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
                if any(step.get("visual") for _, step in args["steps"]):
                    # A paid, screenshot-sending location is never queued behind other actions.
                    raise ValueError("Visual taps are not allowed in batch")
            goal_config = session = None
            if op in {"execute_goal", "read_fields"}:
                from .jev import JevConfig

                decider = args["decider"] if op == "execute_goal" else "jev"
                if decider == "auto":
                    from .settings import effective_jev_settings

                    jev = effective_jev_settings()
                    decider = "jev" if jev["enabled"] and jev["key"] else "host"
                if decider == "jev":
                    # Fail before discovering or reading a phone.
                    goal_config = JevConfig.from_env()
            visual = op == "locate" or (op in TAP_OPS and args.get("visual") is not None)
            if visual:
                from .jev import JevConfig
                from .vision import require_images

                goal_config = JevConfig.from_env()
                require_images(goal_config)
            if op == "goal_decide":
                session = self._take_goal_session(args["token"], args["choice"])
            self._connect()
            with self._guard():
                if op == "resume":
                    self._pause_path().unlink(missing_ok=True)
                    return {"ok": True, "paused": False}
                if op == "goal_decide":
                    from .executor import execute

                    # The executor's first read rechecks pause/lock/scope with the ledger kept.
                    result = execute(self, None, session["policy"], session["run"])
                    return self._goal_result(result, session["policy"], session["run"])
                self._usable()
                if op == "execute_goal":
                    from .executor import GoalRun, HostPolicy, execute
                    from .jev import JevPolicy
                    from .models import ExecuteGoalArgs

                    if goal_config is not None:
                        return execute(self, args, JevPolicy(goal_config))
                    run, policy = GoalRun(ExecuteGoalArgs.model_validate(args)), HostPolicy()
                    return self._goal_result(execute(self, None, policy, run), policy, run)
                if op == "read_fields":
                    from .fields import read_fields
                    from .jev import JevPolicy

                    return read_fields(self, args, JevPolicy(goal_config))
                if visual:
                    from .jev import JevPolicy

                    policy = JevPolicy(goal_config)
                    try:
                        if op == "locate":
                            from .vision import locate

                            return locate(self, args, policy)
                        return self._visual_tap(op, args, policy)
                    finally:
                        policy.close()
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
        if op in TAP_OPS:
            args = {k: v for k, v in args.items() if k != "visual"}
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

    def _context(self, *, device_info=None):
        fg = self.backend.foreground()
        return {
            "serial": self.serial,
            "package": fg.get("package"),
            "activity": fg.get("activity"),
            "size": list(viewport_from_info(device_info) or self.backend.size()),
        }

    def _observe(self, mode="tree", max_nodes=200, *, nodes=None, context=None):
        context = self._context() if context is None else context
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

    def _variation_prepare(self, options, sampler=None):
        if options is None:
            return None
        sampler = sampler or Variation(options)
        delay = sampler.delay()
        if self._deadline is not None and time.monotonic() + delay >= self._deadline:
            raise UseError("interaction_time_budget", "Variation exceeds remaining time budget")
        if delay:
            context = self._checked_context()
            self._check_paused()
            nodes = self._nodes()
            self._usable()
            if any(n["password"] and in_viewport(n, context["size"]) for n in nodes):
                raise UseError("authentication_required", "Pause for authentication")
            end = time.monotonic() + delay
            while time.monotonic() < end:
                self._usable()
                time.sleep(min(.05, max(0, end - time.monotonic())))
            self._usable()
            if context != self._checked_context():
                raise UseError("interaction_context_changed", "App or display changed during delay")
            after = self._nodes()
            self._usable()
            if nodes != after:
                raise UseError("interaction_context_changed", "UI changed during delay; observe again")
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise UseError("interaction_time_budget", "Variation consumed remaining time budget")
        return sampler

    @staticmethod
    def _variation_result(result, sampler):
        if sampler is not None:
            result["variation"] = dict(sampler.audit)
        return result

    def _variation_before_write(self, sampler):
        if sampler is not None:
            self._usable()
            if self._deadline is not None and time.monotonic() >= self._deadline:
                raise UseError("interaction_time_budget", "No budget remains for this action")

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
        variation=None,
    ):
        if variation is not None:
            self._check_observation(observation_id)
        sampled = self._variation_prepare(variation)
        self._check_observation(observation_id)
        if sampled is not None and selector:
            node = self._resolve(selector)
            size = self.backend.size()
            point = tap_point(node, size)
            x, y = sampled.point(point, node["rect"], size)
            self._validate_point((x, y))
        else:
            x, y = self._target_point(selector, x, y)
        if sampled is not None:
            sampled.audit["point"] = [x, y]
            if kind == "long_press":
                duration = sampled.duration(duration, (.5, 3))
            elif kind == "double_tap":
                interval = sampled.duration(interval, (.04, .25), "interval")
        self._variation_before_write(sampled)
        if kind == "long_press":
            self._write(self.backend.long_press, x, y, duration)
        elif kind == "double_tap":
            self._write(self.backend.double_tap, x, y, interval)
        else:
            self._write(self.backend.tap, x, y)
        return self._variation_result(self._after(observe, wait_for), sampled)

    def _visual_tap(self, op, args, policy):
        from .vision import visual_point

        self._check_observation(args["observation_id"])
        located = visual_point(self, args["visual"], policy)
        x, y = located["tap"]
        rest = {k: v for k, v in args.items() if k not in {"visual", "selector", "x", "y"}}
        result = self._tap(kind=op, selector=None, x=x, y=y, **{**rest, "observation_id": None})
        result["visual"] = located
        return result

    def _drag(self, start, end, duration, observation_id, observe, wait_for=None, variation=None):
        if variation is not None:
            self._check_observation(observation_id)
        sampled = self._variation_prepare(variation)
        self._check_observation(observation_id)
        start, end = (start["x"], start["y"]), (end["x"], end["y"])
        self._validate_point(start)
        self._validate_point(end)
        if sampled is not None:
            duration = sampled.duration(duration, (.1, 3))
            sampled.audit.update(start=list(start), end=list(end))
        self._variation_before_write(sampled)
        self._write(self.backend.drag, start, end, duration)
        return self._variation_result(self._after(observe, wait_for), sampled)

    def _swipe(self, direction, region, duration, observation_id, observe, wait_for=None,
               variation=None, *, _variation_sampler=None):
        if variation is not None:
            self._check_observation(observation_id)
        sampled = self._variation_prepare(variation, _variation_sampler)
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
        if sampled is not None:
            start, end = sampled.swipe(start, end, (x, y, rw, rh), (w, h), direction)
            duration = sampled.duration(duration, (.1, 2))
            sampled.audit.update(start=list(start), end=list(end))
        if start == end:
            raise ValueError("Swipe region is too small for this direction")
        self._variation_before_write(sampled)
        self._write(self.backend.swipe, start, end, duration)
        return self._variation_result(self._after(observe, wait_for), sampled)

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
        variation=None,
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
        sampler = Variation(variation) if variation is not None else None
        variations = []
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
                    previous_deadline = self._deadline
                    self._deadline = deadline
                    try:
                        action = self._swipe(direction, region, duration, None, "none",
                                             variation=variation, _variation_sampler=sampler)
                    finally:
                        self._deadline = previous_deadline
                    if sampler is not None:
                        variations.append(action["variation"])
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
            if sampler is not None:
                result["variations"] = variations
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
