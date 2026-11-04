import sys
import time

import pytest
from PIL import Image, ImageDraw
from test_structured_jev import PACKAGE, XML, policy_for

from android_use.backend import UseError
from android_use.fields import read_fields
from android_use.ocr import VisionOCR, run_local


@pytest.fixture
def ocr_rig(rig, monkeypatch):
    runtime, backend = rig
    runtime._connect()
    backend.xml = "<hierarchy/>"

    class LocalOCR:
        def recognize(self, image, deadline):
            return {
                "provider": "fixture",
                "version": "1",
                "elapsed_seconds": 0.01,
                "blocks": [
                    {"text": "合计 ¥21.80", "confidence": 0.99, "box": [0.1, 0.1, 0.8, 0.05]}
                ],
            }

    monkeypatch.setattr("android_use.fields.VisionOCR", LocalOCR)
    return runtime, backend


def request(**extra):
    return {
        "package": PACKAGE,
        "fields": [{"name": "total", "instructions": "actual total"}],
        **extra,
    }


def test_auto_ocr_is_device_owned_and_read_only(ocr_rig):
    runtime, backend = ocr_rig
    result = read_fields(
        runtime,
        request(region={"x": 100, "y": 200, "width": 800, "height": 1200}),
        policy_for(lambda n, opts: next(iter(opts))),
    )
    assert result["source"] == "device_screen_ocr" and result["source_verified_on_device"]
    assert result["freshness"] == "roi_pixels_checked"
    assert result["fields"]["total"]["value"] == "¥21.80"
    assert result["fields"]["total"]["evidence"]["boxes"] == [[180, 320, 640, 60]]
    assert backend.screenshots == 2 and not backend.actions
    assert result["metrics"]["model_calls"] == 1 and not result["verified"]


def test_healthy_ax_does_not_capture_or_run_ocr(ocr_rig, monkeypatch):
    runtime, backend = ocr_rig
    backend.xml = XML
    monkeypatch.setattr("android_use.fields.VisionOCR", lambda: pytest.fail("OCR must not run"))
    result = read_fields(runtime, request(), policy_for(lambda n, opts: next(iter(opts))))
    assert result["source"] == "accessibility_tree" and backend.screenshots == 0


def test_anchor_absent_upgrades_before_first_model_call(ocr_rig):
    runtime, backend = ocr_rig
    backend.xml = XML.replace("实付", "other")
    result = read_fields(
        runtime, request(expected_labels=["合计"]), policy_for(lambda n, opts: next(iter(opts)))
    )
    assert result["source"] == "device_screen_ocr"
    assert result["route"]["ax_sufficient"] is False and result["metrics"]["model_calls"] == 1


def test_changed_roi_discards_all_judgments(ocr_rig):
    runtime, backend = ocr_rig
    image = Image.new("RGB", backend.size(), "white")
    backend.screenshot = lambda: image.copy()
    policy = policy_for(
        lambda n, opts: next(iter(opts)),
        lambda _: ImageDraw.Draw(image).rectangle((0, 0, 100, 100), fill="red"),
    )
    with pytest.raises(UseError) as exc:
        read_fields(runtime, request(), policy)
    assert exc.value.code == "stale_field_evidence" and not backend.actions
    assert exc.value.details["metrics"]["model_calls"] == 1


def test_masked_region_is_excluded_from_ocr_and_freshness(ocr_rig):
    runtime, backend = ocr_rig
    image = Image.new("RGB", backend.size(), "white")
    backend.screenshot = lambda: image.copy()
    policy = policy_for(
        lambda n, opts: next(iter(opts)),
        lambda _: ImageDraw.Draw(image).rectangle((0, 0, 9, 9), fill="red"),
    )
    result = read_fields(
        runtime, request(ocr_masks=[{"x": 0, "y": 0, "width": 10, "height": 10}]), policy
    )
    assert result["fields"]["total"]["accepted"]
    assert result["frame"]["masks"][0]["width"] == 10


@pytest.mark.parametrize(
    "mask",
    [
        {"x": 0, "y": 0, "width": 1080, "height": 1000},
        {"x": 1070, "y": 0, "width": 20, "height": 10},
    ],
)
def test_invalid_masks_stop_before_model(ocr_rig, mask):
    runtime, backend = ocr_rig
    policy = policy_for(lambda n, opts: next(iter(opts)))
    with pytest.raises(UseError) as exc:
        read_fields(runtime, request(ocr_masks=[mask]), policy)
    assert exc.value.code == "invalid_region" and policy.metrics["model_calls"] == 0
    assert not backend.actions


@pytest.mark.parametrize("change", ["pause", "lock", "password", "scope"])
def test_ocr_phase_rechecks_auth_and_scope_before_api(ocr_rig, monkeypatch, change):
    runtime, backend = ocr_rig

    def recognize(image, deadline):
        if change == "pause":
            runtime._pause_path().touch()
        elif change == "lock":
            backend.is_locked = True
        elif change == "password":
            backend.xml = XML.replace('enabled="true"', 'enabled="true" password="true"')
        else:
            backend.front["package"] = "other.app"
        return {
            "provider": "fixture",
            "version": "1",
            "elapsed_seconds": 0,
            "blocks": [{"text": "¥21.80", "confidence": 1.0, "box": [0.1, 0.1, 0.2, 0.1]}],
        }

    monkeypatch.setattr(
        "android_use.fields.VisionOCR",
        lambda: type("Provider", (), {"recognize": staticmethod(recognize)})(),
    )
    policy = policy_for(lambda n, opts: next(iter(opts)))
    with pytest.raises(UseError):
        read_fields(runtime, request(), policy)
    assert policy.metrics["model_calls"] == 0 and not backend.actions


def test_caller_text_is_explicitly_unchecked(ocr_rig):
    runtime, _ = ocr_rig
    result = read_fields(
        runtime, request(evidence_text="合计 ¥21.80"), policy_for(lambda n, opts: next(iter(opts)))
    )
    assert result["freshness"] == "unchecked" and not result["source_verified_on_device"]
    assert result["frame"] is None and not result["region_applied"]


def test_unsupported_host_has_structured_error(monkeypatch):
    monkeypatch.setattr("android_use.ocr.platform.system", lambda: "Linux")
    with pytest.raises(UseError) as exc:
        VisionOCR().recognize(Image.new("RGB", (10, 10)), time.monotonic() + 5)
    assert exc.value.code == "ocr_unavailable"


def test_ocr_subprocess_timeout_is_bounded():
    with pytest.raises(UseError) as exc:
        run_local([sys.executable, "-c", "import time;time.sleep(10)"], 0.05)
    assert exc.value.code == "ocr_timeout"


@pytest.mark.parametrize(
    "score,minimum,accepted", [(0.9, 0.8, True), (0.3, 0, True), (0.3, 0.5, False)]
)
def test_amount_confidence_uses_its_block_and_an_explicit_floor(
    ocr_rig, monkeypatch, score, minimum, accepted
):
    runtime, _ = ocr_rig

    def recognize(image, deadline):
        return {
            "provider": "fixture",
            "version": "1",
            "elapsed_seconds": 0,
            "blocks": [
                {"text": "合计", "confidence": 0.1, "box": [0.1, 0.1, 0.2, 0.05]},
                {"text": "¥21.80", "confidence": score, "box": [0.6, 0.1, 0.3, 0.05]},
            ],
        }

    monkeypatch.setattr(
        "android_use.fields.VisionOCR",
        lambda: type("Provider", (), {"recognize": staticmethod(recognize)})(),
    )
    result = read_fields(
        runtime, request(min_ocr_confidence=minimum), policy_for(lambda n, opts: next(iter(opts)))
    )
    item = result["fields"]["total"]
    assert item["accepted"] is accepted and item["evidence"]["ocr_confidence"] == score
    assert item["evidence"]["boxes"] == [[648, 240, 324, 120]]
    if not accepted:
        assert item["value"] is None and item["review_reason"] == "ocr_low_confidence"


@pytest.mark.parametrize(
    "text", ["请输入支付密码", "输入PIN码", "Enter your PIN", "One-time code", "短信验证码"]
)
def test_ocr_authentication_prompt_never_reaches_model(ocr_rig, monkeypatch, text):
    runtime, backend = ocr_rig
    monkeypatch.setattr(
        "android_use.fields.VisionOCR",
        lambda: type(
            "Provider",
            (),
            {
                "recognize": staticmethod(
                    lambda image, deadline: {
                        "provider": "fixture",
                        "version": "1",
                        "elapsed_seconds": 0,
                        "blocks": [{"text": text, "confidence": 1, "box": [0.1, 0.1, 0.8, 0.1]}],
                    }
                )
            },
        )(),
    )
    policy = policy_for(lambda n, opts: next(iter(opts)))
    with pytest.raises(UseError, match="authentication"):
        read_fields(runtime, request(), policy)
    assert policy.metrics["model_calls"] == 0 and not backend.actions


@pytest.mark.parametrize("change", ["pause", "scope", "lock"])
def test_late_ocr_guard_failure_retains_inference_metrics(ocr_rig, change):
    runtime, backend = ocr_rig

    def interrupt(_):
        if change == "pause":
            runtime._pause_path().touch()
        elif change == "lock":
            backend.is_locked = True
        else:
            backend.front["package"] = "other.app"

    with pytest.raises(UseError) as exc:
        read_fields(runtime, request(), policy_for(lambda n, opts: next(iter(opts)), interrupt))
    assert exc.value.details["metrics"]["model_calls"] == 1 and not backend.actions


@pytest.mark.parametrize(
    "extra",
    [
        {"source": "tree"},
        {"source": "screen_ocr"},
        {"region": {"x": 0, "y": 0, "width": 100, "height": 100}},
        {"ocr_masks": [{"x": 0, "y": 0, "width": 10, "height": 10}]},
    ],
)
def test_caller_text_cannot_claim_device_roi_or_provider(extra):
    from pydantic import ValidationError

    from android_use.models import ReadFieldsArgs

    with pytest.raises(ValidationError):
        ReadFieldsArgs.model_validate(request(evidence_text="¥21.80", **extra))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [{"text": "x", "confidence": float("nan"), "box": [0, 0, 1, 1]}],
        [{"text": "x", "confidence": 1, "box": [0.9, 0, 0.5, 1]}],
        [{"text": "x", "confidence": 1, "box": [0, 0, 1]}],
    ],
)
def test_malformed_local_provider_output_is_rejected(monkeypatch, payload):
    import json
    from pathlib import Path

    monkeypatch.setattr("android_use.ocr.platform.system", lambda: "Darwin")
    monkeypatch.setattr("android_use.ocr.platform.mac_ver", lambda: ("fixture", (), ""))

    def local(command, timeout):
        if command[0] == "/usr/bin/swiftc":
            Path(command[-1]).write_bytes(b"fixture")
            return b""
        return json.dumps(payload).encode()

    monkeypatch.setattr("android_use.ocr.run_local", local)
    with pytest.raises(UseError) as exc:
        VisionOCR().recognize(Image.new("RGB", (10, 10)), time.monotonic() + 5)
    assert exc.value.code == "ocr_invalid_response"


def test_forced_tree_never_falls_back_to_ocr(ocr_rig, monkeypatch):
    runtime, backend = ocr_rig
    backend.xml = XML
    monkeypatch.setattr("android_use.fields.VisionOCR", lambda: pytest.fail("OCR disabled"))
    result = read_fields(
        runtime,
        request(source="tree", expected_labels=["absent"]),
        policy_for(lambda n, opts: next(iter(opts))),
    )
    assert result["source"] == "accessibility_tree" and backend.screenshots == 0


def test_forced_screen_ocr_can_override_healthy_tree(ocr_rig):
    runtime, backend = ocr_rig
    backend.xml = XML
    result = read_fields(
        runtime, request(source="screen_ocr"), policy_for(lambda n, opts: next(iter(opts)))
    )
    assert result["source"] == "device_screen_ocr" and result["route"]["ax_sufficient"]
    assert backend.screenshots == 2 and not backend.actions


def test_mask_is_whitened_before_recognition(ocr_rig, monkeypatch):
    runtime, _ = ocr_rig
    original = __import__("android_use.fields", fromlist=["VisionOCR"]).VisionOCR

    class MaskCheck(original):
        def recognize(self, image, deadline):
            assert image.getpixel((5, 5)) == (255, 255, 255)
            assert image.getpixel((20, 20)) == (0, 0, 0)
            return super().recognize(image, deadline)

    monkeypatch.setattr("android_use.fields.VisionOCR", MaskCheck)
    result = read_fields(
        runtime,
        request(ocr_masks=[{"x": 0, "y": 0, "width": 10, "height": 10}]),
        policy_for(lambda n, opts: next(iter(opts))),
    )
    assert result["all_fields_accepted"]
