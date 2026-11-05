"""Local, read-only OCR with device-owned ROI evidence and strict freshness checks."""

import hashlib
import json
import math
import os
import platform
import re
import signal
import subprocess
import tempfile
import time
import uuid
from importlib.resources import files
from pathlib import Path

from PIL import ImageDraw

from .backend import UseError, state_dir

# Authentication prompts: never send their text or pixels to a decision provider.
AUTH_TEXT = re.compile(
    r"验证码|输入.{0,12}(?:密码|PIN)|enter.{0,40}(?:password|pin)|"
    r"verification code|one[- ]time (?:password|code)",
    re.I,
)


def run_local(command, timeout):
    if timeout <= 0:
        raise UseError("ocr_timeout", "OCR budget exhausted")
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
        )
    except OSError:
        raise UseError(
            "ocr_unavailable", "Install Xcode Command Line Tools for macOS OCR"
        ) from None
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # The child can exit between the timeout and group cleanup.
        process.communicate()
        raise UseError("ocr_timeout", "Local OCR operation exceeded its budget") from None
    if process.returncode:
        raise UseError("ocr_unavailable", "Local OCR provider failed; inspect local dependencies")
    return stdout


class VisionOCR:
    name = "macos_vision"

    def recognize(self, image, deadline):
        if platform.system() != "Darwin":
            raise UseError("ocr_unavailable", "Built-in OCR currently requires macOS Vision")
        source = files("android_use").joinpath("assets/ocr.swift").read_bytes()
        version = "vision-v1-" + platform.mac_ver()[0]
        key = hashlib.sha256(source + (version + platform.machine()).encode()).hexdigest()[:20]
        cache = state_dir() / "ocr" / key
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        binary = cache / "recognize"
        start = time.monotonic()
        if not binary.exists():
            with tempfile.TemporaryDirectory(dir=cache) as folder:
                swift = Path(folder) / "ocr.swift"
                output = Path(folder) / "recognize"
                swift.write_bytes(source)
                run_local(
                    ["/usr/bin/swiftc", str(swift), "-o", str(output)],
                    min(15, deadline - time.monotonic()),
                )
                output.chmod(0o700)
                os.replace(output, binary)
        with tempfile.TemporaryDirectory(prefix="android-use-ocr-") as folder:
            path = Path(folder) / "region.png"
            image.save(path, format="PNG")
            raw = run_local([str(binary), str(path)], min(5, deadline - time.monotonic()))
        if len(raw) > 2_000_000:
            raise UseError("ocr_invalid_response", "OCR output exceeds local bounds")
        try:
            blocks = json.loads(raw)
            if not isinstance(blocks, list) or len(blocks) > 500:
                raise ValueError()
            for block in blocks:
                if not isinstance(block["text"], str) or not 0 < len(block["text"]) <= 2000:
                    raise ValueError()
                numbers = [block["confidence"], *block["box"]]
                if len(block["box"]) != 4 or any(
                    type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1
                    for n in numbers
                ):
                    raise ValueError()
                x, y, w, h = block["box"]
                if w <= 0 or h <= 0 or x + w > 1.001 or y + h > 1.001:
                    raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise UseError(
                "ocr_invalid_response", "Local OCR returned invalid text or boxes"
            ) from None
        return {
            "provider": self.name,
            "version": version,
            "blocks": blocks,
            "elapsed_seconds": time.monotonic() - start,
        }


def roi_for(size, region):
    width, height = size
    roi = region.model_dump() if region else {"x": 0, "y": 0, "width": width, "height": height}
    if roi["x"] + roi["width"] > width or roi["y"] + roi["height"] > height:
        raise UseError("invalid_region", "OCR region exceeds the current display")
    return roi


def capture(runtime, context, region, masks):
    roi = roi_for(context["size"], region)
    if runtime._checked_context() != context:
        raise UseError("goal_scope_changed", "Context changed before OCR capture")
    image = runtime.backend.screenshot()
    if list(image.size) != context["size"]:
        raise UseError("display_changed", "Screenshot and display geometry differ")
    if runtime._checked_context() != context:
        raise UseError("goal_scope_changed", "Context changed during OCR capture")
    x, y, w, h = (roi[k] for k in ("x", "y", "width", "height"))
    image = image.crop((x, y, x + w, y + h)).convert("RGB")
    area = 0
    for mask in masks:
        if not (
            x <= mask.x
            and y <= mask.y
            and mask.x + mask.width <= x + w
            and mask.y + mask.height <= y + h
        ):
            raise UseError("invalid_region", "OCR masks must be contained in the OCR region")
        area += mask.width * mask.height
        if area > w * h * 0.2:
            raise UseError("invalid_region", "OCR masks may cover at most 20% of the region")
        ImageDraw.Draw(image).rectangle(
            (mask.x - x, mask.y - y, mask.x - x + mask.width - 1, mask.y - y + mask.height - 1),
            fill="white",
        )
    digest = hashlib.sha256(image.tobytes()).hexdigest()
    runtime._check_paused()
    return image, {
        "id": uuid.uuid4().hex,
        "roi": roi,
        "image_size": list(image.size),
        "display_size": context["size"],
        "captured_at": time.time(),
        "sha256": digest,
        "masks": [m.model_dump() for m in masks],
    }


def visual_rows(blocks, roi):
    nodes = []
    for index, block in enumerate(blocks):
        x, y, w, h = block["box"]
        rect = [
            roi["x"] + round(x * roi["width"]),
            roi["y"] + round(y * roi["height"]),
            max(1, round(w * roi["width"])),
            max(1, round(h * roi["height"])),
        ]
        nodes.append(
            {
                "id": index,
                "text": block["text"],
                "description": "",
                "rect": rect,
                "ocr_confidence": block["confidence"],
                "style": "unknown",
            }
        )
    return nodes
