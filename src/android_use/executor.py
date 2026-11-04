"""Android Use owns execution. A decision policy selects IDs, never device commands."""

import hashlib
import json
import time
from typing import Protocol

from .backend import UseError
from .models import ARG_MODELS, ExecuteGoalArgs
from .tree import in_viewport, matches, tap_point


class DecisionPolicy(Protocol):
    """Jev or a host adapter receives UI facts and returns a candidate decision only."""

    metrics: dict

    def decide(self, state: dict, criteria: dict, timeout: float) -> dict: ...
    def summary(self) -> dict: ...
    def close(self) -> None: ...


def snapshot(runtime, package):
    runtime._usable()
    before = runtime._context()
    nodes = runtime._nodes()
    runtime._usable()
    after = runtime._context()
    if before != after or after["package"] != package:
        raise UseError(
            "goal_scope_changed", "Foreground app or display changed; inspect current state"
        )
    visible = [n for n in nodes if in_viewport(n, after["size"])]
    if any(n["password"] for n in visible):
        raise UseError("authentication_required", "Pause for user authentication before continuing")
    owned = [n for n in visible if n["package"] == package]
    if not owned:
        raise UseError("goal_empty_tree", "No visible app-owned accessibility nodes")
    # A foreign dialog/keyboard can cover the app. Do not target through it.
    if any(
        n["package"] and n["package"] != package and (n["clickable"] or n["focused"])
        for n in visible
    ):
        raise UseError("goal_foreign_overlay", "Another package owns interactive UI; hand off")
    # XML indices are global traversal offsets, not stable Android element identities.
    # A System UI sibling can shift every app index without changing any target.
    semantic = [{k: v for k, v in n.items() if k not in {"id", "parent"}} for n in owned]
    fingerprint = hashlib.sha256(
        json.dumps([after, semantic], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return {"context": after, "nodes": owned, "fingerprint": fingerprint}


def candidates(snap, args):
    result = {}
    size = snap["context"]["size"]

    def add(kind, params, label):
        if len(result) < 200:
            result["a" + str(len(result))] = {"op": kind, "args": params, "label": label}

    for node in snap["nodes"]:
        if not node["enabled"]:
            continue
        label = {k: node[k][:300] for k in ("text", "description", "resource_id", "class_name")}
        label["node"] = node["id"]
        x, y = tap_point(node, size)
        editable = node["class_name"].endswith("EditText")
        if node["clickable"] or editable:
            if "tap" in args.actions:
                add("tap", {"x": x, "y": y}, label)
            if "long_press" in args.actions:
                add("long_press", {"x": x, "y": y}, label)
        if editable and node["focused"] and "type_text" in args.actions:
            # Reject newlines at candidate construction: some editors submit them.
            for text in args.texts:
                if "\n" not in text and "\r" not in text:
                    add(
                        "type_text",
                        {
                            "text": text,
                            "verify": True,
                            "selector": {
                                "class_name": node["class_name"],
                                "focused": True,
                                "package": args.package,
                                **(
                                    {"resource_id": node["resource_id"]}
                                    if node["resource_id"]
                                    else {}
                                ),
                            },
                        },
                        {**label, "set_exact_text": text},
                    )
        if node["scrollable"] and "swipe" in args.actions:
            nx, ny, w, h = node["rect"]
            left, top = max(0, nx), max(0, ny)
            region = {
                "x": left,
                "y": top,
                "width": min(size[0], nx + w) - left,
                "height": min(size[1], ny + h) - top,
            }
            if region["width"] >= 10 and region["height"] >= 10:
                for direction in ("up", "down", "left", "right"):
                    add("swipe", {"direction": direction, "region": region}, label)
    if "back" in args.actions:
        add("press_button", {"name": "back"}, "Go back within the current app")
    if "wait" in args.actions:
        add("wait_local", {}, "Wait briefly for pending UI changes")
    return result


def completed(snap, args):
    if args.completion is None:
        return False
    found = bool(matches(snap["nodes"], args.completion))
    return found if args.completion_state == "visible" else not found


def execute(runtime, args, policy: DecisionPolicy):
    args = ExecuteGoalArgs.model_validate(args)
    start = time.monotonic()
    deadline = start + args.timeout_seconds
    runtime._deadline = deadline
    trace = []
    decisions = []
    history = []
    snap = None
    stale, unchanged, reads, actions = 0, 0, 0.0, 0.0

    def read():
        nonlocal reads
        began = time.monotonic()
        try:
            return snapshot(runtime, args.package)
        finally:
            reads += time.monotonic() - began

    def finish(reason, verified=False, error=None):
        result = {
            "ok": error is None,
            "status": "completed" if verified else "handoff",
            "verified": verified,
            "stop_reason": reason,
            "steps": trace,
            "decisions": decisions,
            "roles": {
                "planner": "host_agent",
                "decision": getattr(policy, "name", "external_policy"),
                "executor": "android_use",
            },
            "action_attempted": any(s["op"] != "wait_local" for s in trace),
            "replay_action": False,
            "metrics": {
                **policy.summary(),
                "elapsed_seconds": time.monotonic() - start,
                "device_read_seconds": reads,
                "device_action_seconds": actions,
                "stale_decisions": stale,
            },
        }
        if error:
            result["error"] = error.as_dict()
        if not verified:
            uncertain = [i for i, step in enumerate(trace) if step["state"] == "uncertain"]
            result["handoff"] = {
                "reason": reason,
                "completed_actions": sum(s["state"] == "executed" for s in trace),
                "uncertain_steps": uncertain,
                "next": "user_authentication"
                if reason
                in {"authentication_required", "phone_locked", "screen_off", "user_takeover"}
                else "observe_and_replan",
            }
        if error is None and snap is not None and args.observe == "tree":
            # Reuse the checked final snapshot. The host needn't issue another tree read.
            # Never expose an old snapshot after authentication/transport/scope errors.
            try:
                runtime._usable()
                if runtime._context() == snap["context"]:
                    result["observation"] = runtime._observe("tree", nodes=snap["nodes"])
            except Exception:
                pass
        result["metrics"]["elapsed_seconds"] = time.monotonic() - start
        return result

    try:
        snap = read()
        while True:
            runtime._usable()
            if completed(snap, args):
                return finish("completion_selector", True)
            if time.monotonic() >= deadline:
                return finish("timeout")
            if len(trace) >= args.max_steps:
                return finish("max_steps")
            if policy.metrics["model_calls"] >= args.max_model_calls:
                return finish("max_model_calls")
            if unchanged >= 3:
                return finish("no_progress")
            options = candidates(snap, args)
            criteria = {
                key: {
                    "action": value["op"],
                    "target": value["label"],
                    **(
                        {
                            "finger_motion": value["args"]["direction"],
                            "meaning": {
                                "up": "reveal later rows below",
                                "down": "reveal earlier rows above",
                                "left": "reveal content to the right",
                                "right": "reveal content to the left",
                            }[value["args"]["direction"]],
                        }
                        if value["op"] == "swipe"
                        else {}
                    ),
                }
                for key, value in options.items()
            }
            criteria.update(
                DONE="Goal already achieved in the current UI",
                BLOCKED="Cannot safely achieve the goal with these actions",
            )
            state = {
                "goal": args.goal,
                "ui": [
                    {
                        k: (v[:300] if isinstance(v, str) else v)
                        for k, v in n.items()
                        if k not in {"package", "parent", "password"}
                    }
                    for n in snap["nodes"][:120]
                ],
                "ui_truncated": len(snap["nodes"]) > 120,
                "history": history[-8:],
            }
            if len(json.dumps([state, criteria], ensure_ascii=False).encode()) > 32_000:
                return finish("context_too_large")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return finish("timeout")
            decision = policy.decide(state, criteria, remaining)
            audit = {
                "choice": decision["choice"],
                "probability": decision["probability"],
                "margin": decision["margin"],
                "state": "pending",
            }
            decisions.append(audit)
            runtime._usable()
            if time.monotonic() >= deadline:
                return finish("timeout")
            fresh = read()
            if fresh["fingerprint"] != snap["fingerprint"]:
                audit["state"] = "stale"
                stale += 1
                snap = fresh
                continue
            if (
                decision["probability"] < args.min_probability
                or decision["margin"] < args.min_margin
            ):
                audit["state"] = "handoff"
                return finish("low_confidence")
            choice = decision["choice"]
            if choice == "BLOCKED":
                audit["state"] = "handoff"
                return finish("model_blocked")
            if choice == "DONE":
                audit["state"] = "handoff"
                return finish(
                    "model_done_unverified"
                    if args.completion is None
                    else "model_done_completion_unmet"
                )
            if choice not in options:
                raise UseError("jev_invalid_response", "Unknown candidate; no action executed")
            selected = options[choice]  # Consume this decision once, before any mutation.
            runtime._usable()
            if runtime._context() != fresh["context"]:
                raise UseError("goal_scope_changed", "App or display changed before action")
            if time.monotonic() >= deadline:
                return finish("timeout")
            entry = {"op": selected["op"], "candidate": choice, "state": "attempted"}
            trace.append(entry)
            audit["state"] = "consumed"
            runtime.mutation_attempted = False
            began = time.monotonic()
            try:
                if selected["op"] == "wait_local":
                    time.sleep(min(0.35, max(0, deadline - time.monotonic())))
                else:
                    params = (
                        ARG_MODELS[selected["op"]].model_validate(selected["args"]).model_dump()
                    )
                    runtime._dispatch(selected["op"], params)
                entry["state"] = "executed"
                history.append({"action": selected["op"], "target": selected["label"]})
            except Exception:
                entry["state"] = "uncertain" if runtime.mutation_attempted else "failed"
                raise
            finally:
                actions += time.monotonic() - began
            # Record the action before reading, so a read failure cannot erase it.
            next_snap = read()
            unchanged = unchanged + 1 if next_snap["fingerprint"] == snap["fingerprint"] else 0
            snap = next_snap
    except UseError as exc:
        return finish(exc.code, error=exc)
    except Exception as exc:
        return finish(
            "executor_error",
            error=UseError(
                "executor_error",
                "Execution stopped; inspect device before continuing",
                uncertain=bool(trace and trace[-1]["state"] == "uncertain"),
                cause=type(exc).__name__,
            ),
        )
    finally:
        policy.close()
