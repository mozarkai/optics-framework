"""Optics Framework MCP server instance.

Single `Server("optics-framework")` that fans out across capability
modules. Each capability exposes `TOOL_DEFINITIONS` and an async
`handle(name, arguments)` callable; the server concatenates the
definitions for `list_tools` and walks the handlers in order for
`call_tool`.
"""

from __future__ import annotations

import json

from mcp.server import Server
from mcp import types as mcp_types

from optics_framework.mcp.tools import inspect as inspect_tools
from optics_framework.mcp.tools import keywords as keyword_tools
from optics_framework.mcp.tools import session as session_tools

server = Server("optics-framework")

_SYSTEM_PROMPT = """You are an Optics Framework operator — you drive mobile and web apps through Optics keywords on behalf of the user.

Workflow:
1. Call `optics_start_session` first. Provide driver_sources / elements_sources matching the target platform (e.g. driver_sources=["appium"], elements_sources=["appium_find_element"] for Android/iOS via Appium). Save the returned session_id.
2. Use `optics_screenshot`, `optics_page_source`, `optics_screen_elements`, or `optics_interactive_elements` to observe the current screen before acting.
3. Drive the app with the `optics_<keyword>` tools (press_element, enter_text, swipe, scroll, assert_presence, ...). Every keyword tool requires the session_id from step 1.
4. When done, call `optics_terminate_session` to release the driver.

Element identifiers in keyword args follow Optics conventions: plain text, `text=foo`, `xpath=//...`, `css=...`, or `<name>.png` for image-based location. Passing an array for any element-shaped param triggers Optics' built-in fallback ladder."""

ALL_TOOLS: list[mcp_types.Tool] = (
    session_tools.TOOL_DEFINITIONS
    + inspect_tools.TOOL_DEFINITIONS
    + keyword_tools.TOOL_DEFINITIONS
)


@server.list_prompts()
async def list_prompts() -> list[mcp_types.Prompt]:
    return [
        mcp_types.Prompt(
            name="optics_operator",
            description="Operator persona that drives apps through Optics keywords.",
        )
    ]


@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None) -> mcp_types.GetPromptResult:
    return mcp_types.GetPromptResult(
        description="Optics Framework operator system prompt",
        messages=[
            mcp_types.PromptMessage(
                role="user",
                content=mcp_types.TextContent(type="text", text=_SYSTEM_PROMPT),
            )
        ],
    )


@server.list_tools()
async def list_tools() -> list[mcp_types.Tool]:
    return ALL_TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[mcp_types.TextContent]:
    for handler in (session_tools, inspect_tools, keyword_tools):
        result = await handler.handle(name, arguments or {})
        if result is not None:
            return result
    return [
        mcp_types.TextContent(
            type="text",
            text=json.dumps({"error": f"unknown tool: {name}"}, indent=2),
        )
    ]
