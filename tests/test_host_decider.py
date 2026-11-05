import time

import pytest

PACKAGE = "demo.app"
XML = """<hierarchy><node package="demo.app" class="android.widget.Button" text="{}"
enabled="true" clickable="true" bounds="[0,200][300,300]"/></hierarchy>"""


@pytest.fixture
def host_rig(rig, monkeypatch):
    monkeypatch.delenv("ANDROID_USE_JEV_ENABLED", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    runtime, backend = rig
    backend.xml = XML.format("Show")
    original = backend.tap

    def tap(*args):
        original(*args)
        backend.xml = XML.format(backend.next_label)

    backend.next_label = "Done"
    backend.tap = tap
    return runtime, backend


def start(runtime, **kwargs):
    return runtime.call("execute_goal", goal="Show the target", package=PACKAGE, **kwargs)


def decide(runtime, result, choice="a0"):
    return runtime.call("goal_decide", token=result["decision"]["token"], choice=choice)


def test_host_round_trip_executes_chosen_candidate_once(host_rig):
    runtime, backend = host_rig
    first = start(runtime, completion={"text": "Done"})
    assert first["ok"] and first["status"] == "decision_required" and not first["verified"]
    assert first["roles"]["decision"] == "host_agent" and not backend.actions
    decision = first["decision"]
    assert decision["options"]["a0"]["action"] == "tap"
    assert {"DONE", "BLOCKED"} <= decision["options"].keys()
    assert decision["state"]["ui"][0]["text"] == "Show"
    assert decision["previous_choice_stale"] is False

    done = decide(runtime, first)
    assert done["status"] == "completed" and done["verified"]
    assert len(backend.actions) == 1 and done["steps"][0]["state"] == "executed"
    assert done["metrics"]["model_calls"] == 1
    # One-shot token: a replayed decision never reaches the device again.
    assert decide(runtime, first)["error"]["code"] == "goal_session_expired"
    assert len(backend.actions) == 1


def test_choice_for_changed_ui_is_discarded(host_rig):
    runtime, backend = host_rig
    first = start(runtime)
    backend.xml = XML.format("Show again")
    again = decide(runtime, first)
    assert again["status"] == "decision_required" and not backend.actions
    assert again["decision"]["previous_choice_stale"] is True
    assert again["decision"]["token"] != first["decision"]["token"]
    assert again["metrics"]["stale_decisions"] == 1
    assert again["decision"]["state"]["ui"][0]["text"] == "Show again"


def test_unknown_choice_keeps_token(host_rig):
    runtime, backend = host_rig
    first = start(runtime, completion={"text": "Done"})
    assert decide(runtime, first, "rm -rf")["error"]["code"] == "invalid_choice"
    assert not backend.actions
    assert decide(runtime, first)["verified"]


def test_expired_session_cannot_execute(host_rig):
    runtime, backend = host_rig
    first = start(runtime)
    runtime.goal_sessions[first["decision"]["token"]]["expires"] = 0
    assert decide(runtime, first)["error"]["code"] == "goal_session_expired"
    assert not backend.actions


def test_pause_between_decisions_keeps_ledger(host_rig):
    runtime, backend = host_rig
    backend.next_label = "Next"
    first = start(runtime)
    second = decide(runtime, first)
    assert second["status"] == "decision_required" and len(second["steps"]) == 1
    runtime._pause_path().touch()
    stopped = decide(runtime, second)
    assert not stopped["ok"] and stopped["error"]["code"] == "user_takeover"
    assert stopped["steps"][0]["state"] == "executed" and len(backend.actions) == 1
    assert stopped["handoff"]["next"] == "user_authentication"
    assert "observation" not in stopped


def test_host_done_and_blocked_hand_off(host_rig):
    runtime, backend = host_rig
    assert decide(runtime, start(runtime), "DONE")["stop_reason"] == "model_done_unverified"
    assert decide(runtime, start(runtime), "BLOCKED")["stop_reason"] == "model_blocked"
    assert not backend.actions


def test_host_thinking_time_does_not_spend_device_budget(host_rig):
    runtime, backend = host_rig
    first = start(runtime, timeout_seconds=0.3, completion={"text": "Done"})
    time.sleep(0.35)
    assert decide(runtime, first)["verified"] and len(backend.actions) == 1


class Blocked:
    metrics = {"model_calls": 0}

    def decide(self, state, criteria, timeout):
        return {"choice": "BLOCKED", "probability": 1.0, "margin": 1.0}

    def summary(self):
        return self.metrics

    def close(self):
        pass


def test_auto_prefers_enabled_jev(host_rig, monkeypatch):
    runtime, backend = host_rig
    monkeypatch.setenv("ANDROID_USE_JEV_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr("android_use.jev.JevPolicy", lambda config: Blocked())
    result = start(runtime)
    assert result["stop_reason"] == "model_blocked" and "decision" not in result
    assert start(runtime, decider="host")["status"] == "decision_required"
