"""Smoke tests for the optics-framework MCP layer.

These exercise the pure-Python pieces: schema generation, dispatch
plumbing, and the session/inspect handler wiring. The MCP transport
itself is tested by `mcp`'s own suite; we trust the SDK.

The async handlers are driven via `asyncio.run` so we don't need
pytest-asyncio in the test deps.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


pytestmark = pytest.mark.white_box


def _run(coro):
    return asyncio.run(coro)


# ---------- keywords.py: schema + dispatch ----------------------------------


def test_keyword_tool_definitions_have_session_id_required():
    from optics_framework.mcp.tools import keywords

    assert keywords.TOOL_DEFINITIONS, "expected discover_keywords() to yield at least one tool"
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


def test_json_type_for_annotation_string_mapping():
    from optics_framework.mcp.tools.keywords import _json_type_for

    assert _json_type_for("<class 'bool'>") == {"type": "boolean"}
    assert _json_type_for("<class 'int'>") == {"type": "integer"}
    assert _json_type_for("<class 'float'>") == {"type": "number"}
    assert _json_type_for("typing.List[str]")["type"] == "array"
    assert _json_type_for("typing.Dict[str, Any]") == {"type": "object"}
    assert _json_type_for("<class 'str'>") == {"type": "string"}
    assert _json_type_for("Any") == {"type": "string"}


def test_keyword_handle_returns_none_for_unknown_tool():
    from optics_framework.mcp.tools import keywords

    result = _run(keywords.handle("not_an_optics_tool", {}))
    assert result is None


def test_keyword_handle_errors_without_session_id():
    from optics_framework.mcp.tools import keywords

    name = keywords.TOOL_DEFINITIONS[0].name
    result = _run(keywords.handle(name, {}))
    assert result is not None
    payload = json.loads(result[0].text)
    assert payload["error"]
    assert payload["status"] == 400


def test_keyword_handle_dispatches_to_execute_keyword():
    from optics_framework.mcp.tools import keywords

    name = keywords.TOOL_DEFINITIONS[0].name
    slug = keywords._tool_name_to_slug(name)
    info = keywords._KEYWORD_INDEX[slug]

    class _Resp:
        def model_dump(self) -> dict[str, Any]:
            return {"execution_id": "abc", "status": "SUCCESS"}

    fake = AsyncMock(return_value=_Resp())
    with patch.object(keywords, "execute_keyword", fake):
        result = _run(keywords.handle(name, {"session_id": "s1"}))

    assert result is not None
    payload = json.loads(result[0].text)
    assert payload["status"] == "SUCCESS"
    fake.assert_awaited_once()
    args, _ = fake.call_args
    assert args[0] == "s1"
    forwarded = args[1]
    assert forwarded.mode == "keyword"
    assert forwarded.keyword == info.keyword


# ---------- session.py ------------------------------------------------------


def test_session_list_returns_active_session_ids():
    from optics_framework.mcp.tools import session as session_tools

    fake_sessions = {"s1": object(), "s2": object()}
    with patch.object(session_tools.session_manager, "sessions", fake_sessions, create=True):
        result = _run(session_tools.handle("optics_list_sessions", {}))

    payload = json.loads(result[0].text)
    assert set(payload["sessions"]) == {"s1", "s2"}


def test_session_terminate_requires_session_id():
    from optics_framework.mcp.tools import session as session_tools

    result = _run(session_tools.handle("optics_terminate_session", {}))
    payload = json.loads(result[0].text)
    assert payload["status"] == 400


def test_session_terminate_calls_session_manager():
    from optics_framework.mcp.tools import session as session_tools

    with patch.object(session_tools.session_manager, "terminate_session") as term:
        result = _run(
            session_tools.handle("optics_terminate_session", {"session_id": "abc"})
        )

    term.assert_called_once_with("abc")
    payload = json.loads(result[0].text)
    assert payload["status"] == "terminated"


# ---------- server.py: fan-out --------------------------------------------


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
