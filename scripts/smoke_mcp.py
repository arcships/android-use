"""Opt-in, read-only real-device MCP smoke test. Run setup/start beforehand."""

import asyncio
import json
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run():
    params = StdioServerParameters(command=sys.executable, args=["-m", "android_use.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            result = await client.call_tool("aua_ready", {"screenshot": True})
            assert not result.isError, result.structuredContent
            data = result.structuredContent
            pictures = [b for b in result.content if b.type == "image"]
            assert data["ready"] and len(pictures) == 1
            assert "_image_data" not in data["observation"]
            assert result.meta["openai/widgetSessionId"] == "android-use-screen"
            resources = (await client.list_resources()).resources
            screen = next(t for t in tools.tools if t.name == "aua_screen")
            assert screen.meta["ui"]["resourceUri"] == str(resources[0].uri)
            resource = await client.read_resource(resources[0].uri)
            assert resource.contents[0].mimeType == "text/html;profile=mcp-app"
            deadline = time.monotonic() + 15
            native_frame = None
            while time.monotonic() < deadline:
                response = await client.call_tool("aua_screen_frame", {})
                assert response.content == []
                if response.structuredContent.get("frame"):
                    native_frame = response.structuredContent["frame"]
                    break
                await asyncio.sleep(0.2)
            assert native_frame is not None, "No native MCP video frame arrived"
            print(
                json.dumps(
                    {
                        "ready": True,
                        "tool_count": len(tools.tools),
                        "foreground": data["observation"]["foreground"],
                        "viewport": data["observation"]["viewport"],
                        "image_blocks": len(pictures),
                        "native_video_frame": True,
                        "native_video_size": [native_frame["width"], native_frame["height"]],
                    },
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    asyncio.run(run())
