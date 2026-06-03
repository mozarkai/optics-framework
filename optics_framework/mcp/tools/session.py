"""Session lifecycle MCP tools.

Wraps the FastAPI session endpoints from `expose_api` so MCP clients can
start/terminate the per-client Optics session that every keyword tool
needs.

Errors raise exceptions rather than returning structured error payloads
so the MCP SDK's call_tool handler (see mcp.server.Server.call_tool)
sets `isError=True` on the response — the documented way to surface
tool failures to the LLM.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from mcp import types as mcp_types

from optics_framework.common.expose_api import (
    SessionConfig,
    create_session,
    session_manager,
)


# Describe the shape of an individual source list item so the LLM has
# something concrete to generate against. Each entry may be either a
# bare source name (e.g. "appium") or a single-key dict mapping that
# name to its config block.
_SOURCE_ITEM_SCHEMA = {
    "oneOf": [
        {"type": "string", "description": "Source name, e.g. 'appium'."},
        {
            "type": "object",
            "description": (
                "Single-key mapping {source_name: {...}} describing the "
                "source and its config."
            ),
            "minProperties": 1,
            "maxProperties": 1,
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": True},
                    "url": {
                        "type": "string",
                        "description": "Backend URL (e.g. Appium http://localhost:4723).",
                    },
                    "capabilities": {
                        "type": "object",
                        "description": "Backend-specific capabilities map.",
                    },
                },
            },
        },
    ],
}


def _source_array_schema(label: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": label,
        "items": _SOURCE_ITEM_SCHEMA,
    }


TOOL_DEFINITIONS: list[mcp_types.Tool] = [
    mcp_types.Tool(
        name="optics_start_session",
        description=(
            "Start a new Optics Framework session. Returns a session_id you "
            "MUST pass to every subsequent optics_* keyword tool. Configure "
            "driver_sources / elements_sources / text_detection / "
            "image_detection the same way `optics serve` does."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "driver_sources": _source_array_schema(
                    "Driver backends in priority order (e.g. [\"appium\"] or "
                    "[{\"appium\": {\"url\": \"http://localhost:4723\", "
                    "\"capabilities\": {...}}}])."
                ),
                "elements_sources": _source_array_schema(
                    "Element source backends in priority order (e.g. "
                    "[\"appium_find_element\"])."
                ),
                "text_detection": _source_array_schema(
                    "OCR backends (e.g. [\"easyocr\"])."
                ),
                "image_detection": _source_array_schema(
                    "Image-detection backends (e.g. [\"templatematch\"])."
                ),
                "project_path": {
                    "type": "string",
                    "description": (
                        "Filesystem path to an Optics project; used to "
                        "discover template images for vision-based keywords."
                    ),
                },
                "api_data": {
                    "type": "object",
                    "description": (
                        "Inline API definitions (same JSON shape as api.yaml)."
                    ),
                },
            },
        },
    ),
    mcp_types.Tool(
        name="optics_terminate_session",
        description=(
            "Terminate an Optics session created with optics_start_session. "
            "Releases the driver, clears templates, removes the per-session "
            "temp dir."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    mcp_types.Tool(
        name="optics_list_sessions",
        description="List currently active Optics session IDs on this server.",
        inputSchema={"type": "object", "properties": {}},
    ),
]


def tool_names() -> set[str]:
    return {t.name for t in TOOL_DEFINITIONS}


def _ok(payload: Any) -> list[mcp_types.TextContent]:
    return [mcp_types.TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


async def handle(name: str, arguments: dict[str, Any]) -> list[mcp_types.TextContent] | None:
    if name == "optics_start_session":
        try:
            cfg = SessionConfig(**arguments)
        except Exception as e:  # pydantic ValidationError lands here
            raise ValueError(f"invalid session config: {e}") from e
        try:
            response = await create_session(cfg)
        except HTTPException as e:
            raise RuntimeError(f"{e.status_code}: {e.detail}") from e
        return _ok(response.model_dump())

    if name == "optics_terminate_session":
        session_id = arguments.get("session_id")
        if not session_id:
            raise ValueError("session_id is required")
        try:
            session_manager.terminate_session(session_id)
        except Exception as e:
            raise RuntimeError(f"terminate failed: {e}") from e
        return _ok({"session_id": session_id, "status": "terminated"})

    if name == "optics_list_sessions":
        return _ok({"sessions": session_manager.list_session_ids()})

    return None
