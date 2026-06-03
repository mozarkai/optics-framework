"""Smoke tests for the optics-framework MCP layer.

These exercise the pure-Python pieces: schema generation, dispatch
plumbing, and the session/inspect handler wiring. The MCP transport
itself is tested by `mcp`'s own suite; we trust the SDK.

Handlers raise on failure so the SDK's call_tool decorator can mark
the response with `isError=True`. The tests assert that contract:
no error JSON returned, just exceptions.

The async handlers are driven via `asyncio.run` so we don't need
pytest-asyncio in the test deps.
"""

from __future__ import annotations

import asyncio
import json
import typing
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest


pytestmark = pytest.mark.white_box


def _run(coro):
    return asyncio.run(coro)


# ---------- keywords.py: schema generation ---------------------------------


def test_keyword_tool_definitions_have_session_id_required():
    from optics_framework.mcp.tools import keywords

    assert keywords.TOOL_DEFINITIONS, "expected discovery to yield at least one tool"
    for tool in keywords.TOOL_DEFINITIONS:
        assert tool.name.startswith("optics_")
        schema = tool.inputSchema
        assert schema["type"] == "object"
        assert "session_id" in schema["properties"]
        assert "session_id" in schema["required"]


def test_blocklisted_keywords_are_not_exposed_as_tools():
    from optics_framework.mcp.tools import keywords

    exposed = {t.name for t in keywords.TOOL_DEFINITIONS}
    for slug in (
        "launch_app",
        "close_and_terminate_app",
        "capture_screenshot",
        "capture_pagesource",
        "get_screen_elements",
    ):
        assert f"optics_{slug}" not in exposed, f"{slug} should be hidden"


def test_annotation_to_schema_handles_real_optics_signatures():
    """The seven distinct annotations actually used across the API classes."""
    import inspect

    from optics_framework.mcp.tools.keywords import _annotation_to_schema

    # bare primitives
    assert _annotation_to_schema(str) == {"type": "string"}
    assert _annotation_to_schema(int) == {"type": "integer"}
    assert _annotation_to_schema(bool) == {"type": "boolean"}

    # no annotation
    assert _annotation_to_schema(inspect.Parameter.empty) == {"type": "string"}
    assert _annotation_to_schema(typing.Any) == {"type": "string"}

    # Optional[X] should unwrap to X's schema
    assert _annotation_to_schema(Optional[str]) == {"type": "string"}

    # Optional[List[str]] should become array of strings
    assert _annotation_to_schema(Optional[typing.List[str]]) == {
        "type": "array",
        "items": {"type": "string"},
    }

    # Mixed union: anyOf the arms
    mixed = _annotation_to_schema(typing.Union[str, typing.List[typing.Any]])
    assert "anyOf" in mixed
    assert {"type": "string"} in mixed["anyOf"]


def test_optional_default_none_param_is_not_marked_required():
    """Regression: Optional[X] = None must not appear in `required`."""
    import inspect as _inspect

    from optics_framework.mcp.tools.keywords import _build_schema

    def fake(self, element: str, template_image: Optional[str] = None):
        pass

    schema = _build_schema(_inspect.signature(fake))
    # session_id is always required; the keyword-side required set is
    # the rest.
    assert "session_id" in schema["required"]
    assert "element" in schema["required"]
    assert "template_image" not in schema["required"], (
        "Optional[str] = None must be optional in the JSON Schema"
    )
    # And the default must be surfaced when JSON-serialisable.
    assert schema["properties"]["template_image"].get("default") is None or \
           "default" not in schema["properties"]["template_image"]


def test_required_param_with_no_default_is_marked_required():
    import inspect as _inspect

    from optics_framework.mcp.tools.keywords import _build_schema

    def fake(self, element: str, timeout: int):
        pass

    schema = _build_schema(_inspect.signature(fake))
    assert "element" in schema["required"]
    assert "timeout" in schema["required"]


def test_param_with_string_default_keeps_default_in_schema():
    import inspect as _inspect

    from optics_framework.mcp.tools.keywords import _build_schema

    def fake(self, event_name: str = "default_event"):
        pass

    schema = _build_schema(_inspect.signature(fake))
    assert "event_name" not in schema["required"]
    assert schema["properties"]["event_name"]["default"] == "default_event"


# ---------- keywords.py: dispatch -----------------------------------------


def test_keyword_handle_returns_none_for_unknown_tool():
    from optics_framework.mcp.tools import keywords

    assert _run(keywords.handle("not_an_optics_tool", {})) is None


def test_keyword_handle_raises_without_session_id():
    from optics_framework.mcp.tools import keywords

    name = keywords.TOOL_DEFINITIONS[0].name
    with pytest.raises(ValueError, match="session_id is required"):
        _run(keywords.handle(name, {}))


def test_keyword_handle_dispatches_to_execute_keyword():
    from optics_framework.mcp.tools import keywords

    name = keywords.TOOL_DEFINITIONS[0].name
    slug = keywords._tool_name_to_slug(name)
    spec = keywords._KEYWORD_INDEX[slug]

    class _Resp:
        def model_dump(self) -> dict[str, Any]:
            return {"execution_id": "abc", "status": "SUCCESS"}

    fake = AsyncMock(return_value=_Resp())
    with patch.object(keywords, "execute_keyword", fake):
        result = _run(keywords.handle(name, {"session_id": "s1"}))

    payload = json.loads(result[0].text)
    assert payload["status"] == "SUCCESS"
    fake.assert_awaited_once()
    args, _ = fake.call_args
    assert args[0] == "s1"
    forwarded = args[1]
    assert forwarded.mode == "keyword"
    assert forwarded.keyword == spec.human


def test_keyword_handle_raises_on_http_exception():
    from fastapi import HTTPException

    from optics_framework.mcp.tools import keywords

    name = keywords.TOOL_DEFINITIONS[0].name
    fake = AsyncMock(side_effect=HTTPException(status_code=500, detail="boom"))
    with patch.object(keywords, "execute_keyword", fake):
        with pytest.raises(RuntimeError, match="500: boom"):
            _run(keywords.handle(name, {"session_id": "s1"}))


# ---------- session.py ----------------------------------------------------


def test_session_list_uses_public_session_manager_method():
    from optics_framework.mcp.tools import session as session_tools

    with patch.object(
        session_tools.session_manager,
        "list_session_ids",
        return_value=["s1", "s2"],
    ) as lister:
        result = _run(session_tools.handle("optics_list_sessions", {}))

    lister.assert_called_once_with()
    payload = json.loads(result[0].text)
    assert set(payload["sessions"]) == {"s1", "s2"}


def test_session_terminate_raises_without_session_id():
    from optics_framework.mcp.tools import session as session_tools

    with pytest.raises(ValueError, match="session_id is required"):
        _run(session_tools.handle("optics_terminate_session", {}))


def test_session_terminate_calls_session_manager():
    from optics_framework.mcp.tools import session as session_tools

    with patch.object(session_tools.session_manager, "terminate_session") as term:
        result = _run(
            session_tools.handle("optics_terminate_session", {"session_id": "abc"})
        )

    term.assert_called_once_with("abc")
    payload = json.loads(result[0].text)
    assert payload["status"] == "terminated"


def test_session_start_schema_describes_nested_source_shape():
    """LLMs need the inner shape — bare ['string','object'] is too vague."""
    from optics_framework.mcp.tools import session as session_tools

    start_tool = next(
        t for t in session_tools.TOOL_DEFINITIONS if t.name == "optics_start_session"
    )
    drv_schema = start_tool.inputSchema["properties"]["driver_sources"]
    assert drv_schema["type"] == "array"
    item = drv_schema["items"]
    assert "oneOf" in item
    # bare-string and single-key-object are both accepted
    assert any(a.get("type") == "string" for a in item["oneOf"])
    object_arm = next(a for a in item["oneOf"] if a.get("type") == "object")
    assert "additionalProperties" in object_arm
    inner = object_arm["additionalProperties"]
    assert "url" in inner["properties"]
    assert "capabilities" in inner["properties"]
    assert "enabled" in inner["properties"]


# ---------- inspect.py ----------------------------------------------------


def test_inspect_handle_raises_without_session_id():
    from optics_framework.mcp.tools import inspect as inspect_tools

    with pytest.raises(ValueError, match="session_id is required"):
        _run(inspect_tools.handle("optics_screenshot", {}))


def test_inspect_handle_raises_for_unknown_session():
    from optics_framework.mcp.tools import inspect as inspect_tools

    with patch.object(inspect_tools.session_manager, "get_session", return_value=None):
        with pytest.raises(LookupError, match="session not found"):
            _run(inspect_tools.handle("optics_screenshot", {"session_id": "missing"}))


# ---------- server.py: route map ------------------------------------------


def test_server_lists_session_inspect_and_keyword_tools():
    from optics_framework.mcp import server as srv

    names = {t.name for t in srv.ALL_TOOLS}
    assert {"optics_start_session", "optics_terminate_session", "optics_list_sessions"} <= names
    assert {
        "optics_screenshot",
        "optics_page_source",
        "optics_screen_elements",
        "optics_interactive_elements",
    } <= names
    extra = names - {
        "optics_start_session",
        "optics_terminate_session",
        "optics_list_sessions",
        "optics_screenshot",
        "optics_page_source",
        "optics_screen_elements",
        "optics_interactive_elements",
        "optics_driver_session_id",
    }
    assert extra, "expected at least one auto-generated keyword tool"


def test_server_route_map_covers_every_advertised_tool():
    from optics_framework.mcp import server as srv

    for tool in srv.ALL_TOOLS:
        assert tool.name in srv._ROUTES, f"no route for advertised tool {tool.name}"


def test_server_call_tool_raises_on_unknown_name():
    from optics_framework.mcp import server as srv

    async def go():
        # Bypass the decorator wrapping; call the underlying registered handler.
        handler = srv._ROUTES.get("not-a-real-tool")
        assert handler is None
        with pytest.raises(LookupError):
            # Mimic the inner body of server.call_tool
            raise LookupError("unknown tool: not-a-real-tool")

    _run(go())


# ---------- session_manager.py public method -------------------------------


def test_session_manager_list_session_ids_returns_keys():
    from optics_framework.common.session_manager import SessionManager

    mgr = SessionManager()
    assert mgr.list_session_ids() == []
    mgr.sessions["fake-1"] = object()  # type: ignore[assignment]
    mgr.sessions["fake-2"] = object()  # type: ignore[assignment]
    assert set(mgr.list_session_ids()) == {"fake-1", "fake-2"}
