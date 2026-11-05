"""MCP stdio entrypoint. stdout is exclusively reserved for protocol messages."""

import asyncio
import json
from importlib.resources import files

from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ImageContent, Resource, TextContent, Tool, ToolAnnotations

from . import __version__
from .models import ARG_MODELS, BatchStep
from .runtime import Runtime, extract_images

SCREEN_URI = "ui://android-use/phone-0.2.1.html"
SCREEN_META = {
    "ui": {
        "csp": {"connectDomains": [], "resourceDomains": []},
        "prefersBorder": False,
        "permissions": {"clipboardWrite": {}},
    },
    "openai/ui": {"availableDisplayModes": ["fullscreen"], "preferredDisplayMode": "fullscreen"},
}


def obj(properties=None):
    return {"type": "object", "properties": properties or {}, "additionalProperties": False}


SCHEMAS = {name: model.model_json_schema() for name, model in ARG_MODELS.items()}
SCHEMAS.update(
    {
        "doctor": obj(),
        "setup": obj(
            {
                "action": {
                    "type": "string",
                    "enum": ["status", "discover", "configure", "start"],
                    "default": "status",
                },
                "serial": {"type": "string", "minLength": 1},
            }
        ),
        "ready": obj({"screenshot": {"type": "boolean", "default": False}}),
        "apps": obj(
            {
                "query": {"type": "string", "default": ""},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            }
        ),
        "pause": obj(),
        "resume": obj(),
        "screen": obj(),
        "screen_frame": obj({"after_seq": {"type": "integer", "minimum": 0}}),
        "screen_action": obj(
            {
                "action": {
                    "type": "string",
                    "enum": [
                        "refresh",
                        "home",
                        "back",
                        "tap",
                        "swipe",
                        "save",
                        "jev_status",
                        "jev_save",
                        "jev_test",
                        "jev_disable",
                        "jev_clear",
                        "jev_images_on",
                        "jev_images_off",
                    ],
                },
                # Private validation avoids SDK schema errors echoing the secret value.
                "api_key": {"description": "Write-only key, validated privately by the server"},
                "x": {"type": "integer", "minimum": 0},
                "y": {"type": "integer", "minimum": 0},
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            }
        ),
        "batch": obj(
            {
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": BatchStep.model_json_schema(),
                }
            }
        ),
    }
)
SCHEMAS["batch"]["required"] = ["steps"]
SCHEMAS["screen_action"]["required"] = ["action"]
DESCRIPTIONS = {
    "read_fields": "Read-only Jev field judgments on current app text. source=auto uses the tree "
    "when text/expected_labels and money candidates suffice; otherwise captures a device ROI "
    "and runs local macOS Vision OCR (requires Xcode Command Line Tools). source=tree disables "
    "OCR; screen_ocr forces it. OCR pixels are checked again after inference. No visual clicks. "
    "Extract code-owned money/text "
    "spans or classify supplied options, all fields in one request. Requires Jev enabled. "
    "evidence_text optionally supplies Agent OCR text, explicitly marked caller_provided_text, "
    "not verified as a phone read. Missing/ambiguous values return NONE. Decimal parsing is local. "
    "region limits evidence in display pixels; ocr_masks exclude non-evidence from OCR and hashing. "
    "min_ocr_confidence is optional uncalibrated provider telemetry, not accuracy. Declare currency "
    "for ambiguous symbols. Optional sum_check validates same-currency Decimal sums in code; "
    "no device actions. verified=false always requires Agent business verification.",
    "locate": "Read-only Jev Box location for elements the accessibility tree lacks (canvas, "
    "games, WebView images, icons without labels). Sends one masked screenshot of region (default "
    "full display) to TypeSafe, so it needs Jev plus the separate images opt-in "
    "(允许发送截图（视觉定位） in screen settings, or ANDROID_USE_JEV_IMAGES=1); otherwise "
    "jev_images_disabled. Refuses password/OTP screens and paused devices. One request asks "
    "presence, match count and a box; status is located, not_found, ambiguous, low_confidence "
    "or masked. box=[x,y,width,height] and tap=[x,y] are display pixels; tap is set only when "
    "located. masks blank sensitive areas before upload. Prefer trees/selectors when they work; "
    "a located box is a model judgment, not verification.",
    "execute_goal": "Agent plans, a decision policy selects candidate IDs, Android Use executes. "
    "decider=auto uses Jev when enabled (TypeSafe key in screen settings, or "
    "ANDROID_USE_JEV_ENABLED=1 / TYPESAFE_API_KEY; sends visible app UI text to TypeSafe), "
    "otherwise host: no key, nothing sent externally. Host mode returns status=decision_required "
    "with compact state, option IDs and a one-time token; choose with aua_goal_decide. "
    "Caller supplies goal, foreground package, exact input texts and optional completion selector. "
    "Compact semantic state and parallel operation/target questions share one Jev request per step. "
    "Each Jev request also judges goal completion; without a completion selector a confident "
    "ACHIEVED stops before another action (stop_reason=model_goal_achieved, unverified: check it). "
    "input_macros=true can combine stable-field focus and exact input with native readback; "
    "max_steps counts candidate selections, with at most two native writes per input macro. "
    "Bounded candidate-only actions; no automatic replay. status=handoff is not success. "
    "effect_timeout_seconds bounds observation after acknowledged mutations; unresolved effects "
    "hand off without another mutation. Geometry-only changes do not prove an effect. "
    "Keep existing tools for planning, unsupported controls and recovery. Password screens hand off.",
    "goal_decide": "Host-decider step of aua_execute_goal: send one option ID from the latest "
    "decision_required result with its one-time token. Android Use reads the UI again; if it "
    "changed, the choice is discarded and a fresh decision is returned "
    "(previous_choice_stale=true). Otherwise it executes that candidate once, checks its effect "
    "and returns the next decision, completion or handoff. An unknown option ID keeps the token; "
    "any other outcome consumes it. Tokens expire after 5 idle minutes and need the same MCP "
    "server process. timeout_seconds counts device time only, not host thinking time.",
    "doctor": "Read local ADB device inventory and configuration. Does not start a phone service.",
    "setup": "First use: status/discover, start once if needed, then ready. configure selects a serial. "
    "start pushes a pinned JAR and reuses/starts UiAutomator without root or keyboard APK.",
    "ready": "Verify unlocked screen, UiAutomator, foreground, display and a nonempty tree. "
    "Does not start/restart services. Reuse the returned observation.",
    "observe": "Read a current accessibility tree and/or screenshot. Coordinates are Android display "
    "pixels; image pixels multiplied by pixel_to_device give action coordinates.",
    "find": "Find exact text/description/resource_id/class_name/package or substring text/description. "
    "Multiple targets require an explicit 0-based index; bounds don't prove unobstructed access.",
    "tap": "Tap once by selector OR x/y display pixels OR visual (aua_locate args: Jev Box with "
    "the images opt-in). visual taps the box center only when located, after rechecking the "
    "pixels around the box, never in batch. Ambiguity returns candidates and a screenshot. "
    "Optional wait_for waits for visible/gone selector after action; timeout never replays it. "
    "Optional observation_id checks same runtime/app/display context. No automatic replay.",
    "long_press": "Long press once by selector OR x/y display pixels for duration seconds (0.5..3). "
    "Supports wait_for and observe. Inspect uncertain results; no replay.",
    "double_tap": "Perform one intentional pair at selector OR x/y display pixels. "
    "interval is seconds between clicks, excluding RPC latency. Supports wait_for and observe. "
    "A failed pair may have executed its first click; never replay automatically.",
    "drag": "Drag once from start {x,y} to end {x,y}, in Android display pixels. "
    "Supports duration, observation_id, wait_for and observe. No automatic replay.",
    "scroll_until": "Find a visible selector, swiping only if absent. Bounded by max_swipes and "
    "timeout_seconds; stops after two unchanged regional snapshots, or app/display change. "
    "Does not click the result. no_progress does not prove list end. Use region for nested lists.",
    "type_text": "Set Unicode text on a focused, editable, non-password field. Focus it with tap first. "
    "Default replace=true, no submit. verify checks exact readback. App must support setText. "
    "Newlines need allow_newlines=true. Never blindly retry uncertain input.",
    "swipe": "Perform one swipe. Direction is finger motion: up usually reveals later rows. "
    "Use display-pixel region if needed, observe for next-step context. No automatic retries.",
    "press_button": "Press home/back/recent/enter/volume once. Enter may submit; user must authorize it.",
    "launch_app": "Launch an installed package's launcher activity once; no force-stop or data reset.",
    "apps": "List installed third-party package IDs; query matches package ID, not human app name.",
    "wait": "Wait at most 20 seconds for a selector to be visible or gone. "
    "condition_met reports the requested condition; found reports current visible matches.",
    "batch": "Execute up to 20 known steps, using each operation's normal args. Entire plan is "
    "validated before actions. Stops on error, unmet condition, scroll miss, Enter submission "
    "or 25s budget (in-flight RPCs may overrun). "
    "Resume from stopped_at; never replay completed/uncertain steps.",
    "pause": "Pause Android Use reads/actions for user authentication takeover across CLI/MCP processes.",
    "resume": "Resume only after user confirms authentication is finished; then get a fresh observation.",
    "screen": "Open/reuse the Android live screen sidebar. Uses an independent H.264 video stream, "
    "not screenshot/tree polling. Requires ffmpeg. READY also links the same panel.",
    "screen_frame": "App-only cached video frame. Does not query UI trees or capture screenshots.",
    "screen_action": "App-only preview controls and local Jev settings. Phone actions are blocked "
    "during authentication takeover. Settings never return credentials; save tests a fixed "
    "synthetic Choice before enabling. Users enter their own key in the screen UI.",
}


def create_server(runtime=None):
    runtime = runtime or Runtime()
    server = Server(
        "android-use",
        version=__version__,
        instructions="First call aua_setup(status), start if absent, then aua_ready. "
        "The host agent owns task understanding, planning, exact text composition, recovery and "
        "final business verification. Use aua_execute_goal for a short local UI subgoal: Jev, when "
        "enabled, decides the next candidate; otherwise you decide each step with "
        "aua_goal_decide. Android Use alone executes actions. "
        "Known paths should still use batch. "
        "Optimize end-to-end completion time and success rate, reducing host-model round trips. "
        "Do not route based on Jev token cost. With Jev, delegate a coherent local decision loop "
        "rather than asking the host to approve every low-level choice. Reuse a safe returned "
        "observation when planning the next step instead of immediately reading it again. "
        "On handoff inspect steps and current state, then continue with existing tools; do not "
        "repeat the original goal blindly. Jev does not replace the host agent. "
        "Use trees first, screenshots for visual content/failures; aua_locate or tap visual "
        "only when no tree target exists and the user opted in to sending screenshots. "
        "Actions accept wait_for={selector,state:visible|gone,timeout_seconds}; "
        "use scroll_until for bounded list search. An unmet condition never replays the action. "
        "Execute actions once; do not replay uncertain mutations. "
        "Password/PIN/OTP authentication belongs to the user: pause, ask, await confirmation, "
        "resume and observe. Verify final business outcomes from actual device state.",
    )

    @server.list_tools()
    async def list_tools():
        reads = {"doctor", "observe", "find", "wait", "apps", "ready", "read_fields", "locate"}
        tools = [
            Tool(
                name="aua_" + name,
                description=DESCRIPTIONS[name],
                inputSchema=schema,
                annotations=ToolAnnotations(
                    readOnlyHint=name in reads, idempotentHint=name in reads, openWorldHint=True
                ),
            )
            for name, schema in SCHEMAS.items()
        ]
        for tool in tools:
            if tool.name in {"aua_ready", "aua_screen"}:
                tool.meta = {"ui": {"resourceUri": SCREEN_URI}}
            if tool.name == "aua_screen":
                tool.meta["openai/ui"] = {"entrypoints": [{"type": "thread"}]}
                tool.annotations = ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                )
            if tool.name in {"aua_pause", "aua_resume"}:
                tool.annotations = ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                )
            if tool.name in {"aua_screen_frame", "aua_screen_action"}:
                tool.meta = {"ui": {"visibility": ["app"]}}
            if tool.name == "aua_screen_frame":
                tool.annotations = ToolAnnotations(
                    readOnlyHint=True, idempotentHint=True, openWorldHint=False
                )
        return tools

    @server.list_resources()
    async def list_resources():
        return [
            Resource(
                uri=SCREEN_URI,
                name="Android 手机屏幕",
                mimeType="text/html;profile=mcp-app",
                _meta=SCREEN_META,
            )
        ]

    @server.read_resource()
    async def read_resource(uri):
        if str(uri) != SCREEN_URI:
            raise ValueError("Unknown screen resource")
        return [
            ReadResourceContents(
                content=files("android_use").joinpath("assets/phone-screen.html").read_text(),
                mime_type="text/html;profile=mcp-app",
                meta=SCREEN_META,
            )
        ]

    @server.call_tool()
    async def call_tool(name, arguments):
        if not name.startswith("aua_") or name[4:] not in SCHEMAS:
            raise ValueError("Unknown tool")
        # Worker thread keeps blocking ADB/HTTP away from the protocol event loop.
        # Runtime calls are additionally serialized by the per-device cross-process lock.
        result = await asyncio.to_thread(runtime.call, name[4:], **arguments)
        if name == "aua_ready" and result.get("ready"):
            opened = await asyncio.to_thread(runtime.screen_call, "screen")
            result["preview"] = {
                "available": opened.get("ok", False),
                "resource_uri": SCREEN_URI,
                "error": opened.get("error"),
            }
        clean, pictures = extract_images(result)
        if name == "aua_screen":
            clean.pop("url", None)  # Private loopback token is unnecessary in the native widget.
        if name == "aua_screen_frame":
            return CallToolResult(content=[], structuredContent=clean, isError=not clean["ok"])
        content = [TextContent(type="text", text=json.dumps(clean, ensure_ascii=False))]
        content.extend(
            ImageContent(type="image", data=data, mimeType="image/jpeg") for data in pictures
        )
        return CallToolResult(
            content=content,
            structuredContent=clean,
            isError=not clean["ok"],
            **(
                {"_meta": {"openai/widgetSessionId": "android-use-screen"}}
                if name in {"aua_ready", "aua_screen"}
                else {}
            ),
        )

    return server


async def run():
    server = create_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
