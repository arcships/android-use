import random
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from android_use.interaction import Variation
from android_use.models import InteractionVariation, TapArgs


def options(**kwargs):
    return InteractionVariation(**kwargs).model_dump()


@pytest.fixture
def variable_rig(rig, monkeypatch):
    runtime, backend = rig
    tree = ET.fromstring(backend.xml)
    for node in list(tree.iter("node")):
        if node.get("password") == "true":
            tree.find("node").remove(node)
    backend.xml = ET.tostring(tree, encoding="unicode")
    clock = SimpleNamespace(now=0.0, callback=None, sleeps=[])

    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds
        if clock.callback:
            clock.callback()

    monkeypatch.setattr(
        "android_use.runtime.time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep)
    )
    return runtime, backend, clock


def test_seed_is_reproducible_without_mutating_global_rng():
    state = random.getstate()

    def sample():
        plan = Variation(options(seed=42))
        delay = plan.delay()
        point = plan.point((50, 50), (0, 0, 100, 100), (100, 100))
        duration = plan.duration(0.4, (0.1, 2))
        return delay, point, duration

    assert sample() == sample()
    assert random.getstate() == state
    assert len({Variation(options(seed=i)).delay() for i in range(20)}) == 20


@pytest.mark.parametrize(
    "rect,size",
    [
        ((0, 0, 1, 1), (100, 100)),
        ((-50, -30, 70, 60), (100, 100)),
        ((95, 95, 200, 200), (100, 100)),
        ((0, 0, 10, 2), (100, 100)),
    ],
)
def test_sampling_never_leaves_visible_rect(rect, size):
    x, y, w, h = rect
    left, top, right, bottom = max(0, x), max(0, y), min(size[0], x + w), min(size[1], y + h)
    point = ((left + right) // 2, (top + bottom) // 2)
    for seed in range(100):
        plan = Variation(options(seed=seed, point_radius=16))
        plan.delay()
        px, py = plan.point(point, rect, size)
        assert left <= px < right and top <= py < bottom
        assert abs(px - point[0]) <= 16 and abs(py - point[1]) <= 16


@pytest.mark.parametrize("direction", ["up", "down", "left", "right"])
def test_swipe_keeps_direction_and_region(direction):
    points = {
        "up": ((10, 16), (10, 4)),
        "down": ((10, 4), (10, 16)),
        "left": ((16, 10), (4, 10)),
        "right": ((4, 10), (16, 10)),
    }
    for seed in range(100):
        plan = Variation(options(seed=seed, point_radius=16))
        plan.delay()
        a, b = plan.swipe(*points[direction], (0, 0, 20, 20), (20, 20), direction)
        assert all(0 <= v < 20 for p in [a, b] for v in p)
        assert {"up": a[1] > b[1], "down": a[1] < b[1], "left": a[0] > b[0], "right": a[0] < b[0]}[
            direction
        ]


@pytest.mark.parametrize(
    "duration,bounds",
    [
        (0.5, (0.5, 3)),
        (3.0, (0.5, 3)),
        (0.04, (0.04, 0.25)),
        (0.25, (0.04, 0.25)),
        (0.1, (0.1, 2)),
        (2.0, (0.1, 2)),
    ],
)
def test_durations_stay_in_schema_limits(duration, bounds):
    for seed in range(20):
        plan = Variation(options(seed=seed, duration_fraction=0.25))
        plan.delay()
        sampled = plan.duration(duration, bounds)
        assert bounds[0] <= sampled <= bounds[1]
        assert 0.75 * duration <= sampled <= 1.25 * duration


@pytest.mark.parametrize(
    "bad",
    [
        {"seed": True},
        {"seed": -1},
        {"point_radius": 17},
        {"delay_max_seconds": 0.51},
        {"duration_fraction": 0.26},
        {"delay_max_seconds": float("nan")},
        {"unknown": 1},
    ],
)
def test_invalid_variation_refused_before_action(rig, bad):
    runtime, backend = rig
    result = runtime.call("tap", x=10, y=20, variation=bad)
    assert result["error"]["code"] == "invalid_arguments" and not backend.actions


def test_default_path_stays_exact(rig):
    runtime, backend = rig
    size = backend.size
    sizes = []

    def count_size():
        sizes.append(True)
        return size()

    backend.size = count_size
    result = runtime.call("tap", selector={"text": "同名", "index": 0})
    assert result["ok"] and "variation" not in result
    assert backend.actions == [("tap", 100, 450)]
    assert backend.tree_reads == 1
    assert len(sizes) <= 3  # No extra device geometry reads on the existing default path.


def test_selector_point_variation_is_repeatable_and_audited(variable_rig):
    runtime, backend, clock = variable_rig
    settings = {"seed": 7}
    first = runtime.call("tap", selector={"text": "同名", "index": 0}, variation=settings)
    second = runtime.call("tap", selector={"text": "同名", "index": 0}, variation=settings)
    assert first["ok"] and first["variation"] == second["variation"]
    assert backend.actions[0] == backend.actions[1]
    assert first["variation"]["point"] == list(backend.actions[0][1:])
    assert all(0 < s <= 0.05 for s in clock.sleeps)
    assert clock.now == pytest.approx(2 * first["variation"]["delay_seconds"])
    assert first["verified"] is False


def test_explicit_coordinates_remain_exact(variable_rig):
    runtime, backend, _ = variable_rig
    result = runtime.call("tap", x=10, y=20, variation={"seed": 1, "point_radius": 16})
    assert result["ok"] and backend.actions == [("tap", 10, 20)]
    assert not result["variation"]["spatial_applied"]


@pytest.mark.parametrize(
    "op,args,parameter,bounds",
    [
        ("long_press", {"x": 10, "y": 20}, "duration", (0.5, 3)),
        ("double_tap", {"x": 10, "y": 20}, "interval", (0.04, 0.25)),
        ("drag", {"start": {"x": 10, "y": 20}, "end": {"x": 30, "y": 40}}, "duration", (0.1, 3)),
        ("swipe", {}, "duration", (0.1, 2)),
    ],
)
def test_timed_gestures_report_applied_parameters(variable_rig, op, args, parameter, bounds):
    runtime, backend, _ = variable_rig
    result = runtime.call(op, **args, variation={"seed": 1})
    assert result["ok"] and len(backend.actions) == 1
    assert bounds[0] <= result["variation"][parameter] <= bounds[1]
    if op == "drag":
        assert backend.actions[0][1:3] == ((10, 20), (30, 40))
        assert not result["variation"]["spatial_applied"]


@pytest.mark.parametrize(
    "change,error",
    [
        ("page", "interaction_context_changed"),
        ("scope", "interaction_context_changed"),
        ("pause", "user_takeover"),
        ("lock", "phone_locked"),
        ("screen", "screen_off"),
        ("rotation", "interaction_context_changed"),
    ],
)
def test_delay_revalidates_and_never_writes_changed_context(variable_rig, change, error):
    runtime, backend, clock = variable_rig

    def mutate():
        if change == "page":
            backend.xml = backend.xml.replace("同名", "changed")
        elif change == "scope":
            backend.front = {"package": "other.app", "activity": "Other"}
        elif change == "pause":
            runtime._pause_path().touch()
        elif change == "lock":
            backend.is_locked = True
        elif change == "screen":
            backend.d.info["screenOn"] = False
        elif change == "rotation":
            backend.size = lambda: (2400, 1080)

    clock.callback = mutate
    result = runtime.call("tap", x=10, y=20, variation={"seed": 1})
    assert result["error"]["code"] == error
    assert result["action_attempted"] is False and not backend.actions
    assert result["replay_action"] is False and backend.screenshots == 0


def test_password_screen_refuses_added_wait(rig, monkeypatch):
    runtime, backend = rig
    monkeypatch.setattr("android_use.runtime.time.sleep", lambda _: pytest.fail("no wait on auth"))
    result = runtime.call("tap", x=10, y=20, variation={"seed": 1})
    assert result["error"]["code"] == "authentication_required" and not backend.actions
    assert backend.screenshots == 0


def test_stale_observation_refused_without_wait(variable_rig):
    runtime, backend, clock = variable_rig
    result = runtime.call("tap", x=10, y=20, observation_id="stale", variation={"seed": 1})
    assert result["error"]["code"] == "stale_observation" and not clock.sleeps
    assert not backend.actions


def test_uncertain_write_is_not_replayed(variable_rig):
    runtime, backend, _ = variable_rig
    backend.fail_write = True
    result = runtime.call("tap", x=10, y=20, variation={"seed": 1})
    assert result["error"]["uncertain"] and result["replay_action"] is False
    assert backend.actions == [("tap", 10, 20)]


def test_batch_prevalidates_and_honors_remaining_budget(variable_rig):
    runtime, backend, clock = variable_rig
    bad = runtime.call(
        "batch",
        steps=[
            {"op": "tap", "args": {"x": 1, "y": 2}},
            {"op": "tap", "args": {"x": 1, "y": 2, "variation": {"point_radius": 50}}},
        ],
    )
    assert bad["error"]["code"] == "invalid_arguments" and not backend.actions
    original = backend.tap

    def slow(*args):
        result = original(*args)
        clock.now += 2.45
        return result

    backend.tap = slow
    result = runtime.call(
        "batch",
        steps=[
            {
                "op": "tap",
                "args": {"x": 1, "y": 2, "variation": {"seed": 0, "delay_max_seconds": 0.5}},
            }
        ]
        * 12,
    )
    assert result["stop_reason"] in {"time_budget", "error"}
    assert len(backend.actions) < 12 and result.get("replay_action", False) is False
    assert runtime._deadline is None


def test_scroll_keeps_one_rng_stream_and_stops_on_progress_limits(variable_rig):
    runtime, backend, _ = variable_rig
    result = runtime.call(
        "scroll_until",
        selector={"text": "absent"},
        variation={"seed": 3},
        max_swipes=5,
        observe="none",
    )
    assert result["stop_reason"] == "no_progress" and result["swipes"] == 2
    assert len(result["variations"]) == 2
    assert result["variations"][0] != result["variations"][1]
    assert len(backend.actions) == 2 and runtime._deadline is None


def test_time_budget_exhaustion_during_validation_prevents_write(variable_rig):
    runtime, backend, clock = variable_rig
    runtime._connect()
    runtime._deadline = 0.01
    from android_use.backend import UseError

    with pytest.raises(UseError, match="remaining time budget"):
        runtime._dispatch("tap", TapArgs(x=1, y=2, variation={"seed": 0}).model_dump())
    assert not backend.actions and not clock.sleeps


def test_visual_and_text_variation_are_not_silently_applied(rig):
    runtime, backend = rig
    assert (
        runtime.call("type_text", text="exact text", variation={})["error"]["code"]
        == "invalid_arguments"
    )
    assert (
        runtime.call("tap", visual={"package": "demo.app", "description": "icon"}, variation={})[
            "error"
        ]["code"]
        == "invalid_arguments"
    )
    assert not backend.actions
