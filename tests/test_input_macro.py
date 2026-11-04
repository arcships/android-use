import json
import time

import pytest

from android_use.backend import UseError
from android_use.executor import candidates, execute, snapshot
from android_use.models import ExecuteGoalArgs

PACKAGE = "demo.app"
XML = """<hierarchy><node package="demo.app" class="android.widget.EditText"
content-desc="name-field" text="Name" enabled="true" clickable="true" focused="false"
bounds="[30,200][500,300]"/></hierarchy>"""


class MacroPolicy:
    metrics = None
    closed = False

    def __init__(self):
        self.metrics = {"model_calls": 0}

    def decide(self, state, criteria, timeout):
        self.metrics["model_calls"] += 1
        choice = next(
            k
            for k, v in criteria.items()
            if isinstance(v, dict) and v["action"] == "focus_type_text"
        )
        return {"choice": choice, "probability": 0.99, "margin": 0.9}

    def summary(self):
        return self.metrics

    def close(self):
        self.closed = True


@pytest.fixture
def macro_rig(rig):
    runtime, backend = rig
    runtime._connect()
    backend.xml = XML
    original = backend.tap

    def tap(*args):
        original(*args)
        backend.xml = backend.xml.replace('focused="false"', 'focused="true"')

    backend.tap = tap
    return runtime, backend


def run(runtime, **kwargs):
    return execute(
        runtime,
        {
            "goal": "Set the Name field",
            "package": PACKAGE,
            "texts": ["安卓🐻"],
            "max_steps": 1,
            **kwargs,
        },
        MacroPolicy(),
    )


def test_checked_context_reuses_one_info_only_within_phase(rig):
    runtime, backend = rig
    runtime._connect()
    reads = []

    class Device:
        @property
        def info(self):
            reads.append(1)
            return {"screenOn": True, "displayWidth": 1080, "displayHeight": 2400}

    backend.d = Device()
    backend.size = lambda: pytest.fail("No second deviceInfo/window-size read in a phase")
    assert runtime._checked_context()["size"] == [1080, 2400]
    assert len(reads) == 1
    assert runtime._checked_context()["size"] == [1080, 2400]
    assert len(reads) == 2  # No cross-phase cache.


def test_snapshot_detects_rotation_between_phase_samples(rig):
    runtime, backend = rig
    runtime._connect()
    backend.xml = XML
    samples = iter([(1080, 2400), (2400, 1080)])

    class Device:
        @property
        def info(self):
            width, height = next(samples)
            return {"screenOn": True, "displayWidth": width, "displayHeight": height}

    backend.d = Device()
    with pytest.raises(UseError, match="Foreground app or display changed"):
        snapshot(runtime, PACKAGE)
    assert not backend.actions


def test_pause_during_context_read_stops_before_tree(rig):
    runtime, backend = rig
    runtime._connect()

    def front():
        runtime._pause_path().touch()
        return backend.front

    backend.foreground = front
    with pytest.raises(UseError, match="paused"):
        snapshot(runtime, PACKAGE)
    assert backend.tree_reads == 0


def test_focus_type_verifies_unicode_and_preserves_leaf_ledger(macro_rig):
    runtime, backend = macro_rig
    native = []
    original = backend.set_text

    def set_text(text, selector):
        native.append(selector)
        return original(text, selector)

    backend.set_text = set_text
    backend.sanitize_non_bmp = True
    result = run(runtime)
    step = result["steps"][0]
    assert result["metrics"]["model_calls"] == 1 and step["state"] == "executed"
    assert [s["op"] for s in step["substeps"]] == ["tap", "type_text"]
    assert step["substeps"][1]["verified"]
    assert backend.actual_text == "安卓🐻"
    assert len(backend.actions) == 2 and native[0]["description"] == "name-field"
    assert result["handoff"]["completed_actions"] == 2
    assert "安卓🐻" not in json.dumps(
        {"steps": result["steps"], "decisions": result["decisions"]}, ensure_ascii=False
    )


@pytest.mark.parametrize(
    "case",
    [
        "no_identity",
        "duplicate",
        "offscreen_duplicate",
        "disabled",
        "action_missing",
        "opt_out",
        "newline",
    ],
)
def test_macro_candidates_require_stable_unique_allowed_target(macro_rig, case):
    runtime, backend = macro_rig
    args = ExecuteGoalArgs(goal="Set Name", package=PACKAGE, texts=["value"])
    if case == "no_identity":
        backend.xml = XML.replace('content-desc="name-field"', "")
    elif case == "duplicate":
        backend.xml = XML.replace("</hierarchy>", "") + XML.removeprefix("<hierarchy>")
    elif case == "offscreen_duplicate":
        backend.xml = XML.replace("</hierarchy>", "") + XML.removeprefix("<hierarchy>").replace(
            "[30,200][500,300]", "[30,2500][500,2600]"
        )
    elif case == "disabled":
        backend.xml = XML.replace('enabled="true"', 'enabled="false"')
    elif case == "action_missing":
        args.actions = ["tap"]
    elif case == "opt_out":
        args.input_macros = False
    else:
        args.texts = ["bad\nsubmit", "bad\rsubmit"]
    options = candidates(snapshot(runtime, PACKAGE), args)
    assert not any(v["op"] == "focus_type_text" for v in options.values())


@pytest.mark.parametrize(
    "change", ["pause", "lock", "scope", "password", "overlay", "dialog", "move", "focus_lost"]
)
def test_focus_change_aborts_macro_before_text_write(macro_rig, change):
    runtime, backend = macro_rig
    original = backend.tap

    def tap(*args):
        original(*args)
        if change == "pause":
            runtime._pause_path().touch()
        elif change == "lock":
            backend.is_locked = True
        elif change == "scope":
            backend.front["package"] = "other.app"
        elif change == "password":
            backend.xml = backend.xml.replace('text="Name"', 'text="secret" password="true"')
        elif change in {"overlay", "dialog"}:
            package = "other.app" if change == "overlay" else PACKAGE
            backend.xml = backend.xml.replace(
                "</hierarchy>",
                f'<node package="{package}" class="Dialog" clickable="true" bounds="[0,0][200,400]"/></hierarchy>',
            )
        elif change == "move":
            backend.xml = backend.xml.replace("[30,200][500,300]", "[30,400][500,500]")
        else:
            backend.xml = backend.xml.replace('focused="true"', 'focused="false"')

    backend.tap = tap
    result = run(runtime)
    assert not result["ok"] and result["steps"][0]["state"] == "partial"
    assert [a[0] for a in backend.actions] == ["tap"]
    assert result["handoff"]["completed_actions"] == 1
    assert "observation" not in result and not result["replay_action"]


def test_wrong_native_readback_never_replays_write(macro_rig):
    runtime, backend = macro_rig
    backend.get_text = lambda selector: "wrong"
    result = run(runtime)
    assert result["error"]["code"] == "input_verification_failed"
    assert result["steps"][0]["state"] == "uncertain"
    assert result["steps"][0]["substeps"][1]["acknowledged"]
    assert len(backend.actions) == 2 and not result["replay_action"]


def test_deadline_after_focus_does_not_type(macro_rig):
    runtime, backend = macro_rig
    original = backend.tap

    def slow(*args):
        original(*args)
        time.sleep(0.02)

    backend.tap = slow
    result = run(runtime, timeout_seconds=0.01)
    assert result["error"]["code"] == "goal_timeout"
    assert result["steps"][0]["state"] == "partial"
    assert len(backend.actions) == 1


def test_macro_uses_resource_id_before_mutable_description(macro_rig):
    runtime, backend = macro_rig
    backend.xml = XML.replace('content-desc="name-field"', 'resource-id="demo:id/name"')
    native = []
    original = backend.set_text

    def set_text(text, selector):
        native.append(selector)
        return original(text, selector)

    backend.set_text = set_text
    result = run(runtime)
    assert result["steps"][0]["state"] == "executed"
    assert native[0]["resourceId"] == "demo:id/name"


@pytest.mark.parametrize("phase", ["tap", "type"])
def test_macro_transport_uncertainty_records_leaf_and_never_replays(macro_rig, phase):
    runtime, backend = macro_rig
    if phase == "tap":
        backend.fail_write = True
    else:
        original = backend.set_text

        def fail(*args):
            original(*args)
            raise TimeoutError("Write may have executed")

        backend.set_text = fail
    result = run(runtime)
    assert result["steps"][0]["state"] == "uncertain"
    assert result["steps"][0]["substeps"][-1]["state"] == "uncertain"
    assert len(backend.actions) == (1 if phase == "tap" else 2)
    assert not result["replay_action"] and "observation" not in result


def test_pause_after_macro_write_does_not_read_text_or_repeat(macro_rig):
    runtime, backend = macro_rig
    original = backend.set_text

    def pause(*args):
        original(*args)
        runtime._pause_path().touch()

    backend.set_text = pause
    backend.get_text = lambda *_: pytest.fail("Must not read after takeover")
    result = run(runtime)
    assert result["error"]["code"] == "user_takeover"
    assert result["steps"][0]["substeps"][-1]["acknowledged"]
    assert result["steps"][0]["state"] == "uncertain" and len(backend.actions) == 2


def test_text_can_resize_same_bound_field_after_write(macro_rig):
    runtime, backend = macro_rig
    original = backend.set_text

    def resize(*args):
        original(*args)
        backend.xml = backend.xml.replace("[30,200][500,300]", "[30,200][500,320]")

    backend.set_text = resize
    result = run(runtime)
    assert result["steps"][0]["state"] == "executed"
    assert result["steps"][0]["substeps"][1]["verified"]
    assert len(backend.actions) == 2
