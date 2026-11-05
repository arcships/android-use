import json
import time
from types import SimpleNamespace

import httpx
import pytest

from android_use.backend import UseError
from android_use.executor import candidates, execute, snapshot
from android_use.jev import JevConfig, JevPolicy
from android_use.models import ExecuteGoalArgs

PACKAGE = "demo.app"
XML = """<hierarchy><node package="demo.app" class="android.widget.Button" text="Show"
enabled="true" clickable="true" bounds="[0,200][300,300]"/></hierarchy>"""


class Policy:
    def __init__(self, decision="a0", callback=None, probability=0.99):
        self.decision, self.callback, self.probability = decision, callback, probability
        self.metrics = {"model_calls": 0}
        self.closed = False
        self.states = []

    def decide(self, state, criteria, timeout):
        self.metrics["model_calls"] += 1
        self.states.append((state, criteria))
        if self.callback:
            self.callback(self.metrics["model_calls"])
        return {"choice": self.decision, "probability": self.probability, "margin": 0.9}

    def summary(self):
        return self.metrics

    def close(self):
        self.closed = True


@pytest.fixture
def goal_rig(rig):
    runtime, backend = rig
    runtime._connect()
    backend.xml = XML
    return runtime, backend


def run(runtime, policy, **kwargs):
    return execute(runtime, {"goal": "Show the target", "package": PACKAGE, **kwargs}, policy)


def test_opt_in_and_config_fail_before_device(monkeypatch):
    from android_use.runtime import Runtime

    def forbidden(*args):
        raise AssertionError("Must not discover device")

    monkeypatch.setattr("android_use.runtime.choose_serial", forbidden)
    monkeypatch.delenv("ANDROID_USE_JEV_ENABLED", raising=False)
    goal = {"goal": "hello", "package": PACKAGE, "decider": "jev"}
    assert Runtime().call("execute_goal", **goal)["error"]["code"] == "jev_disabled"
    monkeypatch.setenv("ANDROID_USE_JEV_ENABLED", "1")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert Runtime().call("execute_goal", **goal)["error"]["code"] == "jev_configuration"


def test_completed_without_model(goal_rig):
    runtime, _ = goal_rig
    policy = Policy()
    result = run(runtime, policy, completion={"text": "Show"})
    assert result["verified"] and result["steps"] == [] and policy.closed
    assert policy.metrics["model_calls"] == 0


def test_done_cannot_prove_completion(goal_rig):
    runtime, backend = goal_rig
    result = run(runtime, Policy("DONE"), completion={"text": "absent"})
    assert not result["verified"] and result["stop_reason"] == "model_done_completion_unmet"
    assert not backend.actions
    result = run(runtime, Policy("DONE"))
    assert result["status"] == "handoff" and not result["verified"]


def test_stale_discarded_and_model_budget(goal_rig):
    runtime, backend = goal_rig

    def change(call):
        backend.xml = XML.replace("Show", "Show" + str(call))

    result = run(runtime, Policy(callback=change), max_model_calls=2)
    assert result["stop_reason"] == "max_model_calls"
    assert result["metrics"]["stale_decisions"] == 2 and not backend.actions


def test_global_xml_index_shift_is_not_stale(goal_rig):
    runtime, backend = goal_rig
    original = snapshot(runtime, PACKAGE)["fingerprint"]
    backend.xml = XML.replace(
        "<hierarchy>",
        '<hierarchy><node package="com.android.systemui" class="FrameLayout" bounds="[0,0][5,5]"/>',
    )
    assert snapshot(runtime, PACKAGE)["fingerprint"] == original
    backend.xml = backend.xml.replace("[0,200][300,300]", "[0,400][300,500]")
    assert snapshot(runtime, PACKAGE)["fingerprint"] != original


def test_low_confidence_returns_safe_handoff_context(goal_rig):
    runtime, _ = goal_rig
    result = run(runtime, Policy(probability=0.1))
    assert result["roles"]["executor"] == "android_use"
    assert result["decisions"][0]["state"] == "handoff"
    assert result["handoff"] == {
        "reason": "low_confidence",
        "completed_actions": 0,
        "uncertain_steps": [],
        "next": "observe_and_replan",
    }
    assert result["observation"]["tree"]["nodes"][0]["text"] == "Show"
    assert "observation" not in run(runtime, Policy("DONE"), observe="none")


@pytest.mark.parametrize("kind", ["pause", "lock", "scope", "password", "overlay", "empty"])
def test_interrupt_after_decision_never_mutates(goal_rig, kind):
    runtime, backend = goal_rig

    def interrupt(call):
        if kind == "pause":
            runtime._pause_path().touch()
        elif kind == "lock":
            backend.is_locked = True
        elif kind == "scope":
            backend.front["package"] = "other.app"
        elif kind == "password":
            backend.xml = XML.replace('text="Show"', 'text="secret" password="true"')
        elif kind == "overlay":
            backend.xml = XML.replace('package="demo.app"', 'package="other.app"')
        else:
            backend.xml = "<hierarchy/>"

    result = run(runtime, Policy(callback=interrupt))
    assert not result["ok"] and not backend.actions and result["steps"] == []
    assert "secret" not in json.dumps(result)
    assert "observation" not in result


def test_low_probability_and_unknown_candidate(goal_rig):
    runtime, backend = goal_rig
    assert run(runtime, Policy(probability=0.5))["stop_reason"] == "low_confidence"
    assert run(runtime, Policy("arbitrary_command"))["error"]["code"] == "jev_invalid_response"
    assert not backend.actions


def test_action_ledger_and_no_replay_after_write_failure(goal_rig):
    runtime, backend = goal_rig
    backend.fail_write = True
    result = run(runtime, Policy())
    assert len(backend.actions) == 1
    assert result["steps"] == [{"op": "tap", "candidate": "a0", "state": "uncertain"}]
    assert result["error"]["uncertain"] and not result["replay_action"]


def test_action_ledger_survives_post_action_read_failure(goal_rig):
    runtime, backend = goal_rig
    original = backend.tap

    def tap(*args):
        original(*args)
        backend.xml = "broken"

    backend.tap = tap
    result = run(runtime, Policy())
    assert result["steps"][0]["state"] == "executed" and len(backend.actions) == 1
    assert not result["verified"]


def test_no_effect_never_repeats_a_mutation(goal_rig):
    runtime, backend = goal_rig
    result = run(runtime, Policy(), effect_timeout_seconds=0.1)
    assert result["stop_reason"] == "uncertain_effect" and len(backend.actions) == 1
    assert result["steps"][0]["state"] == "executed"
    assert result["steps"][0]["effect"]["observed"] is False
    assert result["metrics"]["model_calls"] == 1


def test_wait_local_still_has_bounded_no_progress(goal_rig):
    runtime, backend = goal_rig
    assert run(runtime, Policy(), actions=["wait"], max_steps=2)["stop_reason"] == "max_steps"
    assert run(runtime, Policy(), actions=["wait"])["stop_reason"] == "no_progress"
    assert not backend.actions


def test_deadline_after_slow_model(goal_rig):
    runtime, backend = goal_rig
    result = run(runtime, Policy(callback=lambda _: time.sleep(0.02)), timeout_seconds=0.01)
    assert result["stop_reason"] == "timeout" and not backend.actions


def test_candidates_are_code_owned_and_no_newlines(goal_rig):
    runtime, backend = goal_rig
    backend.xml = XML.replace("android.widget.Button", "android.widget.EditText").replace(
        'enabled="true"', 'enabled="true" focused="true"'
    )
    args = ExecuteGoalArgs(goal="type", package=PACKAGE, texts=["你好", "bad\nsubmit"])
    options = candidates(snapshot(runtime, PACKAGE), args)
    typed = [v for v in options.values() if v["op"] == "type_text"]
    assert len(typed) == 1 and typed[0]["args"]["text"] == "你好"
    assert all(v["op"] not in {"press_button", "long_press"} for v in options.values())
    assert len(options) <= 200


def test_large_context_compacts_before_inference(goal_rig):
    runtime, backend = goal_rig
    backend.xml = (
        "<hierarchy>"
        + (
            XML.replace("Show", "中" * 500).removeprefix("<hierarchy>").removesuffix("</hierarchy>")
            * 100
        )
        + "</hierarchy>"
    )
    policy = Policy("BLOCKED")
    result = run(runtime, policy)
    assert result["stop_reason"] == "model_blocked"
    assert not backend.actions and policy.metrics["model_calls"] == 1
    assert result["decisions"][0]["context"]["request_bytes"] <= 56_000
    assert len(policy.states[0][0]["ui"]) == 100


def test_swipe_direction_is_visible_to_policy(goal_rig):
    runtime, backend = goal_rig
    backend.xml = XML.replace('clickable="true"', 'scrollable="true"')
    policy = Policy("BLOCKED")
    run(runtime, policy)
    criteria = policy.states[0][1]
    swipes = [v for v in criteria.values() if isinstance(v, dict) and v["action"] == "swipe"]
    assert {v["finger_motion"] for v in swipes} == {"up", "down", "left", "right"}


@pytest.mark.parametrize(
    "url",
    [
        "http://untrusted.example/v1",
        "https://u:p@example.com/v1",
        "https://example.com/v1?key=secret",
    ],
)
def test_invalid_provider_url_rejected(monkeypatch, url):
    monkeypatch.setenv("ANDROID_USE_JEV_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("TYPESAFE_BASE_URL", url)
    with pytest.raises(UseError):
        JevConfig.from_env()


def response(criteria, **overrides):
    return {
        "model": "jev-1.14.0",
        "usage": {"input_tokens": 100, "output_tokens": 0},
        "answers": {"action": {"type": "choice", "choice": "a0", "probabilities": criteria}},
        **overrides,
    }


def test_http_contract_usage_and_no_secret_in_config_repr():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-secret"
        payload = json.loads(request.content)
        assert payload["questions"]["action"]["type"] == "choice"
        return httpx.Response(200, json=response({"a0": 0.99, "BLOCKED": 0.01}))

    config = JevConfig("test-secret")
    assert "test-secret" not in repr(config)
    policy = JevPolicy(config, transport=httpx.MockTransport(handler))
    choice = policy.decide({}, {"a0": "tap", "BLOCKED": "blocked"}, 1)
    assert choice["choice"] == "a0" and choice["margin"] == 0.98
    assert policy.summary()["estimated_cost_usd"] == pytest.approx(0.0000042)
    policy.close()


@pytest.mark.parametrize(
    "distribution",
    [
        {"a0": 1},
        {"a0": 0.99, "evil": 0.01},
        {"a0": float("nan"), "BLOCKED": 0.01},
        {"a0": True, "BLOCKED": 0},
        {"a0": 0.9, "BLOCKED": 0.9},
    ],
)
def test_invalid_api_distribution(distribution):
    policy = JevPolicy(
        JevConfig("test"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=json.dumps(response(distribution)))
        ),
    )
    with pytest.raises(UseError, match="validation"):
        policy.decide({}, {"a0": "tap", "BLOCKED": "blocked"}, 1)
    policy.close()


def test_http_error_not_retried_or_reported_free():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, text="secret response")

    policy = JevPolicy(JevConfig("test"), transport=httpx.MockTransport(handler))
    with pytest.raises(UseError) as error:
        policy.decide({}, {"a0": "tap", "BLOCKED": "blocked"}, 1)
    assert "secret response" not in str(error.value) and len(calls) == 1
    assert policy.summary()["estimated_cost_usd"] is None
    policy.close()


@pytest.mark.parametrize("overrides", [{"model": "unknown"}, {"usage": {}}, {"usage": None}])
def test_unknown_or_missing_usage_is_not_free(overrides):
    policy = JevPolicy(
        JevConfig("test"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response({"a0": 0.99, "BLOCKED": 0.01}, **overrides))
        ),
    )
    policy.decide({}, {"a0": "tap", "BLOCKED": "blocked"}, 1)
    assert policy.summary()["estimated_cost_usd"] is None
    assert policy.summary()["unpriced_calls"] == 1
    policy.close()


@pytest.mark.parametrize("delay,budget,success", [(0.3, 0.5, True), (1.5, 0.5, False)])
def test_delayed_effect_waits_without_another_decision(
    goal_rig, monkeypatch, delay, budget, success
):
    runtime, backend = goal_rig
    now = [time.monotonic()]
    began = now[0]
    monkeypatch.setattr(
        "android_use.executor.time",
        SimpleNamespace(
            monotonic=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds)
        ),
    )
    backend.tree = lambda: (
        XML.replace("Show", "Done") if backend.actions and now[0] - began >= delay else XML
    )
    policy = Policy()
    result = run(runtime, policy, effect_timeout_seconds=budget, completion={"text": "Done"})
    assert result["verified"] is success
    assert result["stop_reason"] == ("completion_selector" if success else "uncertain_effect")
    assert len(backend.actions) == 1 and policy.metrics["model_calls"] == 1


def test_transient_empty_tree_after_ack_does_not_replay(goal_rig, monkeypatch):
    runtime, backend = goal_rig
    now = [time.monotonic()]
    began = now[0]
    monkeypatch.setattr(
        "android_use.executor.time",
        SimpleNamespace(
            monotonic=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds)
        ),
    )
    backend.tree = lambda: (
        XML
        if not backend.actions
        else "<hierarchy/>"
        if now[0] - began < 0.2
        else XML.replace("Show", "Done")
    )
    result = run(runtime, Policy(), effect_timeout_seconds=0.5, completion={"text": "Done"})
    assert result["verified"] and len(backend.actions) == 1


def test_geometry_animation_is_not_action_effect(goal_rig):
    runtime, backend = goal_rig
    backend.tree = lambda: (
        XML.replace("[0,200][300,300]", "[0,400][300,500]") if backend.actions else XML
    )
    result = run(runtime, Policy(), effect_timeout_seconds=0.1)
    assert result["stop_reason"] == "uncertain_effect" and len(backend.actions) == 1


def test_return_to_consumed_state_does_not_repeat_action(goal_rig):
    runtime, backend = goal_rig
    backend.tree = lambda: XML.replace("Show", "Back") if len(backend.actions) % 2 else XML
    result = run(runtime, Policy())
    assert result["stop_reason"] == "duplicate_action" and len(backend.actions) == 2


@pytest.mark.parametrize("kind", ["pause", "lock", "password", "scope"])
def test_effect_wait_keeps_authentication_and_scope_guards(goal_rig, kind):
    runtime, backend = goal_rig
    original = backend.tap

    def tap(*args):
        original(*args)
        if kind == "pause":
            runtime._pause_path().touch()
        elif kind == "lock":
            backend.is_locked = True
        elif kind == "password":
            backend.xml = XML.replace('enabled="true"', 'enabled="true" password="true"')
        else:
            backend.front["package"] = "other.app"

    backend.tap = tap
    result = run(runtime, Policy())
    assert not result["ok"] and len(backend.actions) == 1
    assert result["steps"][0]["state"] == "executed" and "observation" not in result
