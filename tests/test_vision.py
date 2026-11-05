import base64
import io
import json

import httpx
import pytest
from PIL import Image, ImageDraw

from android_use.backend import UseError
from android_use.jev import JevConfig, JevPolicy
from android_use.vision import fit_image, locate

PACKAGE = "demo.app"
XML = """<hierarchy><node package="demo.app" class="android.view.View"
enabled="true" bounds="[0,0][1080,2400]"/></hierarchy>"""
BOX = {"left": 0.8, "top": 0.1, "right": 0.9, "bottom": 0.15}


def vision_policy(
    presence=0.95, count="one", box=BOX, confidence=0.9, callback=None, images=True, then=None
):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append((request.headers, body))
        if callback:
            callback(body)
        # `then` overrides answers for the zoomed re-ask (second request).
        a = {"presence": presence, "count": count, "box": box, "confidence": confidence}
        if then and len(requests) > 1:
            a.update(then)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": {
                    "present": {"type": "noul", "noul": a["presence"]},
                    "count": {
                        "type": "choice",
                        "choice": a["count"],
                        "probabilities": {
                            k: float(k == a["count"]) for k in ("none", "one", "several")
                        },
                    },
                    "box": {"type": "box", "box": a["box"], "confidence": a["confidence"]},
                },
                "usage": {"input_tokens": 1500, "output_tokens": 20},
            },
        )

    policy = JevPolicy(JevConfig("test", images=images), transport=httpx.MockTransport(handler))
    policy.requests = requests
    return policy


@pytest.fixture
def screen(rig):
    runtime, backend = rig
    runtime._connect()
    backend.xml = XML
    image = Image.new("RGB", backend.size(), "white")
    ImageDraw.Draw(image).rectangle((864, 240, 972, 360), fill="red")
    backend.screenshot = lambda: image.copy()
    return runtime, backend, image


def request(**extra):
    return {"package": PACKAGE, "description": "red cart icon", **extra}


def test_locate_sends_one_scaled_image_and_maps_box_to_display(screen):
    runtime, backend, _ = screen
    policy = vision_policy()
    result = locate(runtime, request(), policy)
    assert result["status"] == "located" and result["found"]
    assert result["box"] == [864, 240, 108, 120] and result["tap"] == [918, 300]
    assert not backend.actions and not result["verified"] and not result["action_attempted"]
    headers, body = policy.requests[0]
    assert headers["TypeSafe-Danger-Zone-Images"] == "true"
    ref = body["state"]["screen"]
    assert set(body["assets"]) == {ref} and body["state"]["target"] == "red cart icon"
    assert set(body["questions"]) == {"present", "count", "box"}
    sent = Image.open(io.BytesIO(base64.b64decode(body["assets"][ref]["data_base64"])))
    assert sent.size == (706, 1568) and result["frame"]["sent_size"] == [706, 1568]
    assert ref not in json.dumps(body["questions"])  # Image appears once, via state.
    assert result["metrics"]["model_calls"] == 1 and result["metrics"]["input_tokens"] == 1500


def test_region_offsets_box(screen):
    runtime, _, _ = screen
    region = {"x": 540, "y": 0, "width": 540, "height": 1200}
    result = locate(runtime, request(region=region), vision_policy())
    assert result["box"] == [972, 120, 54, 60]


@pytest.mark.parametrize(
    "answer,status",
    [
        ({"presence": 0.2}, "not_found"),
        ({"count": "none"}, "not_found"),
        ({"count": "several"}, "ambiguous"),
        ({"confidence": 0.3}, "low_confidence"),
    ],
)
def test_unconfirmed_boxes_have_no_tap_point(screen, answer, status):
    runtime, _, _ = screen
    result = locate(runtime, request(), vision_policy(**answer))
    assert result["status"] == status and not result["found"] and result["tap"] is None


def test_uncertain_answer_is_reasked_on_native_crop(screen):
    runtime, _, _ = screen
    # The crop is 560x560 around the first box; the target fills its middle quarter.
    zoomed = {"presence": 0.93, "box": {"left": 0.4, "top": 0.4, "right": 0.6, "bottom": 0.6}}
    policy = vision_policy(presence=0.55, then=zoomed)
    result = locate(runtime, request(), policy)
    assert result["status"] == "located" and result["metrics"]["model_calls"] == 2
    window = result["refined"]["window"]
    assert window == [520, 20, 560, 560] and result["refined"]["first_presence"] == 0.55
    assert result["refined"]["sent_size"] == [560, 560]
    assert result["box"] == [744, 244, 112, 112] and result["tap"] == [800, 300]
    crop_body = policy.requests[1][1]
    sent = Image.open(
        io.BytesIO(
            base64.b64decode(crop_body["assets"][crop_body["state"]["screen"]]["data_base64"])
        )
    )
    assert sent.size == (560, 560)


@pytest.mark.parametrize("first", [{"presence": 0.1}, {"presence": 0.5, "count": "several"}])
def test_absent_or_ambiguous_is_not_reasked(screen, first):
    runtime, _, _ = screen
    policy = vision_policy(**first)
    result = locate(runtime, request(), policy)
    assert result["metrics"]["model_calls"] == 1 and result["refined"] is None


def test_reask_can_still_reject(screen):
    runtime, _, _ = screen
    result = locate(runtime, request(), vision_policy(presence=0.55, then={"presence": 0.4}))
    assert result["status"] == "not_found" and result["tap"] is None
    assert result["metrics"]["model_calls"] == 2


def test_full_resolution_region_is_not_reasked(screen):
    runtime, _, _ = screen
    region = {"x": 540, "y": 0, "width": 540, "height": 600}
    result = locate(runtime, request(region=region), vision_policy(presence=0.55))
    assert result["metrics"]["model_calls"] == 1 and result["status"] == "not_found"


def test_mask_is_blanked_and_masked_center_is_not_tappable(screen):
    runtime, _, _ = screen
    policy = vision_policy()
    mask = {"x": 900, "y": 280, "width": 40, "height": 40}
    result = locate(runtime, request(masks=[mask]), policy)
    assert result["status"] == "masked" and result["tap"] is None
    body = policy.requests[0][1]
    sent = Image.open(
        io.BytesIO(base64.b64decode(body["assets"][body["state"]["screen"]]["data_base64"]))
    )
    scale = sent.size[0] / 1080
    pixel = sent.getpixel((round(920 * scale), round(300 * scale)))
    assert min(pixel) > 230  # White, not the red target.


def test_images_toggle_is_enforced_in_transport(screen):
    runtime, backend, _ = screen
    policy = vision_policy(images=False)
    with pytest.raises(UseError) as exc:
        locate(runtime, request(), policy)
    assert exc.value.code == "jev_images_disabled" and not policy.requests


@pytest.mark.parametrize(
    "xml,code",
    [
        (
            '<hierarchy><node package="demo.app" class="android.widget.EditText" password="true" '
            'enabled="true" bounds="[0,0][500,100]"/></hierarchy>',
            "authentication_required",
        ),
        (
            '<hierarchy><node package="demo.app" class="android.widget.TextView" text="请输入验证码" '
            'enabled="true" bounds="[0,0][500,100]"/></hierarchy>',
            "authentication_required",
        ),
    ],
)
def test_auth_screens_never_leave_the_device(screen, xml, code):
    runtime, backend, _ = screen
    backend.xml = xml
    policy = vision_policy()
    with pytest.raises(UseError) as exc:
        locate(runtime, request(), policy)
    assert exc.value.code == code and not policy.requests


def test_paused_device_is_not_captured(screen):
    runtime, backend, _ = screen
    runtime._pause_path().touch()
    policy = vision_policy()
    with pytest.raises(UseError) as exc:
        locate(runtime, request(), policy)
    assert exc.value.code == "user_takeover" and not policy.requests


def test_secure_black_screen_is_not_sent(screen):
    runtime, backend, _ = screen
    backend.screenshot = lambda: Image.new("RGB", backend.size())
    policy = vision_policy()
    with pytest.raises(UseError) as exc:
        locate(runtime, request(), policy)
    assert exc.value.code == "screen_capture_blocked" and not policy.requests


@pytest.mark.parametrize(
    "box",
    [
        {"left": 0.5, "top": 0.1, "right": 0.4, "bottom": 0.2},
        {"left": 0.1, "top": 0.1, "right": 1.2, "bottom": 0.2},
        {"left": 0.1, "top": 0.1, "right": 0.2},
    ],
)
def test_invalid_box_fails_validation(screen, box):
    runtime, _, _ = screen
    with pytest.raises(UseError) as exc:
        locate(runtime, request(), vision_policy(box=box))
    assert exc.value.code == "jev_invalid_response"


@pytest.mark.parametrize(
    "size,expected",
    [((1080, 2400), (706, 1568)), ((200, 200), (280, 280)), ((1000, 400), (1000, 400))],
)
def test_fit_image_bounds(size, expected):
    assert fit_image(Image.new("RGB", size)).size == expected


def test_too_narrow_region_is_rejected():
    with pytest.raises(UseError) as exc:
        fit_image(Image.new("RGB", (1080, 50)))
    assert exc.value.code == "invalid_region"


def visual_args(**extra):
    return {"visual": request(), **extra}


def jev_images(monkeypatch, policy):
    monkeypatch.setenv("ANDROID_USE_JEV_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("ANDROID_USE_JEV_IMAGES", "1")
    monkeypatch.setattr("android_use.jev.JevPolicy", lambda config: policy)


def test_visual_tap_taps_box_center_once(screen, monkeypatch):
    runtime, backend, _ = screen
    jev_images(monkeypatch, vision_policy())
    result = runtime.call("tap", **visual_args())
    assert result["ok"] and backend.actions == [("tap", 918, 300)]
    assert result["visual"]["pixel_change_ratio"] == 0
    assert result["visual"]["metrics"]["model_calls"] == 1


def test_visual_tap_refuses_when_target_pixels_change(screen, monkeypatch):
    runtime, backend, image = screen

    def move(_):
        ImageDraw.Draw(image).rectangle((864, 240, 972, 360), fill="white")
        ImageDraw.Draw(image).rectangle((100, 240, 208, 360), fill="red")

    jev_images(monkeypatch, vision_policy(callback=move))
    result = runtime.call("tap", **visual_args())
    assert result["error"]["code"] == "stale_visual_target"
    assert not backend.actions and result["replay_action"] is False


def test_visual_tap_ignores_changes_outside_padded_box(screen, monkeypatch):
    runtime, backend, image = screen
    clock = lambda _: ImageDraw.Draw(image).rectangle((0, 0, 200, 60), fill="black")  # noqa: E731
    jev_images(monkeypatch, vision_policy(callback=clock))
    assert runtime.call("tap", **visual_args())["ok"]
    assert backend.actions == [("tap", 918, 300)]


@pytest.mark.parametrize(
    "answer,code",
    [
        ({"presence": 0.1}, "visual_target_not_found"),
        ({"count": "several"}, "ambiguous_target"),
        ({"confidence": 0.1}, "visual_low_confidence"),
    ],
)
def test_visual_tap_without_confirmed_target_does_nothing(screen, monkeypatch, answer, code):
    runtime, backend, _ = screen
    jev_images(monkeypatch, vision_policy(**answer))
    result = runtime.call("tap", **visual_args())
    assert result["error"]["code"] == code and not backend.actions
    assert result["error"]["locate"]["tap"] is None


def test_visual_requires_images_opt_in_before_device_access(screen, monkeypatch):
    runtime, backend, _ = screen
    monkeypatch.setenv("ANDROID_USE_JEV_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    backend.screenshot = lambda: pytest.fail("Must not capture without the images opt-in")
    for op, args in (("locate", request()), ("tap", visual_args())):
        result = runtime.call(op, **args)
        assert result["error"]["code"] == "jev_images_disabled"
    assert not backend.actions


def test_visual_tap_long_press_and_wait_for(screen, monkeypatch):
    runtime, backend, _ = screen
    jev_images(monkeypatch, vision_policy())
    result = runtime.call(
        "long_press",
        **visual_args(duration=1.0, wait_for={"selector": {"package": PACKAGE}}),
    )
    assert result["ok"] and result["wait"]["condition_met"]
    assert backend.actions == [("long_press", 918, 300, 1.0)]


@pytest.mark.parametrize(
    "args",
    [
        {"visual": request(), "x": 1, "y": 1},
        {"visual": request(), "selector": {"text": "a"}},
        {},
    ],
)
def test_tap_target_is_exclusive(screen, args):
    runtime, backend, _ = screen
    assert runtime.call("tap", **args)["error"]["code"] == "invalid_arguments"


def test_visual_tap_is_not_allowed_in_batch(screen, monkeypatch):
    runtime, backend, _ = screen
    jev_images(monkeypatch, vision_policy())
    result = runtime.call("batch", steps=[{"op": "tap", "args": visual_args()}])
    assert result["error"]["code"] == "invalid_arguments" and not backend.actions
