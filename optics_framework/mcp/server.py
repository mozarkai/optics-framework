"""Optics Framework MCP server instance.

Single `Server("optics-framework")` that builds a route map at import
time from each capability module's `tool_names()`. `call_tool` is then
an O(1) dispatch into the owning handler — no per-call fan-out.

Each capability module exposes:
  - `TOOL_DEFINITIONS: list[mcp_types.Tool]`
  - `tool_names() -> set[str]`
  - `handle(name, arguments) -> list[TextContent] | None`

Handlers raise on failure; the MCP SDK's `call_tool` decorator catches
the exception and returns a `CallToolResult(isError=True, ...)` to the
client.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

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


# Build the dispatch table once. `keyword_tools` owns the long tail
# (one entry per keyword), session and inspect are static.
_Handler = Callable[[str, dict[str, Any]], Awaitable[list[mcp_types.TextContent] | None]]


def _build_route_map() -> dict[str, _Handler]:
    routes: dict[str, _Handler] = {}
    for module in (session_tools, inspect_tools, keyword_tools):
        for name in module.tool_names():
            if name in routes:
                raise RuntimeError(
                    f"duplicate MCP tool name across capability modules: {name}"
                )
            routes[name] = module.handle
    return routes


_ROUTES: dict[str, _Handler] = _build_route_map()


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
    handler = _ROUTES.get(name)
    if handler is None:
        # Unknown tool — raise so the SDK reports it as isError=True.
        raise LookupError(f"unknown tool: {name}")
    result = await handler(name, arguments or {})
    if result is None:
        # Belt-and-braces: a route-mapped handler shouldn't return None
        # for its own tool, but if it does, treat it as a programming
        # error rather than silently returning an empty body.
        raise RuntimeError(f"handler for {name} returned no content")
    return result
