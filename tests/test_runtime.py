import base64

from android_use.backend import device_lock
from android_use.runtime import extract_images


def test_ready_and_image_pixel_mapping(rig):
    runtime, backend = rig
    result = runtime.call("ready", screenshot=True)
    assert result["ready"]
    observation = result["observation"]
    assert observation["tree"]["total_nodes"] == 6
    assert observation["image"]["pixel_to_device"] == [1.5, 1.5]
    clean, images = extract_images(result)
    assert len(images) == 1
    assert base64.b64decode(images[0]).startswith(b"\xff\xd8")
    assert "_image_data" not in clean["observation"]


def test_ambiguous_does_not_click_and_includes_picture(rig):
    runtime, backend = rig
    result = runtime.call("tap", selector={"text": "同名"})
    assert result["error"]["code"] == "ambiguous_target"
    assert len(result["error"]["candidates"]) == 2
    assert result["observation"]["_image_data"]
    assert backend.actions == []
    result = runtime.call("tap", selector={"text": "同名", "index": 1})
    assert result["ok"]
    assert backend.actions == [("tap", 100, 650)]


def test_timeout_does_not_replay_and_keeps_uncertainty(rig):
    runtime, backend = rig
    backend.fail_write = True
    result = runtime.call("tap", x=20, y=50)
    assert result["error"]["uncertain"]
    assert result["replay_action"] is False
    assert backend.actions == [("tap", 20, 50)]


def test_batch_validates_all_steps_before_mutating(rig):
    runtime, backend = rig
    result = runtime.call(
        "batch",
        steps=[
            {"op": "tap", "args": {"x": 10, "y": 10}},
            {"op": "tap", "args": {"x": 10}},
        ],
    )
    assert result["error"]["code"] == "invalid_arguments"
    assert not backend.actions


def test_batch_reports_exact_failure_position(rig):
    runtime, backend = rig
    result = runtime.call(
        "batch",
        steps=[
            {"op": "press_button", "args": {"name": "back"}},
            {"op": "tap", "args": {"selector": {"text": "missing"}}},
            {"op": "press_button", "args": {"name": "home"}},
        ],
    )
    assert result["completed_steps"] == 1
    assert result["stopped_at"] == 1
    assert backend.actions == [("press", "back")]
    assert result["results"][1]["observation"]["_image_data"]


def test_batch_timeout_and_enter_stop_remaining_steps(rig):
    runtime, backend = rig
    result = runtime.call(
        "batch",
        steps=[
            {"op": "wait", "args": {"selector": {"text": "missing"}, "timeout_seconds": 0}},
            {"op": "press_button", "args": {"name": "home"}},
        ],
    )
    assert result["stop_reason"] == "wait_timeout"
    assert not backend.actions
    result = runtime.call(
        "batch",
        steps=[
            {"op": "press_button", "args": {"name": "enter"}},
            {"op": "press_button", "args": {"name": "home"}},
        ],
    )
    assert result["stopped_at"] == 1
    assert result["stop_reason"] == "submission_requires_observation"
    assert backend.actions == [("press", "enter")]


def test_locked_and_paused_do_not_capture_or_mutate(rig):
    runtime, backend = rig
    backend.is_locked = True
    assert runtime.call("observe", mode="both")["error"]["code"] == "phone_locked"
    assert (backend.tree_reads, backend.screenshots) == (0, 0)
    backend.is_locked = False
    assert runtime.call("pause")["paused"]
    assert runtime.call("ready")["error"]["code"] == "user_takeover"
    assert (backend.tree_reads, backend.screenshots) == (0, 0)
    assert runtime.call("resume")["paused"] is False
    assert runtime.call("ready")["ready"]


def test_cross_process_device_lock_blocks_actions(rig):
    runtime, backend = rig
    with device_lock("demo"):
        result = runtime.call("tap", x=1, y=1)
    assert result["error"]["code"] == "device_busy"
    assert not backend.actions


def test_usb_and_wireless_share_lock_and_takeover_state(rig):
    from android_use.runtime import Runtime

    runtime, backend = rig
    wireless = Runtime("wireless-endpoint", backend_factory=lambda serial: backend)
    assert runtime.call("pause")["paused"]
    assert wireless.call("observe")["error"]["code"] == "user_takeover"
    assert wireless.call("resume")["ok"]
    with device_lock("demo"):
        assert wireless.call("tap", x=1, y=1)["error"]["code"] == "device_busy"
    assert not backend.actions


def test_stale_app_context_rejected_before_action(rig):
    runtime, backend = rig
    token = runtime.call("observe")["observation_id"]
    backend.front = {"package": "other.app", "activity": "Main"}
    result = runtime.call("tap", x=1, y=1, observation_id=token)
    assert result["error"]["code"] == "stale_observation"
    assert not backend.actions


def test_unicode_input_append_and_exact_readback(rig):
    runtime, backend = rig
    result = runtime.call("type_text", text='中文 emoji 🐻 "引号"', verify=True)
    assert result["input_complete"] and result["verified"]
    result = runtime.call("type_text", text="追加", replace=False, verify=True)
    assert result["verified"]
    assert backend.actions[-1] == ("type", '中文 emoji 🐻 "引号"追加')


def test_xml_sanitized_emoji_does_not_corrupt_append_or_fail_verification(rig):
    runtime, backend = rig
    backend.sanitize_non_bmp = True
    assert runtime.call("type_text", text="中文🐻", verify=True)["verified"]
    assert "🐻" not in backend.xml
    result = runtime.call("type_text", text="追加", replace=False, verify=True)
    assert result["verified"]
    assert result["verification_source"] == "native_getText"
    assert backend.actions[-1] == ("type", "中文🐻追加")


def test_secure_field_redacted_and_refused(rig):
    runtime, backend = rig
    tree = runtime.call("observe")["tree"]
    assert "secret" not in str(tree)
    result = runtime.call("type_text", selector={"password": True}, text="unsafe")
    assert result["error"]["code"] == "invalid_arguments"
    result = runtime.call(
        "type_text", selector={"class_name": "android.widget.EditText", "index": 1}, text="unsafe"
    )
    assert result["error"]["code"] == "no_focused_field"
    assert not backend.actions


def test_invalid_coordinates_and_newlines_do_not_mutate(rig):
    runtime, backend = rig
    assert not runtime.call("tap", x=1080, y=10)["ok"]
    assert not runtime.call("tap", x=1, y=2, selector={"text": "同名"})["ok"]
    assert not runtime.call("type_text", text="a\nb")["ok"]
    assert not backend.actions


def test_finger_direction_and_post_action_observation(rig):
    runtime, backend = rig
    result = runtime.call("swipe", direction="up", observe="tree")
    assert result["observation"]["tree"]["nodes"]
    _, start, end = backend.actions[0]
    assert start[1] > end[1]


def test_failed_identity_lookup_does_not_poison_next_connection(rig):
    runtime, backend = rig
    original = backend.identity
    backend.identity = lambda: (_ for _ in ()).throw(TimeoutError())
    assert runtime.call("ready")["error"]["code"] == "transport_error"
    backend.identity = original
    assert runtime.call("ready")["ready"]
