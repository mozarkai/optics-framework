"""Auto-generated MCP tools, one per discoverable Optics keyword.

For each KeywordInfo returned by `expose_api.discover_keywords()`, build
an MCP Tool whose schema mirrors the keyword's Python signature. At call
time, translate the MCP arguments into the FastAPI `ExecuteRequest`
shape and dispatch through `execute_keyword` so we inherit the full
fallback / event / template / KeywordRegistry machinery for free.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException
from mcp import types as mcp_types

from optics_framework.common.expose_api import (
    ExecuteRequest,
    KeywordInfo,
    KeywordParameter,
    discover_keywords,
    execute_keyword,
)

# Optics method names are valid Python identifiers; MCP tool names must
# match `^[a-zA-Z0-9_-]+$`. We prefix with `optics_` to (a) namespace
# the surface so it can't collide with other MCP servers a client mounts
# and (b) make tool calls easy to spot in transcripts.
_TOOL_PREFIX = "optics_"

# Keyword slugs the LLM should NOT call directly via MCP — they're
# covered by the dedicated session / inspect tools or are runner-only
# control flow that doesn't make sense outside a CSV/YAML test case.
_BLOCKLIST: frozenset[str] = frozenset(
    {
        # session lifecycle is handled by optics_start_session
        "launch_app",
        "close_and_terminate_app",
        # inspect tools wrap these with friendlier names
        "capture_screenshot",
        "capture_pagesource",
        "get_interactive_elements",
        "get_screen_elements",
        "get_driver_session_id",
        # flow control only meaningful inside a test-case graph
        "run_loop",
        "execute_module",
        "condition",
    }
)


def _slug_to_tool_name(slug: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", slug)
    return f"{_TOOL_PREFIX}{safe}"


def _tool_name_to_slug(name: str) -> str:
    return name[len(_TOOL_PREFIX):] if name.startswith(_TOOL_PREFIX) else name


def _json_type_for(annotation: str) -> dict[str, Any]:
    """Map an inspect-style annotation string to a JSON Schema fragment.

    discover_keywords stores types as `str(param.annotation)` — e.g.
    "<class 'int'>", "typing.Optional[str]", "typing.List[str]". We don't
    need a complete mapper; LLM clients are lenient. Conservative defaults
    keep us out of trouble.
    """
    a = annotation.lower()
    if "bool" in a:
        return {"type": "boolean"}
    if "int" in a and "list" not in a:
        return {"type": "integer"}
    if "float" in a and "list" not in a:
        return {"type": "number"}
    if "list" in a or "tuple" in a or "sequence" in a:
        return {"type": "array", "items": {"type": "string"}}
    if "dict" in a or "mapping" in a:
        return {"type": "object"}
    # Default to string — Optics keywords accept str | List[str] for most
    # element / fallback params and coerce internally.
    return {"type": "string"}


def _build_schema(params: list[KeywordParameter]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "session_id": {
            "type": "string",
            "description": "Session ID from optics_start_session.",
        },
    }
    required: list[str] = ["session_id"]
    for p in params:
        prop = _json_type_for(p.type)
        if p.default is not None:
            prop["default"] = p.default
        properties[p.name] = prop
        if p.default is None:
            required.append(p.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _build_tool(info: KeywordInfo) -> mcp_types.Tool:
    description = info.description.strip() or info.keyword
    # Prepend the human-readable name so the LLM has a clean handle even
    # if the docstring is empty.
    description = f"{info.keyword}. {description}".strip()
    return mcp_types.Tool(
        name=_slug_to_tool_name(info.keyword_slug),
        description=description,
        inputSchema=_build_schema(info.parameters),
    )


def _discover() -> tuple[list[mcp_types.Tool], dict[str, KeywordInfo]]:
    tools: list[mcp_types.Tool] = []
    index: dict[str, KeywordInfo] = {}
    for info in discover_keywords():
        if info.keyword_slug in _BLOCKLIST:
            continue
        tools.append(_build_tool(info))
        index[info.keyword_slug] = info
    return tools, index


TOOL_DEFINITIONS, _KEYWORD_INDEX = _discover()


def _ok(payload: Any) -> list[mcp_types.TextContent]:
    return [mcp_types.TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


def _err(message: str, status: int | None = None) -> list[mcp_types.TextContent]:
    payload: dict[str, Any] = {"error": message}
    if status is not None:
        payload["status"] = status
    return _ok(payload)


async def handle(name: str, arguments: dict[str, Any]) -> list[mcp_types.TextContent] | None:
    if not name.startswith(_TOOL_PREFIX):
        return None
    slug = _tool_name_to_slug(name)
    info = _KEYWORD_INDEX.get(slug)
    if info is None:
        return None  # let other capability handlers try

    session_id = arguments.get("session_id")
    if not session_id:
        return _err("session_id is required (call optics_start_session first)", status=400)

    # Strip the session_id from named params we forward; everything else
    # becomes the keyword's named args (execute_keyword handles both
    # dict and list shapes, and applies fallback logic for List[str]).
    named: dict[str, Any] = {k: v for k, v in arguments.items() if k != "session_id"}

    request = ExecuteRequest(
        mode="keyword",
        keyword=info.keyword,
        params=named,
    )
    try:
        response = await execute_keyword(session_id, request)
    except HTTPException as e:
        return _err(str(e.detail), status=e.status_code)
    except Exception as e:
        return _err(f"keyword execution failed: {e}", status=500)
    return _ok(response.model_dump() if hasattr(response, "model_dump") else response)
