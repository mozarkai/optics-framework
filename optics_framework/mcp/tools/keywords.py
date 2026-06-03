"""Auto-generated MCP tools, one per discoverable Optics keyword.

We re-introspect the API classes ourselves rather than using
`expose_api.discover_keywords()`'s `KeywordInfo` because that model
collapses "no default" and "default is literally None" into the same
`default=None`, which would make every `Optional[X] = None` parameter
look required. Working from `inspect.Signature` directly lets us use
`Parameter.empty` as the sentinel and lets us reach the real type
objects for typing.get_origin / get_args.

At call time we translate the MCP arguments into the FastAPI
`ExecuteRequest` shape and dispatch through `execute_keyword` so we
inherit the full fallback / event / template / KeywordRegistry
machinery for free.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
import types as builtin_types
import typing
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from mcp import types as mcp_types

from optics_framework.common.expose_api import (
    ExecuteRequest,
    execute_keyword,
)

_API_PKG = "optics_framework.api"

# Tool names must match `^[a-zA-Z0-9_-]+$`. We prefix with `optics_` to
# (a) namespace the surface so it can't collide with another MCP server
# a client mounts, and (b) make tool calls easy to spot in transcripts.
_TOOL_PREFIX = "optics_"

# Keyword slugs the LLM should NOT call directly via MCP — they're
# covered by the dedicated session / inspect tools, or are runner-only
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


@dataclass(frozen=True)
class _KeywordSpec:
    """Per-keyword spec carried from discovery into call_tool."""

    slug: str            # e.g. "press_element" — also the method name
    human: str           # e.g. "Press Element" — what execute_keyword expects
    signature: inspect.Signature
    doc: str


# ---------- discovery ------------------------------------------------------


def _humanize(slug: str) -> str:
    return " ".join(p.capitalize() for p in slug.replace("_", " ").split())


def _discover_specs() -> list[_KeywordSpec]:
    """Walk optics_framework.api.* and collect every public method."""
    out: list[_KeywordSpec] = []
    api_path = importlib.import_module(_API_PKG).__path__
    for _, modname, ispkg in pkgutil.iter_modules(api_path):
        if ispkg or modname.startswith("__"):
            continue
        mod = importlib.import_module(f"{_API_PKG}.{modname}")
        for _, cls in inspect.getmembers(mod, predicate=inspect.isclass):
            if cls.__module__ != mod.__name__:
                continue
            for mname, meth in inspect.getmembers(cls, predicate=inspect.isfunction):
                if mname.startswith("_") or mname.startswith("test"):
                    continue
                out.append(
                    _KeywordSpec(
                        slug=mname,
                        human=_humanize(mname),
                        signature=inspect.signature(meth),
                        doc=(inspect.getdoc(meth) or "").strip(),
                    )
                )
    return out


# ---------- annotation → JSON Schema --------------------------------------


_PRIMITIVE_SCHEMA: dict[type, dict[str, Any]] = {
    bool: {"type": "boolean"},  # checked before int! bool is a subclass of int
    int: {"type": "integer"},
    float: {"type": "number"},
    str: {"type": "string"},
    dict: {"type": "object"},
    list: {"type": "array"},
}


def _annotation_to_schema(annotation: Any) -> dict[str, Any]:
    """Translate a Python annotation object into a JSON Schema fragment.

    The Optics keyword annotation landscape (verified by scanning every
    public method on the API classes) is small:

        - bare primitive: `str`, `int`
        - typing.Any  (no annotation)
        - Optional[str], Optional[List[str]]
        - Union[str, List[Any]]  (the element + fallback-list shape)

    We map those to the most LLM-friendly JSON Schema we can. Anything
    we don't recognise falls back to `{"type": "string"}` because the
    Optics keyword body almost always coerces strings.
    """
    if annotation is inspect.Parameter.empty or annotation is typing.Any:
        return {"type": "string"}

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    # typing.Optional[X] == typing.Union[X, None]; typing.Union[A, B] is
    # the new-style PEP 604 `A | B` too.
    if origin is typing.Union or origin is builtin_types.UnionType:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _annotation_to_schema(non_none[0])
        # Mixed union (e.g. str | List[Any]): describe each arm.
        return {"anyOf": [_annotation_to_schema(a) for a in non_none]}

    # typing.List[X] / list[X]
    if origin in (list, typing.List):  # noqa: UP006 - typing.List handled for older syntax
        inner = args[0] if args else typing.Any
        return {"type": "array", "items": _annotation_to_schema(inner)}

    # typing.Dict[K, V] / dict[K, V]
    if origin in (dict, typing.Dict):  # noqa: UP006
        return {"type": "object"}

    # Plain class
    if isinstance(annotation, type):
        # bool must be checked before int because issubclass(bool, int)
        if annotation is bool:
            return {"type": "boolean"}
        for cls, schema in _PRIMITIVE_SCHEMA.items():
            if annotation is cls:
                return dict(schema)
        return {"type": "string"}

    return {"type": "string"}


# ---------- tool / schema construction ------------------------------------


def _build_schema(sig: inspect.Signature) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "session_id": {
            "type": "string",
            "description": "Session ID from optics_start_session.",
        },
    }
    required: list[str] = ["session_id"]
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        schema = _annotation_to_schema(param.annotation)
        if param.default is not inspect.Parameter.empty:
            # Optional: surface the actual default so the LLM can see it.
            # JSON Schema only allows JSON-serialisable defaults; fall
            # back to omitting the key if the default isn't.
            try:
                _json_default(param.default)
                schema["default"] = param.default
            except TypeError:
                pass
        else:
            required.append(name)
        properties[name] = schema
    return {"type": "object", "properties": properties, "required": required}


def _json_default(value: Any) -> None:
    """Raise TypeError if `value` is not JSON-serialisable. Cheap probe."""
    import json

    json.dumps(value)


def _build_tool(spec: _KeywordSpec) -> mcp_types.Tool:
    description = spec.doc or spec.human
    description = f"{spec.human}. {description}".strip()
    return mcp_types.Tool(
        name=_slug_to_tool_name(spec.slug),
        description=description,
        inputSchema=_build_schema(spec.signature),
    )


def _slug_to_tool_name(slug: str) -> str:
    return f"{_TOOL_PREFIX}{re.sub(r'[^a-zA-Z0-9_]', '_', slug)}"


def _tool_name_to_slug(name: str) -> str:
    return name[len(_TOOL_PREFIX):] if name.startswith(_TOOL_PREFIX) else name


# ---------- module-level discovery ----------------------------------------


def _build_registry() -> tuple[list[mcp_types.Tool], dict[str, _KeywordSpec]]:
    tools: list[mcp_types.Tool] = []
    index: dict[str, _KeywordSpec] = {}
    for spec in _discover_specs():
        if spec.slug in _BLOCKLIST:
            continue
        tools.append(_build_tool(spec))
        index[spec.slug] = spec
    return tools, index


TOOL_DEFINITIONS, _KEYWORD_INDEX = _build_registry()


# ---------- dispatch -------------------------------------------------------


def tool_names() -> set[str]:
    """Names of MCP tools handled by this module. Consumed by server.py."""
    return {t.name for t in TOOL_DEFINITIONS}


def _ok(payload: Any) -> list[mcp_types.TextContent]:
    import json
    return [mcp_types.TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


async def handle(name: str, arguments: dict[str, Any]) -> list[mcp_types.TextContent] | None:
    slug = _tool_name_to_slug(name)
    spec = _KEYWORD_INDEX.get(slug)
    if spec is None:
        return None  # not a keyword tool — let other handlers try

    session_id = arguments.get("session_id")
    if not session_id:
        # The JSON Schema marks session_id required, so the SDK's input
        # validator usually catches this first. We still raise here in
        # case validation is disabled or a client bypasses it.
        raise ValueError("session_id is required (call optics_start_session first)")

    named: dict[str, Any] = {k: v for k, v in arguments.items() if k != "session_id"}

    request = ExecuteRequest(mode="keyword", keyword=spec.human, params=named)
    try:
        response = await execute_keyword(session_id, request)
    except HTTPException as e:
        # The MCP SDK catches Exception and converts to isError=True with
        # str(e) as the message — preserve the FastAPI detail for the LLM.
        raise RuntimeError(f"{e.status_code}: {e.detail}") from e
    return _ok(response.model_dump() if hasattr(response, "model_dump") else response)
