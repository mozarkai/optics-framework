"""Read-only state-inspection MCP tools.

Wrap the screenshot / page-source / elements endpoints so the LLM can
observe the device between keyword calls without juggling the generic
`run_keyword` path. Each tool delegates to the same FastAPI handler the
REST API uses, so behaviour stays in sync.

Errors raise; see tools/session.py for the rationale.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from mcp import types as mcp_types

from optics_framework.common.expose_api import (
    run_keyword_endpoint,
    session_manager,
)

TOOL_DEFINITIONS: list[mcp_types.Tool] = [
    mcp_types.Tool(
        name="optics_screenshot",
        description=(
            "Capture the current device screenshot for a session. Returns "
            "base64-encoded PNG bytes in the execution result."
        ),
        inputSchema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    ),
    mcp_types.Tool(
        name="optics_page_source",
        description="Capture the current XML / HTML page source from the device.",
        inputSchema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    ),
    mcp_types.Tool(
        name="optics_screen_elements",
        description=(
            "Get the parsed list of on-screen elements (text + bounds) "
            "from the current page source."
        ),
        inputSchema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    ),
    mcp_types.Tool(
        name="optics_interactive_elements",
        description=(
            "Get interactive elements on the current screen, optionally "
            "filtered. filter_config accepts any of: all, interactive, "
            "buttons, inputs, images, text."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "filter_config": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Subset of: all, interactive, buttons, inputs, images, text.",
                },
            },
            "required": ["session_id"],
        },
    ),
    mcp_types.Tool(
        name="optics_driver_session_id",
        description="Return the underlying driver session id (e.g. the Appium session).",
        inputSchema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    ),
]

_KEYWORD_FOR_TOOL = {
    "optics_screenshot": ("capture_screenshot", None),
    "optics_page_source": ("capture_pagesource", None),
    "optics_screen_elements": ("get_screen_elements", None),
    "optics_interactive_elements": ("get_interactive_elements", "filter_config"),
    "optics_driver_session_id": ("get_driver_session_id", None),
}


def tool_names() -> set[str]:
    return set(_KEYWORD_FOR_TOOL.keys())


def _ok(payload: Any) -> list[mcp_types.TextContent]:
    return [mcp_types.TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


async def handle(name: str, arguments: dict[str, Any]) -> list[mcp_types.TextContent] | None:
    mapping = _KEYWORD_FOR_TOOL.get(name)
    if mapping is None:
        return None
    keyword, optional_arg = mapping

    session_id = arguments.get("session_id")
    if not session_id:
        raise ValueError("session_id is required")
    if session_manager.get_session(session_id) is None:
        raise LookupError(f"session not found: {session_id}")

    params: dict[str, Any] | None = None
    if optional_arg and arguments.get(optional_arg):
        params = {optional_arg: arguments[optional_arg]}

    try:
        response = await run_keyword_endpoint(session_id, keyword, params)
    except HTTPException as e:
        raise RuntimeError(f"{e.status_code}: {e.detail}") from e
    return _ok(response.model_dump() if hasattr(response, "model_dump") else response)
