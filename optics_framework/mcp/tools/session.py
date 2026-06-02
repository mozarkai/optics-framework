"""Session lifecycle MCP tools.

Wraps the FastAPI session endpoints from `expose_api` so MCP clients can
start/terminate the per-client Optics session that every keyword tool
needs.
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

TOOL_DEFINITIONS: list[mcp_types.Tool] = [
    mcp_types.Tool(
        name="optics_start_session",
        description=(
            "Start a new Optics Framework session. Returns a session_id you "
            "MUST pass to every subsequent optics_* keyword tool. Configure "
            "driver_sources / elements_sources / text_detection / "
            "image_detection the same way `optics serve` does: each is a "
            "list of either source-name strings (e.g. \"appium\") or "
            "single-key dicts mapping a source name to its config "
            "(enabled / url / capabilities). project_path lets you point at "
            "a folder of test_data/templates for vision-based keywords."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "driver_sources": {
                    "type": "array",
                    "description": "Driver backends in priority order.",
                    "items": {"type": ["string", "object"]},
                },
                "elements_sources": {
                    "type": "array",
                    "description": "Element source backends in priority order.",
                    "items": {"type": ["string", "object"]},
                },
                "text_detection": {
                    "type": "array",
                    "description": "OCR backends.",
                    "items": {"type": ["string", "object"]},
                },
                "image_detection": {
                    "type": "array",
                    "description": "Image-detection backends.",
                    "items": {"type": ["string", "object"]},
                },
                "project_path": {
                    "type": "string",
                    "description": "Filesystem path to an Optics project (for templates).",
                },
                "api_data": {
                    "type": "object",
                    "description": "Inline API definitions (same schema as api.yaml).",
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


def _ok(payload: Any) -> list[mcp_types.TextContent]:
    return [mcp_types.TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


def _err(message: str, status: int | None = None) -> list[mcp_types.TextContent]:
    payload: dict[str, Any] = {"error": message}
    if status is not None:
        payload["status"] = status
    return _ok(payload)


async def handle(name: str, arguments: dict[str, Any]) -> list[mcp_types.TextContent] | None:
    if name == "optics_start_session":
        try:
            cfg = SessionConfig(**arguments)
        except Exception as e:  # pydantic ValidationError lands here
            return _err(f"invalid session config: {e}", status=400)
        try:
            response = await create_session(cfg)
        except HTTPException as e:
            return _err(str(e.detail), status=e.status_code)
        return _ok(response.model_dump())

    if name == "optics_terminate_session":
        session_id = arguments.get("session_id")
        if not session_id:
            return _err("session_id is required", status=400)
        try:
            session_manager.terminate_session(session_id)
        except Exception as e:
            return _err(f"terminate failed: {e}", status=500)
        return _ok({"session_id": session_id, "status": "terminated"})

    if name == "optics_list_sessions":
        sessions = getattr(session_manager, "sessions", {}) or {}
        return _ok({"sessions": list(sessions.keys())})

    return None
