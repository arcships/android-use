from types import SimpleNamespace

import pytest

from android_use.backend import Backend


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr("android_use.runtime.time", clock)
    return clock


def page(text="row", y=800):
    return (
        '<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" '
        'enabled="true"><node class="android.widget.TextView" enabled="true" '
        f'text="{text}" bounds="[100,{y}][900,{y + 100}]"/></node></hierarchy>'
    )


def test_action_waits_once_and_reuses_last_tree(rig, clock):
    runtime, backend = rig
    original = backend.tree

    def tree():
        backend.xml = page("arrived" if clock.now >= 0.4 else "loading")
        return original()

    backend.tree = tree
    result = runtime.call(
        "tap",
        x=20,
        y=30,
        observe="tree",
        wait_for={"selector": {"text": "arrived"}, "timeout_seconds": 1},
    )
    assert result["ok"] and result["wait"]["condition_met"]
    assert backend.actions == [("tap", 20, 30)]
    assert backend.tree_reads == 3  # No extra XML dump for the returned observation.
    assert result["observation"]["tree"]["nodes"][-1]["text"] == "arrived"


def test_gone_wait_uses_visibility_and_does_not_trust_empty_tree(rig, clock):
    runtime, backend = rig
    backend.xml = page("target", y=2500)
    assert not runtime.call("wait", selector={"text": "target"}, timeout_seconds=0)["condition_met"]
    assert runtime.call("wait", selector={"text": "target"}, state="gone")["condition_met"]
    backend.xml = "<hierarchy/>"
    result = runtime.call("wait", selector={"text": "target"}, state="gone", timeout_seconds=0)
    assert result["timed_out"] and not result["condition_met"]


def test_action_wait_timeout_never_replays_and_batch_stops(rig, clock):
    runtime, backend = rig
    result = runtime.call(
        "batch",
        steps=[
            {
                "op": "tap",
                "args": {
                    "x": 5,
                    "y": 6,
                    "observe": "tree",
                    "wait_for": {
                        "selector": {"text": "missing"},
                        "timeout_seconds": 0.3,
                    },
                },
            },
            {"op": "press_button", "args": {"name": "home"}},
        ],
    )
    assert not result["ok"] and result["stop_reason"] == "postcondition_timeout"
    assert result["completed_steps"] == result["stopped_at"] == 1
    assert result["replay_action"] is False
    action = result["results"][0]
    assert action["action_executed"] and action["wait"]["timed_out"]
    assert action["error"]["code"] == "postcondition_timeout"
    assert backend.actions == [("tap", 5, 6)]


@pytest.mark.parametrize(
    ("op", "args"),
    [
        ("long_press", {"selector": {"text": "同名", "index": 1}, "duration": 0.8}),
        ("double_tap", {"x": 30, "y": 40, "interval": 0.08}),
        ("drag", {"start": {"x": 20, "y": 30}, "end": {"x": 100, "y": 200}}),
        ("swipe", {}),
        ("type_text", {"text": "中文🐻", "verify": True}),
        ("press_button", {"name": "back"}),
        ("launch_app", {"package": "demo.app"}),
    ],
)
def test_all_actions_support_postcondition(op, args, rig, clock):
    runtime, backend = rig
    result = runtime.call(op, **args, wait_for={"selector": {"text": "同名"}}, observe="tree")
    assert result["ok"] and result["wait"]["condition_met"]
    assert len(backend.actions) == 1


@pytest.mark.parametrize(
    ("op", "args"),
    [
        ("long_press", {"x": 20, "y": 30, "duration": 0.1}),
        ("double_tap", {"x": 20, "y": 30, "interval": 1.0}),
        ("drag", {"start": {"x": 20, "y": 30}, "end": {"x": 1080, "y": 200}}),
        ("long_press", {"x": 20, "y": 30, "selector": {"text": "同名"}}),
        ("tap", {"x": 20, "y": 30, "wait_for": {"selector": {}, "timeout_seconds": 30}}),
        (
            "scroll_until",
            {
                "selector": {"text": "x"},
                "region": {
                    "x": 1000,
                    "y": 200,
                    "width": 500,
                    "height": 500,
                },
            },
        ),
    ],
)
def test_invalid_new_arguments_do_not_mutate(op, args, rig):
    runtime, backend = rig
    assert runtime.call(op, **args)["error"]["code"] == "invalid_arguments"
    assert not backend.actions


def test_new_batch_schemas_validated_before_any_action(rig):
    runtime, backend = rig
    result = runtime.call(
        "batch",
        steps=[
            {"op": "long_press", "args": {"x": 10, "y": 20}},
            {"op": "drag", "args": {"start": {"x": 10, "y": 20}, "end": {"x": -1, "y": 20}}},
        ],
    )
    assert result["error"]["code"] == "invalid_arguments"
    assert not backend.actions


def test_scroll_returns_visible_target_without_clicking_or_swiping(rig, clock):
    runtime, backend = rig
    result = runtime.call("scroll_until", selector={"text": "同名"})
    assert result["found"] and len(result["matches"]) == 2
    assert result["swipes"] == 0 and result["stop_reason"] == "found"
    assert backend.tree_reads == 1 and not backend.actions


def test_scroll_ignores_offscreen_match_and_finds_after_two_swipes(rig, clock):
    runtime, backend = rig
    backend.xml = page("target", 2500)
    original = backend.swipe

    def swipe(*args):
        original(*args)
        backend.xml = page("target" if len(backend.actions) == 2 else "row2")

    backend.swipe = swipe
    result = runtime.call("scroll_until", selector={"text": "target"})
    assert result["found"] and result["swipes"] == 2
    assert backend.tree_reads == 3
    assert all(action[0] == "swipe" for action in backend.actions)


@pytest.mark.parametrize(
    ("options", "reason", "swipes"),
    [
        ({}, "no_progress", 2),
        ({"max_swipes": 1}, "max_swipes", 1),
        ({"max_swipes": 0}, "max_swipes", 0),
        ({"timeout_seconds": 0}, "timeout", 0),
        ({"timeout_seconds": 0.2}, "timeout", 1),
    ],
)
def test_scroll_bounds(options, reason, swipes, rig, clock):
    runtime, backend = rig
    result = runtime.call("scroll_until", selector={"text": "missing"}, **options)
    assert result["ok"] and not result["found"]
    assert (result["stop_reason"], result["swipes"]) == (reason, swipes)
    assert len(backend.actions) == swipes


def test_scroll_empty_tree_does_not_swipe(rig, clock):
    runtime, backend = rig
    backend.xml = "<hierarchy/>"
    assert runtime.call("scroll_until", selector={"text": "missing"})["stop_reason"] == "empty_tree"
    assert not backend.actions


def test_scroll_miss_stops_batch_before_next_action(rig, clock):
    runtime, backend = rig
    result = runtime.call(
        "batch",
        steps=[
            {"op": "scroll_until", "args": {"selector": {"text": "missing"}, "max_swipes": 0}},
            {"op": "press_button", "args": {"name": "home"}},
        ],
    )
    assert result["stop_reason"] == "scroll_not_found" and not result["ok"]
    assert result["stopped_at"] == 1 and not backend.actions


@pytest.mark.parametrize("change", ["app", "locked", "paused", "timeout"])
def test_scroll_interrupts_without_replaying(change, rig, clock):
    runtime, backend = rig
    original = backend.swipe

    def swipe(*args):
        original(*args)
        if change == "app":
            backend.front = {"package": "other.app", "activity": "Main"}
        elif change == "locked":
            backend.is_locked = True
        elif change == "paused":
            runtime._pause_path().touch()
        else:
            raise TimeoutError()

    backend.swipe = swipe
    result = runtime.call("scroll_until", selector={"text": "missing"})
    assert not result["ok"] and result["error"]["uncertain"]
    assert result["replay_action"] is False and len(backend.actions) == 1
    assert backend.screenshots == 0


def test_wait_can_be_cancelled_by_pause_marker(rig, clock):
    runtime, backend = rig

    def sleep(seconds):
        clock.now += seconds
        runtime._pause_path().touch()

    clock.sleep = sleep
    result = runtime.call("tap", x=10, y=20, wait_for={"selector": {"text": "missing"}})
    assert result["error"]["code"] == "user_takeover"
    assert backend.tree_reads == 1 and backend.screenshots == 0


def test_pause_bypasses_busy_runtime_and_device_locks(rig):
    from android_use.backend import device_lock

    runtime, backend = rig
    runtime._connect()
    runtime.mutation_attempted = True
    with device_lock("demo"), runtime._mutex:
        result = runtime.call("pause")
    assert result["paused"] and runtime._pause_path().exists()
    assert runtime.mutation_attempted is True
    assert not backend.actions and backend.tree_reads == backend.screenshots == 0


@pytest.mark.parametrize("operation", ["wait", "observe", "find"])
def test_pause_during_tree_read_discards_late_observation(operation, rig, clock):
    runtime, backend = rig
    original = backend.tree

    def tree():
        runtime._pause_path().touch()
        return original()

    backend.tree = tree
    args = {"mode": "both"} if operation == "observe" else {"selector": {"text": "同名"}}
    result = runtime.call(operation, **args)
    assert result["error"]["code"] == "user_takeover"
    assert "tree" not in result and "matches" not in result and "observation" not in result
    assert backend.screenshots == 0


def test_scroll_region_fingerprint_ignores_outside_clock(rig, clock):
    runtime, backend = rig
    original = backend.tree

    def tree():
        backend.xml = page("stable")[:-12] + (
            f'<node class="Clock" text="{backend.tree_reads}" bounds="[0,0][100,100]"/></hierarchy>'
        )
        return original()

    backend.tree = tree
    result = runtime.call("scroll_until", selector={"text": "missing"})
    assert result["stop_reason"] == "no_progress" and result["swipes"] == 2


def test_degenerate_gestures_do_not_mutate(rig):
    runtime, backend = rig
    assert not runtime.call("drag", start={"x": 10, "y": 20}, end={"x": 10, "y": 20})["ok"]
    assert not runtime.call("swipe", region={"x": 10, "y": 20, "width": 1, "height": 1})["ok"]
    assert not backend.actions


def test_batch_time_budget_caps_nested_wait_and_is_reset(rig, clock):
    runtime, backend = rig

    def press(name):
        backend.actions.append(("press", name))
        clock.now += 24.9

    backend.press = press
    result = runtime.call(
        "batch",
        steps=[
            {
                "op": "press_button",
                "args": {
                    "name": "back",
                    "wait_for": {
                        "selector": {"text": "missing"},
                        "timeout_seconds": 20,
                    },
                },
            },
            {"op": "tap", "args": {"x": 20, "y": 30}},
        ],
    )
    assert not result["ok"] and clock.now <= 25.001
    assert len(backend.actions) == 1 and runtime._deadline is None


def test_backend_double_tap_does_not_repeat_partial_pair(monkeypatch):
    calls = []

    def click(*args):
        calls.append(args)
        if len(calls) == 2:
            raise TimeoutError()
        return True

    backend = Backend("demo")
    backend._device = SimpleNamespace(jsonrpc=SimpleNamespace(click=click))
    monkeypatch.setattr("android_use.backend.time.sleep", lambda _: None)
    with pytest.raises(TimeoutError):
        backend.double_tap(10, 20, 0.08)
    assert calls == [(10, 20), (10, 20)]


def test_backend_gesture_rpc_units_and_false_first_click(monkeypatch):
    calls = []

    def click(*args):
        calls.append(("click", args))
        return False

    backend = Backend("demo")
    backend._device = SimpleNamespace(
        jsonrpc=SimpleNamespace(
            click=click,
            drag=lambda *args: calls.append(("drag", args)),
        )
    )
    assert backend.double_tap(10, 20, 0.08) is False
    backend.long_press(10, 20, 0.8)
    backend.drag((10, 20), (100, 200), 0.6)
    assert calls == [
        ("click", (10, 20)),
        ("click", (10, 20, 800)),
        ("drag", (10, 20, 100, 200, 120)),
    ]
