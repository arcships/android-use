import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_handshake_schemas_and_error(tmp_path):
    fake = tmp_path / "adb"
    fake.write_text(f'#!{sys.executable}\nprint("List of devices attached\\n")\n')
    fake.chmod(0o700)

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "android_use.server"],
            env={
                **os.environ,
                "ANDROID_USE_ADB": str(fake),
                "ANDROID_USE_STATE_DIR": str(tmp_path / "state"),
                "ANDROID_USE_JEV_ENABLED": "0",
            },
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                assert len(tools) == 23
                assert "aua_execute_goal" in {t.name for t in tools}
                assert {"aua_long_press", "aua_double_tap", "aua_drag", "aua_scroll_until"} <= {
                    t.name for t in tools
                }
                tap = next(t for t in tools if t.name == "aua_tap")
                assert "wait_for" in tap.inputSchema["properties"]
                screen = next(t for t in tools if t.name == "aua_screen")
                assert screen.meta["ui"]["resourceUri"] == "ui://android-use/phone-0.2.1.html"
                assert next(t for t in tools if t.name == "aua_screen_frame").meta["ui"][
                    "visibility"
                ] == ["app"]
                resource = (await session.list_resources()).resources[0]
                assert resource.mimeType == "text/html;profile=mcp-app"
                html = (await session.read_resource(resource.uri)).contents[0]
                assert "Android Use" in html.text and "__SCRIPT__" not in html.text
                assert html.meta["openai/ui"]["preferredDisplayMode"] == "fullscreen"
                assert html.meta["ui"]["permissions"]["clipboardWrite"] == {}
                missing_frame = await session.call_tool("aua_screen_frame", {})
                assert missing_frame.isError and missing_frame.content == []
                assert next(t for t in tools if t.name == "aua_observe").annotations.readOnlyHint
                doctor = await session.call_tool("aua_doctor", {})
                assert not doctor.isError
                assert doctor.structuredContent["devices"] == []
                invalid = await session.call_tool("aua_tap", {"x": "bad", "y": 1})
                assert invalid.isError
                disabled = await session.call_tool(
                    "aua_execute_goal",
                    {
                        "goal": "Show result",
                        "package": "demo.app",
                    },
                )
                assert disabled.isError
                assert disabled.structuredContent["error"]["code"] == "jev_disabled"
                absent = await session.call_tool("aua_ready", {})
                assert absent.isError
                assert absent.structuredContent["error"]["code"] == "device_selection_required"

    asyncio.run(exercise())
