import json
import time

import httpx
import pytest
from pydantic import ValidationError

from android_use.backend import UseError
from android_use.decision_context import action_questions, check_budget, prepare_action_context
from android_use.executor import candidates, execute, snapshot
from android_use.fields import prepare_fields, read_fields
from android_use.jev import JevConfig, JevPolicy
from android_use.models import ExecuteGoalArgs, ReadFieldsArgs

PACKAGE = "demo.app"
XML = """<hierarchy><node package="demo.app" class="android.widget.Button"
text="实付 ¥21.80" enabled="true" clickable="true" bounds="[0,200][300,300]"/></hierarchy>"""


@pytest.fixture
def field_rig(rig):
    runtime, backend = rig
    runtime._connect()
    backend.xml = XML
    return runtime, backend


def answer(options, choice):
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": {key: float(key == choice) for key in options},
    }


def policy_for(pick, callback=None):
    def handler(request):
        body = json.loads(request.content)
        if callback:
            callback(body)
        answers = {
            name: answer(q["criteria"], pick(name, q["criteria"]))
            for name, q in body["questions"].items()
        }
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": answers,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        )

    return JevPolicy(JevConfig("test"), transport=httpx.MockTransport(handler))


def test_structured_operation_and_target_share_one_request():
    criteria = {
        "a0": {"action": "tap", "target": {"node": 12}},
        "a1": {"action": "wait_local", "target": "wait"},
        "DONE": "done",
        "BLOCKED": "blocked",
    }
    calls = []
    policy = policy_for(
        lambda name, opts: "tap" if name == "operation" else next(iter(opts)), calls.append
    )
    result = policy.decide({"goal": "tap"}, criteria, 1)
    assert result["choice"] == "a0" and result["operation"] == "tap"
    assert len(calls) == 1 and set(calls[0]["questions"]) == {
        "operation",
        "target_tap",
        "target_wait_local",
    }
    assert result["heads"]["target"]["choice"] == "a0"
    policy.close()


def test_unused_head_cannot_execute_or_invalidate_selected_action():
    def handler(request):
        questions = json.loads(request.content)["questions"]
        return httpx.Response(
            200,
            json={
                "answers": {
                    "operation": answer(questions["operation"]["criteria"], "tap"),
                    "target_tap": answer({"a0": ""}, "a0"),
                    "target_wait_local": {"choice": "arbitrary_shell_command"},
                }
            },
        )

    policy = JevPolicy(JevConfig("test"), transport=httpx.MockTransport(handler))
    result = policy.decide(
        {},
        {
            "a0": {"action": "tap"},
            "a1": {"action": "wait_local"},
            "DONE": "done",
            "BLOCKED": "blocked",
        },
        1,
    )
    assert result["choice"] == "a0"
    policy.close()


def test_weakest_selected_head_gates_execution(field_rig):
    runtime, backend = field_rig

    def handler(request):
        questions = json.loads(request.content)["questions"]
        operation = questions["operation"]["criteria"]
        return httpx.Response(
            200,
            json={
                "answers": {
                    "operation": {
                        "type": "choice",
                        "choice": "tap",
                        "probabilities": {k: 0.55 if k == "tap" else 0.225 for k in operation},
                    },
                    "target_tap": answer({"a0": ""}, "a0"),
                }
            },
        )

    policy = JevPolicy(JevConfig("test"), transport=httpx.MockTransport(handler))
    result = execute(runtime, {"goal": "tap", "package": PACKAGE, "actions": ["tap"]}, policy)
    assert result["stop_reason"] == "low_confidence" and not backend.actions


def test_changed_full_tree_still_discards_compact_decision(field_rig):
    runtime, backend = field_rig
    policy = policy_for(
        lambda name, opts: "tap" if name == "operation" else next(iter(opts)),
        lambda _: setattr(backend, "xml", XML.replace("¥21.80", "¥22.80")),
    )
    result = execute(
        runtime,
        {"goal": "tap", "package": PACKAGE, "actions": ["tap"], "max_model_calls": 1},
        policy,
    )
    assert result["metrics"]["stale_decisions"] == 1 and not backend.actions


def test_target_after_old_120_node_cutoff_is_described(field_rig):
    runtime, backend = field_rig
    rows = [
        '<node package="demo.app" class="android.view.View" text="row%d" '
        'enabled="true" bounds="[0,200][300,300]"/>' % i
        for i in range(150)
    ]
    backend.xml = "<hierarchy>" + "".join(rows) + XML.removeprefix("<hierarchy>")
    args = ExecuteGoalArgs(goal="tap", package=PACKAGE, actions=["tap"])
    snap = snapshot(runtime, PACKAGE)
    opts = candidates(snap, args)
    state, criteria, metrics = prepare_action_context(snap, args, opts, [])
    node = opts["a0"]["label"]["node"]
    assert any(n["id"] == node and n["text"] == "实付 ¥21.80" for n in state["ui"])
    assert metrics["available_ui_nodes"] == 151
    assert check_budget(state, action_questions(criteria))
    assert all("rect" not in n and "package" not in n for n in state["ui"])


def test_no_duplicate_candidate_labels_in_compact_payload(field_rig):
    runtime, _ = field_rig
    args = ExecuteGoalArgs(goal="tap", package=PACKAGE)
    snap = snapshot(runtime, PACKAGE)
    state, criteria, _ = prepare_action_context(snap, args, candidates(snap, args), [])
    assert "实付 ¥21.80" in json.dumps(state, ensure_ascii=False)
    assert "实付 ¥21.80" not in json.dumps(criteria, ensure_ascii=False)


def test_oversized_exact_inputs_do_not_get_truncated(field_rig):
    runtime, backend = field_rig
    backend.xml = XML.replace("android.widget.Button", "android.widget.EditText").replace(
        'enabled="true"', 'enabled="true" focused="true"'
    )
    args = ExecuteGoalArgs(goal="input", package=PACKAGE, texts=["中" * 2000] * 8)
    snap = snapshot(runtime, PACKAGE)
    with pytest.raises(UseError, match="budget"):
        prepare_action_context(snap, args, candidates(snap, args), [])


def test_money_and_classification_batched_and_exact(field_rig):
    runtime, backend = field_rig
    calls = []
    evidence = "商品 ¥24\n包装费 ¥1.50\n配送费 ¥2.30\n已减 ¥6\n实付 ¥21.80\n买券包最高减9"

    def pick(name, opts):
        return (
            "paid_pack" if name == "coupon" else next(k for k, v in opts.items() if v == "¥21.80")
        )

    policy = policy_for(pick, calls.append)
    result = read_fields(
        runtime,
        {
            "package": PACKAGE,
            "evidence_text": evidence,
            "fields": [
                {"name": "total", "instructions": "Select actual payable total"},
                {
                    "name": "coupon",
                    "kind": "choice",
                    "instructions": "Classify the highest-discount offer",
                    "options": {
                        "paid_pack": "Requires buying a coupon pack",
                        "existing": "Already owned coupon",
                    },
                },
            ],
        },
        policy,
    )
    assert len(calls) == 1 and len(calls[0]["questions"]) == 2
    assert result["fields"]["total"]["value"] == "¥21.80"
    assert result["fields"]["total"]["decimal"] == "21.80"
    assert result["fields"]["coupon"]["value"] == "paid_pack"
    assert result["source"] == "caller_provided_text" and not result["source_verified_on_device"]
    assert not result["verified"] and not backend.actions and result["all_fields_accepted"]


def test_tree_source_and_none_missing(field_rig):
    runtime, backend = field_rig
    policy = policy_for(lambda name, opts: "NONE" if name == "missing" else next(iter(opts)))
    result = read_fields(
        runtime,
        {
            "package": PACKAGE,
            "fields": [
                {"name": "total", "instructions": "Select payable"},
                {"name": "missing", "instructions": "Select delivery fee"},
            ],
        },
        policy,
    )
    assert result["source_verified_on_device"] and result["source"] == "accessibility_tree"
    assert result["fields"]["missing"]["value"] is None
    assert not result["fields"]["missing"]["accepted"] and not result["all_fields_accepted"]
    assert not backend.actions


@pytest.mark.parametrize("cause", ["changed", "password", "pause", "timeout"])
def test_field_judgments_discarded_on_interrupt(field_rig, cause):
    runtime, backend = field_rig

    def interrupt(_):
        if cause == "changed":
            backend.xml = XML.replace("21.80", "22.80")
        elif cause == "password":
            backend.xml = XML.replace('enabled="true"', 'enabled="true" password="true"')
        elif cause == "pause":
            runtime._pause_path().touch()
        else:
            time.sleep(0.02)

    policy = policy_for(lambda name, opts: next(iter(opts)), interrupt)
    with pytest.raises(UseError):
        read_fields(
            runtime,
            {
                "package": PACKAGE,
                "timeout_seconds": 0.01 if cause == "timeout" else 5,
                "fields": [{"name": "total", "instructions": "Read total"}],
            },
            policy,
        )
    assert not backend.actions


def test_money_duplicates_retain_distinct_roles():
    args = ReadFieldsArgs(package=PACKAGE, fields=[{"name": "fee", "instructions": "delivery"}])
    state, questions, sources = prepare_fields(["配送费 ¥2.00", "包装费 ¥2.00"], args)
    assert len(sources["fee"]) == 2
    assert sources["fee"]["v0"]["line"] != sources["fee"]["v1"]["line"]
    assert state["evidence"][0] == "配送费 ¥2.00"
    assert "NONE" in questions["fee"]["criteria"]


@pytest.mark.parametrize(
    "fields",
    [
        [{"name": "x", "instructions": "x"}, {"name": "x", "instructions": "y"}],
        [{"name": "x", "instructions": "x", "kind": "choice"}],
        [{"name": "x", "instructions": "x", "options": {"a": "a"}}],
        [{"name": "x", "instructions": "x", "kind": "choice", "options": {"NONE": "a"}}],
    ],
)
def test_invalid_fields_rejected(fields):
    with pytest.raises(ValidationError):
        ReadFieldsArgs(package=PACKAGE, fields=fields)


def test_runtime_read_fields_disabled_before_device(monkeypatch):
    from android_use.runtime import Runtime

    monkeypatch.setenv("ANDROID_USE_JEV_ENABLED", "0")
    monkeypatch.setattr("android_use.runtime.choose_serial", lambda _: pytest.fail("read device"))
    result = Runtime().call(
        "read_fields", package=PACKAGE, fields=[{"name": "total", "instructions": "Read total"}]
    )
    assert result["error"]["code"] == "jev_disabled"


def test_nested_wrapper_not_an_action_but_child_remains(field_rig):
    runtime, backend = field_rig
    backend.xml = """<hierarchy><node package="demo.app" class="android.view.ViewGroup"
    enabled="true" clickable="true" bounds="[0,200][1000,400]">
    <node package="demo.app" class="android.widget.Button" text="加入购物车"
    enabled="true" clickable="true" bounds="[700,200][1000,400]"/></node></hierarchy>"""
    args = ExecuteGoalArgs(goal="tap", package=PACKAGE, actions=["tap"])
    opts = candidates(snapshot(runtime, PACKAGE), args)
    assert len(opts) == 1 and opts["a0"]["args"] == {"x": 850, "y": 300}


def test_empty_action_space_hands_off_without_context_error(field_rig):
    from test_executor import Policy

    runtime, _ = field_rig
    result = execute(
        runtime, {"goal": "type", "package": PACKAGE, "actions": ["type_text"]}, Policy("BLOCKED")
    )
    assert result["stop_reason"] == "model_blocked"


def test_visual_rows_keep_fee_and_split_currency_together():
    from android_use.fields import tree_lines

    def node(text, x, y):
        return {"text": text, "description": "", "rect": [x, y, 20, 20]}

    lines = tree_lines(
        [
            node("¥5.4", 300, 200),
            node("2.4", 500, 200),
            node("用户配送费", 10, 200),
            node("¥", 480, 200),
        ]
    )
    assert lines == ["用户配送费 ¥5.4 ¥ 2.4"]


@pytest.mark.parametrize("delivery,passed", [("¥2.40", True), ("¥5.40", False)])
def test_decimal_sum_check_catches_high_confidence_wrong_fee(field_rig, delivery, passed):
    runtime, _ = field_rig
    spans = {"total": "¥22.90", "items": "¥19", "packing": "¥1.50", "delivery": delivery}
    policy = policy_for(
        lambda name, opts: next(k for k, value in opts.items() if value == spans[name])
    )
    result = read_fields(
        runtime,
        {
            "package": PACKAGE,
            "evidence_text": "商品 ¥19\n包装 ¥1.50\n旧配送 ¥5.40\n实际配送 ¥2.40\n实付 ¥22.90",
            "fields": [{"name": n, "instructions": n} for n in spans],
            "sum_check": {"total": "total", "add": ["items", "packing", "delivery"]},
        },
        policy,
    )
    assert result["checks"][0]["passed"] == passed
    assert result["all_fields_accepted"] == passed
    assert not result["verified"]
    if not passed:
        assert all(not f["accepted"] for f in result["fields"].values())
        assert result["fields"]["delivery"]["value"] == "¥5.40"


def test_unknown_field_value_cannot_be_generated(field_rig):
    runtime, backend = field_rig
    policy = policy_for(lambda name, opts: "invented_9.99")
    with pytest.raises(UseError, match="validation"):
        read_fields(
            runtime,
            {"package": PACKAGE, "fields": [{"name": "total", "instructions": "total"}]},
            policy,
        )
    assert not backend.actions


def test_sum_check_references_must_be_distinct_money_fields():
    with pytest.raises(ValidationError):
        ReadFieldsArgs(
            package=PACKAGE,
            fields=[{"name": "total", "instructions": "total"}],
            sum_check={"total": "total", "add": ["total"]},
        )


@pytest.mark.parametrize(
    "printed,decimal", [("-¥6", "-6"), ("¥-6", "-6"), ("−￥6", "-6"), ("¥ 1.50", "1.50")]
)
def test_signed_money_is_copied_and_parsed_without_losing_discount(field_rig, printed, decimal):
    runtime, _ = field_rig
    policy = policy_for(lambda name, opts: next(k for k, v in opts.items() if v == printed))
    result = read_fields(
        runtime,
        {
            "package": PACKAGE,
            "evidence_text": "优惠 " + printed,
            "fields": [{"name": "discount", "instructions": "discount"}],
        },
        policy,
    )
    assert result["fields"]["discount"]["value"] == printed
    assert result["fields"]["discount"]["decimal"] == decimal


def test_field_region_ignores_only_changes_outside_evidence(field_rig):
    runtime, backend = field_rig
    extra = '<node package="demo.app" class="TextView" text="Banner" bounds="[0,0][200,100]"/>'
    backend.xml = XML.replace("</hierarchy>", extra + "</hierarchy>")
    policy = policy_for(
        lambda name, opts: next(iter(opts)),
        lambda _: setattr(backend, "xml", backend.xml.replace("Banner", "News")),
    )
    result = read_fields(
        runtime,
        {
            "package": PACKAGE,
            "region": {"x": 0, "y": 200, "width": 500, "height": 500},
            "fields": [{"name": "total", "instructions": "total"}],
        },
        policy,
    )
    assert result["fields"]["total"]["accepted"]
    assert not result["verified"]


def test_invalid_field_region_rejected_before_inference(field_rig):
    runtime, _ = field_rig
    policy = policy_for(lambda name, opts: next(iter(opts)))
    with pytest.raises(UseError) as exc:
        read_fields(
            runtime,
            {
                "package": PACKAGE,
                "region": {"x": 1000, "y": 200, "width": 500, "height": 500},
                "fields": [{"name": "total", "instructions": "total"}],
            },
            policy,
        )
    assert exc.value.code == "invalid_region"
    assert policy.metrics["model_calls"] == 0


def test_read_only_layout_animation_does_not_invalidate_same_evidence(field_rig):
    runtime, backend = field_rig
    extra = '<node package="demo.app" class="View" bounds="[0,0][200,100]"/>'
    backend.xml = XML.replace("</hierarchy>", extra + "</hierarchy>")
    policy = policy_for(
        lambda name, opts: next(iter(opts)),
        lambda _: setattr(backend, "xml", backend.xml.replace("[0,0][200,100]", "[0,0][200,101]")),
    )
    result = read_fields(
        runtime,
        {"package": PACKAGE, "fields": [{"name": "total", "instructions": "total"}]},
        policy,
    )
    assert result["fields"]["total"]["accepted"]


def test_stale_field_error_keeps_model_telemetry(field_rig):
    runtime, backend = field_rig
    policy = policy_for(
        lambda name, opts: next(iter(opts)),
        lambda _: setattr(backend, "xml", XML.replace("21.80", "22.80")),
    )
    with pytest.raises(UseError) as exc:
        read_fields(
            runtime,
            {"package": PACKAGE, "fields": [{"name": "total", "instructions": "total"}]},
            policy,
        )
    assert exc.value.details["metrics"]["model_calls"] == 1
    assert exc.value.code == "stale_field_evidence"
