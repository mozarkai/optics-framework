"""Unit tests for the Optics MCP server (`optics mcp`).

No device/driver: `expose_api.execute_keyword` / `create_session` /
`run_keyword_endpoint` are mocked. Covers dynamic per-keyword tool registration
(str-typed schemas with an injected session_id, internal `located` excluded),
the read-only observers being surfaced as resources instead of tools, param
stringification at the ExecuteRequest boundary, error translation to ToolError,
and the screenshot resource returning raw image bytes.

`fastmcp` is an optional extra; the whole module skips when it is absent.
"""
import asyncio
import base64
import json
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

from optics_framework.common import expose_api  # noqa: E402
from optics_framework.helper import mcp_server  # noqa: E402

pytestmark = pytest.mark.white_box


def _run(coro):
    return asyncio.run(coro)


def _exec_response(result):
    return expose_api.ExecutionResponse(
        execution_id="x", status="SUCCESS", data={expose_api.KEY_RESULT: result}
    )


# --- registration ----------------------------------------------------------


def test_action_keywords_registered_as_tools():
    server = mcp_server.build_server()
    names = {t.name for t in _run(server._list_tools())}
    for expected in (
        "start_session",
        "terminate_session",
        "press_element",
        "enter_text",
        "swipe",
        "assert_presence",
        "get_interactive_elements",  # tool (takes filter_config) AND resource
        "get_driver_session_id",
    ):
        assert expected in names


def test_readonly_observers_excluded_from_tools():
    server = mcp_server.build_server()
    names = {t.name for t in _run(server._list_tools())}
    for observer in mcp_server._RESOURCE_ONLY_KEYWORDS:
        assert observer not in names


def test_state_resources_registered():
    server = mcp_server.build_server()
    resources = {str(r.uri) for r in _run(server._list_resources())}
    templates = {t.uri_template for t in _run(server._list_resource_templates())}
    assert "optics://keywords" in resources
    assert {
        "optics://session/{session_id}/screenshot",
        "optics://session/{session_id}/source",
        "optics://session/{session_id}/elements",
        "optics://session/{session_id}/screen_elements",
    } <= templates


# --- schema shape ----------------------------------------------------------


def test_keyword_tool_schema_is_string_typed_with_session_id():
    server = mcp_server.build_server()
    press = next(t for t in _run(server._list_tools()) if t.name == "press_element")
    props = press.parameters["properties"]
    # session_id is injected and required alongside the keyword's required args.
    assert "session_id" in props
    assert set(press.parameters["required"]) >= {"session_id", "element"}
    # Internal self-healing param must not be exposed.
    assert "located" not in props
    # Every property is a string (optional ones are anyOf[string, null]).
    for name, schema in props.items():
        types = {schema["type"]} if "type" in schema else {
            sub.get("type") for sub in schema.get("anyOf", [])
        }
        assert "string" in types, f"{name} is not string-typed: {schema}"


# --- dispatch & coercion ---------------------------------------------------


def test_tool_dispatch_forwards_stringified_params():
    server = mcp_server.build_server()
    mock = AsyncMock(return_value=_exec_response("pressed"))
    with patch.object(expose_api, "execute_keyword", new=mock):
        async def go():
            async with Client(server) as client:
                return await client.call_tool(
                    "press_element",
                    {"session_id": "sess-1", "element": "Login", "repeat": "2"},
                )

        result = _run(go())

    session_id, request = mock.call_args.args[0], mock.call_args.args[1]
    assert session_id == "sess-1"
    assert request.keyword == "press_element"
    # Defaults are forwarded too; every value is a str.
    assert request.params["element"] == "Login"
    assert request.params["repeat"] == "2"
    assert all(isinstance(v, str) for v in request.params.values())
    # Result text is delivered to the client.
    assert any(getattr(c, "text", None) == "pressed" for c in result.content)


def test_non_string_scalar_is_rejected_by_schema():
    """The str contract is enforced at the boundary (no silent coercion)."""
    server = mcp_server.build_server()
    with patch.object(expose_api, "execute_keyword", new=AsyncMock()):
        async def go():
            async with Client(server) as client:
                await client.call_tool(
                    "press_element",
                    {"session_id": "s", "element": "L", "repeat": 2},  # int, not str
                )

        with pytest.raises(ToolError):
            _run(go())


def test_stringify_params_helper():
    out = mcp_server._stringify_params(
        {"a": 1, "b": None, "c": ["x", 2], "d": True}
    )
    assert out == {"a": "1", "c": ["x", "2"], "d": "True"}  # None dropped


# --- error translation -----------------------------------------------------


def test_http_error_translates_to_tool_error():
    from fastapi import HTTPException

    server = mcp_server.build_server()
    mock = AsyncMock(side_effect=HTTPException(status_code=404, detail="session not found"))
    with patch.object(expose_api, "execute_keyword", new=mock):
        async def go():
            async with Client(server) as client:
                await client.call_tool(
                    "press_element", {"session_id": "missing", "element": "X"}
                )

        with pytest.raises(ToolError, match="session not found"):
            _run(go())


# --- resources -------------------------------------------------------------


def test_keywords_resource_returns_catalog():
    server = mcp_server.build_server()

    async def go():
        async with Client(server) as client:
            return await client.read_resource("optics://keywords")

    contents = _run(go())
    catalog = json.loads(contents[0].text)
    slugs = {entry["keyword_slug"] for entry in catalog}
    assert "press_element" in slugs and "capture_screenshot" in slugs


def test_screenshot_resource_returns_raw_bytes():
    server = mcp_server.build_server()
    raw = b"\x89PNG\r\n\x1a\nfake-bytes"
    b64 = base64.b64encode(raw).decode()
    mock = AsyncMock(return_value=_exec_response(b64))
    with patch.object(expose_api, "run_keyword_endpoint", new=mock):
        async def go():
            async with Client(server) as client:
                return await client.read_resource(
                    "optics://session/sess-1/screenshot"
                )

        contents = _run(go())

    assert mock.call_args.args[1] == "capture_screenshot"
    blob = contents[0]
    assert base64.b64decode(blob.blob) == raw


def test_decode_screenshot_handles_data_url():
    raw = b"abc"
    data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
    assert mcp_server._decode_screenshot(data_url) == raw


def test_screenshot_tool_returns_rendered_image():
    """The screenshot tool yields image/png ImageContent (renders inline)."""
    server = mcp_server.build_server()
    raw = b"\x89PNG\r\n\x1a\nfake"
    b64 = base64.b64encode(raw).decode()
    mock = AsyncMock(return_value=_exec_response(b64))
    with patch.object(expose_api, "run_keyword_endpoint", new=mock):
        async def go():
            async with Client(server) as client:
                return await client.call_tool("screenshot", {"session_id": "s1"})

        result = _run(go())

    assert mock.call_args.args[1] == "capture_screenshot"
    image = result.content[0]
    assert image.type == "image" and image.mimeType == "image/png"
    assert base64.b64decode(image.data) == raw


# --- optional dependency guard ---------------------------------------------


def test_require_fastmcp_raises_when_missing():
    with patch.object(mcp_server, "_FASTMCP_IMPORT_ERROR", ImportError("no fastmcp")):
        with pytest.raises(RuntimeError, match="optics-framework\\[mcp\\]"):
            mcp_server._require_fastmcp()
