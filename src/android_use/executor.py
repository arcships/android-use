"""Android Use owns execution. A decision policy selects IDs, never device commands."""

import hashlib
import json
import time
from typing import Protocol

from .backend import UseError
from .decision_context import prepare_action_context
from .models import ARG_MODELS, ExecuteGoalArgs, Selector
from .tree import in_viewport, matches, tap_point


class DecisionPolicy(Protocol):
    """Jev or a host adapter receives UI facts and returns a candidate decision only."""

    metrics: dict

    def decide(self, state: dict, criteria: dict, timeout: float) -> dict: ...
    def summary(self) -> dict: ...
    def close(self) -> None: ...


def snapshot(runtime, package, *, allow_empty=False):
    before = runtime._checked_context()
    nodes = runtime._nodes()
    after = runtime._checked_context()
    if before != after or after["package"] != package:
        raise UseError(
            "goal_scope_changed", "Foreground app or display changed; inspect current state"
        )
    visible = [n for n in nodes if in_viewport(n, after["size"])]
    if any(n["password"] for n in visible):
        raise UseError("authentication_required", "Pause for user authentication before continuing")
    owned = [n for n in visible if n["package"] == package]
    if not owned and not allow_empty:
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
    return {"context": after, "nodes": owned, "all_nodes": nodes, "fingerprint": fingerprint}


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
        if not node["text"] and not node["description"]:
            descendants = []
            owned_ids = {node["id"]}
            for child in snap["nodes"]:
                if child["parent"] in owned_ids:
                    owned_ids.add(child["id"])
                    if child["text"] or child["description"]:
                        descendants.append(child["text"] or child["description"])
            if descendants:
                label["context"] = " | ".join(descendants)[:300]
        x, y = tap_point(node, size)
        editable = node["class_name"].endswith("EditText")
        # Unlabelled layout wrappers often duplicate a real child button. They can
        # split target probability or tap blank space; retain the concrete child.
        nested_control = False
        if not node["text"] and not node["description"] and not editable:
            descendants = {node["id"]}
            for child in snap["nodes"]:
                if child["parent"] in descendants:
                    descendants.add(child["id"])
                    nested_control |= child["enabled"] and (
                        child["clickable"] or child["class_name"].endswith("EditText")
                    )
        if (node["clickable"] or editable) and not nested_control:
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
        elif (
            editable and args.input_macros and "tap" in args.actions and "type_text" in args.actions
        ):
            # Binding must survive focus/text changes. Never guess a field by position.
            binding = {"package": args.package, "class_name": node["class_name"]}
            if node["resource_id"]:
                binding["resource_id"] = node["resource_id"]
            elif node["description"]:
                binding["description"] = node["description"]
            if (node["resource_id"] or node["description"]) and len(
                matches(snap.get("all_nodes", snap["nodes"]), Selector.model_validate(binding))
            ) == 1:
                for text in args.texts:
                    if "\n" not in text and "\r" not in text:
                        add(
                            "focus_type_text",
                            {"selector": binding, "text": text},
                            {
                                **label,
                                "focus_and_set_exact_text": text,
                                "verification": "native text readback; no submit",
                            },
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


def effect_fingerprint(snap):
    # Empty layout animation is not evidence that a business action had an effect.
    content = [
        {k: v for k, v in n.items() if k not in {"id", "parent", "rect"}} for n in snap["nodes"]
    ]
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def completed(snap, args):
    if args.completion is None:
        return False
    found = bool(matches(snap["nodes"], args.completion))
    return found if args.completion_state == "visible" else not found


def focus_type_text(runtime, params, snap, package, deadline, entry, read):
    """Two bounded native actions, one decision; every mutation has its own ledger."""
    selector = Selector.model_validate(params["selector"])

    def target(current, focused=False, same_rect=True):
        if current["context"] != snap["context"]:
            raise UseError("goal_scope_changed", "Context changed within input macro")
        found = matches(current.get("all_nodes", current["nodes"]), selector)
        if len(found) != 1:
            raise UseError("macro_target_changed", "Input target is missing or ambiguous")
        node = found[0]
        if (
            not in_viewport(node, current["context"]["size"])
            or (same_rect and node["rect"] != original["rect"])
            or not node["enabled"]
            or node["password"]
            or not node["class_name"].endswith("EditText")
            or (focused and not node["focused"])
        ):
            raise UseError("macro_target_changed", "Input target is no longer an editable field")
        return node

    def guard():
        if runtime._checked_context() != snap["context"]:
            raise UseError("goal_scope_changed", "Context changed before macro action")
        if time.monotonic() >= deadline:
            raise UseError("goal_timeout", "Input macro budget exhausted; do not replay")

    initial = matches(snap["nodes"], selector)
    if len(initial) != 1:
        raise UseError("macro_target_changed", "Input target is missing or ambiguous")
    original = initial[0]
    node = target(snap)
    x, y = tap_point(node, snap["context"]["size"])
    substeps = entry["substeps"] = []

    def mutate(op, function, *values):
        guard()
        step = {"op": op, "state": "attempted"}
        substeps.append(step)
        runtime.mutation_attempted = False
        try:
            runtime._write(function, *values)
            step.update(state="executed", acknowledged=True)
        except Exception:
            step["state"] = "uncertain" if runtime.mutation_attempted else "failed"
            raise
        return step

    mutate("tap", runtime.backend.tap, x, y)
    focused = read()
    node = target(focused, focused=True)

    def layout(current):
        return [
            {
                k: v
                for k, v in item.items()
                if k not in {"id", "parent", "focused"}
                and not (k == "text" and matches([item], selector))
            }
            for item in current["nodes"]
        ]

    if layout(focused) != layout(snap):
        raise UseError(
            "macro_target_changed", "Unexpected layout change after focusing; inspect UI"
        )
    native = {"className": node["class_name"], "packageName": package, "focused": True}
    if node["resource_id"]:
        native["resourceId"] = node["resource_id"]
    else:
        native["description"] = node["description"]
    typed = mutate("type_text", runtime.backend.set_text, params["text"], native)
    try:
        checked = read()
        # Text/font metrics can resize the bound field after setText. No further
        # coordinate action is performed here; stable identity + focus + native
        # readback remain required. Geometry must still match before writing.
        target(checked, focused=True, same_rect=False)
        guard()
        if runtime.backend.get_text(native) != params["text"]:
            raise UseError(
                "input_verification_failed",
                "Macro input differs; inspect before retrying",
                uncertain=True,
            )
        guard()
        typed.update(verified=True, verification_source="native_getText")
    except Exception:
        typed.update(state="uncertain", verified=False)
        raise
    return checked


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
    consumed = set()

    def read(*, allow_empty=False):
        nonlocal reads
        began = time.monotonic()
        try:
            return snapshot(runtime, args.package, allow_empty=allow_empty)
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
                "completed_actions": sum(
                    child["state"] == "executed"
                    for step in trace
                    for child in step.get("substeps", [step])
                    if child["op"] != "wait_local"
                ),
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
                context = runtime._checked_context()
                if context == snap["context"]:
                    result["observation"] = runtime._observe(
                        "tree", nodes=snap["nodes"], context=context
                    )
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
            state, criteria, context_metrics = prepare_action_context(snap, args, options, history)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return finish("timeout")
            decision = policy.decide(state, criteria, remaining)
            audit = {
                "choice": decision["choice"],
                "probability": decision["probability"],
                "margin": decision["margin"],
                "state": "pending",
                "context": context_metrics,
                **({"heads": decision["heads"]} if "heads" in decision else {}),
            }
            decisions.append(audit)
            runtime._check_paused()
            if time.monotonic() >= deadline:
                runtime._usable()
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
            if runtime._checked_context() != fresh["context"]:
                raise UseError("goal_scope_changed", "App or display changed before action")
            if time.monotonic() >= deadline:
                return finish("timeout")
            signature = hashlib.sha256(
                json.dumps(
                    [effect_fingerprint(fresh), selected["op"], selected["args"]],
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            if selected["op"] not in {"wait_local", "swipe"} and signature in consumed:
                audit["state"] = "handoff"
                return finish("duplicate_action")
            consumed.add(signature)
            entry = {"op": selected["op"], "candidate": choice, "state": "attempted"}
            trace.append(entry)
            audit["state"] = "consumed"
            runtime.mutation_attempted = False
            began = time.monotonic()
            read_before = reads
            next_snap = None
            try:
                if selected["op"] == "wait_local":
                    time.sleep(min(0.35, max(0, deadline - time.monotonic())))
                elif selected["op"] == "focus_type_text":
                    next_snap = focus_type_text(
                        runtime, selected["args"], fresh, args.package, deadline, entry, read
                    )
                else:
                    params = (
                        ARG_MODELS[selected["op"]].model_validate(selected["args"]).model_dump()
                    )
                    runtime._dispatch(selected["op"], params)
                entry["state"] = "executed"
                history.append({"action": selected["op"], "target": selected["label"]})
            except Exception:
                if "substeps" in entry:
                    states = {s["state"] for s in entry["substeps"]}
                    entry["state"] = (
                        "uncertain"
                        if "uncertain" in states
                        else "partial"
                        if "executed" in states
                        else "failed"
                    )
                else:
                    entry["state"] = "uncertain" if runtime.mutation_attempted else "failed"
                raise
            finally:
                actions += max(0.0, time.monotonic() - began - (reads - read_before))
            # Record the action before reading, so a read failure cannot erase it.
            if selected["op"] not in {"wait_local", "swipe"}:
                # ACK only proves dispatch. Allow rendering to catch up without a
                # second model decision or a repeated mutation.
                effect_start = time.monotonic()
                effect_deadline = min(deadline, effect_start + args.effect_timeout_seconds)
                next_snap = read(allow_empty=True) if next_snap is None else next_snap
                while not next_snap["nodes"] or (
                    effect_fingerprint(next_snap) == effect_fingerprint(fresh)
                    and not completed(next_snap, args)
                ):
                    if time.monotonic() >= effect_deadline:
                        entry["effect"] = {
                            "observed": False,
                            "wait_seconds": time.monotonic() - effect_start,
                        }
                        snap = next_snap
                        return finish("uncertain_effect")
                    time.sleep(min(0.1, max(0, effect_deadline - time.monotonic())))
                    # A transition can briefly expose no app nodes. Retry only
                    # observation within this budget; keep all auth/scope guards.
                    next_snap = read(allow_empty=True)
                entry["effect"] = {
                    "observed": True,
                    "wait_seconds": time.monotonic() - effect_start,
                }
            else:
                next_snap = read() if next_snap is None else next_snap
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
