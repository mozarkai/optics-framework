"""Unit tests for the Optics MCP server (`optics mcp`).

No device or driver: `expose_api` and `read_screen` are replaced by a scripted
screen. The core toolset is tested as a client sees it; the per-keyword tests run
against `toolset="full"`.

`fastmcp` is an optional extra; the whole module skips when it is absent.
"""
import asyncio
import base64
import inspect
import json
import os
import subprocess
import sys
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("fastmcp")

from fastapi import HTTPException
from fastmcp import Client
from fastmcp.exceptions import ToolError

from optics_framework.common import expose_api
from optics_framework.common.logging_config import (
    internal_logger,
    logging_manager,
)
from optics_framework.helper import mcp_server

pytestmark = pytest.mark.white_box


def _run(coro):
    return asyncio.run(coro)


def _exec_response(result):
    return expose_api.ExecutionResponse(
        execution_id="x", status="SUCCESS", data={expose_api.KEY_RESULT: result}
    )


# --- registration ----------------------------------------------------------


def test_action_keywords_registered_as_tools():
    server = mcp_server.build_server("full")
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
    server = mcp_server.build_server("full")
    names = {t.name for t in _run(server._list_tools())}
    for observer in mcp_server._RESOURCE_ONLY_KEYWORDS:
        assert observer not in names


def test_state_resources_registered():
    server = mcp_server.build_server("full")
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
    server = mcp_server.build_server("full")
    press = next(t for t in _run(server._list_tools()) if t.name == "press_element")
    props = press.parameters["properties"]
    # session_id is injected and required alongside the keyword's required args.
    assert "session_id" in props
    assert set(press.parameters["required"]) >= {"session_id", "element"}
    # Only declared keyword params are exposed (no injection plumbing).
    assert "located" not in props
    # Every property is a string (optional ones are anyOf[string, null]).
    for name, schema in props.items():
        types = {schema["type"]} if "type" in schema else {
            sub.get("type") for sub in schema.get("anyOf", [])
        }
        assert "string" in types, f"{name} is not string-typed: {schema}"


# --- dispatch & coercion ---------------------------------------------------


def test_tool_dispatch_forwards_stringified_params():
    server = mcp_server.build_server("full")
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
    server = mcp_server.build_server("full")
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


# --- deep dispatch: real named-param path (regression for #454) ------------

_SELF_HEALING_KEYWORDS = [
    ("press_element", {"element": "X"}),
    ("press_checkbox", {"element": "X"}),
    ("press_radio_button", {"element": "X"}),
    ("swipe_from_element", {"element": "X", "direction": "up", "swipe_length": "50"}),
    ("scroll_from_element", {"element": "X", "direction": "down", "scroll_length": "50"}),
    ("enter_text", {"element": "X", "text": "T"}),
    ("enter_number", {"element": "X", "number": "5"}),
    ("clear_element_text", {"element": "X"}),
    ("get_text", {"element": "X"}),
]


@pytest.mark.parametrize("slug, params", _SELF_HEALING_KEYWORDS)
def test_mcp_dispatch_binds_real_self_healing_signatures(slug, params):
    from optics_framework.api.action_keyword import ActionKeyword
    from optics_framework.common import expose_api

    method = getattr(ActionKeyword, slug)
    engine = MagicMock()
    engine.execute = AsyncMock(return_value=_exec_response("ok"))
    _run(expose_api._execute_keyword_with_fallback(
        engine, "sess", slug, params, method, MagicMock()
    ))
    built = engine.execute.await_args.args[0].params
    assert "located" not in built
    inspect.signature(method).bind(None, *built)


@pytest.mark.parametrize("slug, params", _SELF_HEALING_KEYWORDS)
def test_mcp_tool_to_dispatch_binds_real_signatures(slug, params):
    server = mcp_server.build_server("full")
    engine = MagicMock()
    engine.execute = AsyncMock(return_value=_exec_response("ok"))

    session = MagicMock()
    session.event_queue.put = AsyncMock()
    session.request_template_overrides = {}
    from optics_framework.api.action_keyword import ActionKeyword

    session.optics.build = MagicMock(return_value=MagicMock())

    class _RealRegistry:
        def __init__(self):
            self.keyword_map = {slug: getattr(ActionKeyword, slug)}

        def register(self, instance):
            pass

    real_registry = _RealRegistry()

    with patch.object(expose_api.session_manager, "get_session", return_value=session), \
            patch.object(expose_api, "KeywordRegistry", return_value=real_registry), \
            patch.object(expose_api, "FlowControl", return_value=MagicMock()), \
            patch.object(expose_api, "ExecutionEngine", return_value=engine):
        async def go():
            async with Client(server) as client:
                await client.call_tool(slug, {"session_id": "s1", **params})

        _run(go())

    built = engine.execute.await_args.args[0].params
    inspect.signature(getattr(ActionKeyword, slug)).bind(None, *built)


# --- error translation -----------------------------------------------------


def test_http_error_translates_to_tool_error():

    server = mcp_server.build_server("full")
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
    server = mcp_server.build_server("full")

    async def go():
        async with Client(server) as client:
            return await client.read_resource("optics://keywords")

    contents = _run(go())
    catalog = json.loads(contents[0].text)
    slugs = {entry["keyword_slug"] for entry in catalog}
    assert "press_element" in slugs and "capture_screenshot" not in slugs


def test_screenshot_resource_returns_raw_bytes():
    server = mcp_server.build_server("full")
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
    server = mcp_server.build_server("full")
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
    assert image.type == "image" and image.mime_type == "image/png"
    assert base64.b64decode(image.data) == raw


# --- optional dependency guard ---------------------------------------------


def test_require_fastmcp_raises_when_missing():
    with (
        patch.object(mcp_server, "_FASTMCP_IMPORT_ERROR", ImportError("no fastmcp")),
        pytest.raises(RuntimeError, match="optics-framework\\[mcp\\]"),
    ):
        mcp_server._require_fastmcp()


def test_stdio_logging_never_blocks_on_unread_stderr(monkeypatch):
    from rich.console import Console

    read_fd, write_fd = os.pipe()
    handler = logging_manager.internal_console_handler
    monkeypatch.setattr(handler, "console", Console(file=os.fdopen(write_fd, "w")))
    monkeypatch.setattr(mcp_server, "build_server", MagicMock)
    original_handlers = list(internal_logger.handlers)
    try:
        mcp_server.run_mcp_server("stdio")
        flood = threading.Thread(
            target=lambda: [internal_logger.warning("x" * 1024) for _ in range(256)],
            daemon=True,
        )
        flood.start()
        flood.join(timeout=5)
        assert not flood.is_alive(), "logging blocked on a full stderr pipe"
    finally:

        def drain():
            while os.read(read_fd, 65536):
                pass

        drainer = threading.Thread(target=drain, daemon=True)
        drainer.start()  # releases whichever writer is blocked on the pipe
        logging_manager.stop_listeners()
        internal_logger.handlers[:] = original_handlers
        handler.console.file.close()
        drainer.join(timeout=5)
        os.close(read_fd)


_EXIT_CHILD = """
from unittest.mock import MagicMock
from optics_framework.common.logging_config import internal_logger
from optics_framework.helper import mcp_server

server = MagicMock()
server.run.side_effect = lambda **_: [internal_logger.warning("x" * 1024) for _ in range(256)]
mcp_server.build_server = lambda *_: server
mcp_server.run_mcp_server("stdio")
"""


def test_stdio_server_exits_with_unread_stderr():
    child = subprocess.Popen(
        [sys.executable, "-c", _EXIT_CHILD],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert child.wait(timeout=60) == 0
    finally:
        child.kill()
        child.communicate()


# --- core toolset ------------------------------------------------------------

SIGN_IN = "xpath=//button[normalize-space()='Sign in']"
LOGIN = [
    {"label": "Optics Store", "cls": "h1", "act": [], "loc": "xpath=//h1[normalize-space()='Optics Store']"},
    {"label": "Email", "cls": "label", "act": ["tap"], "loc": "xpath=//label[normalize-space()='Email']"},
    {"label": "Email", "cls": "input", "act": ["tap", "input"], "rid": "email", "loc": "css=#email"},
    {"label": "Password", "cls": "label", "act": ["tap"], "loc": "xpath=//label[normalize-space()='Password']"},
    {"label": "Password", "cls": "input", "act": ["tap", "input"], "rid": "password",
     "loc": 'css=[data-testid="password"]'},
    {"label": "Password for all users: secret", "cls": "p", "act": [],
     "loc": "xpath=//p[normalize-space()='Password for all users: secret']"},
    {"label": "Sign in", "cls": "button", "act": ["tap"], "loc": SIGN_IN},
]
WELCOME = LOGIN + [
    {"label": "Welcome, a@b.c", "cls": "p", "act": [], "loc": "xpath=//p[normalize-space()='Welcome, a@b.c']"},
    {"label": "Orders", "cls": "a", "act": ["tap"], "loc": 'css=a[href="orders.html"]'},
]
ORDERS = [
    {"label": "Your orders", "cls": "h1", "act": [], "loc": "xpath=//h1[normalize-space()='Your orders']"},
    {"label": "Reorder", "cls": "button", "act": [], "loc": "xpath=//button[normalize-space()='Reorder']"},
]


class Device:
    """A scripted screen and keyword engine standing in for a real session."""

    def __init__(self):
        self.screen = list(LOGIN)
        self.calls = []
        self.handlers = {}

    def read(self, _observer, _png=None):
        return None if self.screen is None else [dict(e) for e in self.screen]

    async def execute(self, session_id, request):
        self.calls.append((session_id, request.keyword, request.params))
        handler = self.handlers.get(request.keyword)
        result = handler(request.params) if handler else None
        if asyncio.iscoroutine(result):
            result = await result
        return _exec_response(result)


def _add_session(sid):
    session = MagicMock()
    session.optics.build = MagicMock(return_value=MagicMock())
    expose_api.session_manager.sessions[sid] = session
    return session


@pytest.fixture
def device(monkeypatch):
    for name in (mcp_server.ENV_SESSION_IDLE_S, mcp_server.ENV_CALL_TIMEOUT_S,
                 mcp_server.ENV_TOOLSET, mcp_server.ENV_ALLOW_COORDINATE_TAPS):
        monkeypatch.delenv(name, raising=False)
    fake = Device()
    monkeypatch.setattr(mcp_server, "read_screen", fake.read)
    monkeypatch.setattr(expose_api, "execute_keyword", fake.execute)
    monkeypatch.setattr(mcp_server, "_SETTLE_PAUSE_S", 0)
    monkeypatch.setattr(mcp_server, "_POLL_S", 0)
    mcp_server._STATES.clear()
    saved = dict(expose_api.session_manager.sessions)
    expose_api.session_manager.sessions.clear()
    _add_session("s1")
    yield fake
    mcp_server._STATES.clear()
    expose_api.session_manager.sessions.clear()
    expose_api.session_manager.sessions.update(saved)


def _session(*calls, toolset="core"):
    """Run (tool, args) calls in one client session; returns each CallToolResult."""

    async def go():
        async with Client(mcp_server.build_server(toolset)) as client:
            return [await client.call_tool(name, args, raise_on_error=False) for name, args in calls]

    return _run(go())


def _text(result):
    return "\n".join(block.text for block in result.content if block.type == "text")


def _tools(toolset="core"):
    async def go():
        async with Client(mcp_server.build_server(toolset)) as client:
            return await client.list_tools_mcp()

    return _run(go())


def test_core_toolset_lists_ten_tools_in_order():
    assert [t.name for t in _tools().tools] == [
        "start_session", "terminate_session", "observe", "tap", "type_text", "scroll",
        "press_key", "launch_app", "check", "run_keyword",
    ]


@pytest.mark.parametrize("toolset", ["core", "full"])
def test_every_tool_carries_all_four_annotations(toolset):
    for tool in _tools(toolset).tools:
        hints = tool.annotations
        assert None not in (
            hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint, hints.open_world_hint
        ), tool.name


def test_annotations_mark_reads_and_destructive_keywords():
    full = {t.name: t.annotations for t in _tools("full").tools}
    assert full["get_text"].read_only_hint and not full["press_element"].read_only_hint
    assert full["execute_script"].destructive_hint and full["run_keyword"].destructive_hint
    assert full["observe"].read_only_hint and full["check"].read_only_hint


def test_every_core_param_is_described():
    for tool in _tools().tools:
        for name, prop in tool.input_schema["properties"].items():
            assert prop.get("description"), f"{tool.name}.{name}"


def test_core_tools_and_instructions_fit_their_budgets():
    listing = _tools()
    assert len(listing.model_dump_json(by_alias=True, exclude_none=True)) <= 10_000
    assert len(mcp_server.SERVER_INSTRUCTIONS) <= 1_800


def test_toolset_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv(mcp_server.ENV_TOOLSET, "full")
    names = {t.name for t in _run(mcp_server.build_server()._list_tools())}
    assert {"press_element", "observe", "run_keyword", "screenshot"} <= names
    with pytest.raises(ValueError, match="core, full"):
        mcp_server.build_server("everything")


def test_start_session_with_app_returns_the_first_screen(device, monkeypatch):
    device.screen = []
    device.handlers["launch_app"] = lambda _: setattr(device, "screen", list(LOGIN))
    create = AsyncMock(return_value=expose_api.SessionResponse(session_id="s1", driver_id="d1"))
    monkeypatch.setattr(expose_api, "create_session", create)
    (result,) = _session(("start_session", {"driver": "playwright", "app": "file:///index.html"}))
    text = _text(result)
    assert text.startswith("session s1 (playwright) · idle lease 900 s\n")
    assert 'e7 button "Sign in" [tap]' in text
    assert device.calls[-1] == ("s1", "launch_app", {"app_identifier": "file:///index.html"})
    assert result.structured_content["session_id"] == "s1"


def test_tap_by_ref_sends_the_row_locator(device):
    _session(("observe", {}), ("tap", {"target": "e7"}))
    assert device.calls == [("s1", "press_element", {"element": [SIGN_IN]})]


def test_stale_ref_is_an_error_that_lists_the_current_rows(device):
    def go_to_orders(_):
        device.screen = list(ORDERS)

    device.handlers["press_element"] = go_to_orders
    _, _, stale = _session(("observe", {}), ("tap", {"target": "e7"}), ("tap", {"target": "e7"}))
    assert stale.is_error
    assert _text(stale).split("\n")[:3] == [
        'e7 ("Sign in" button) is not on the current screen.',
        "Next: Pick a ref from the current screen.",
        "screen 0e9e83 · 2 rows",
    ]
    assert 'e8 h1 "Your orders"' in _text(stale)


def test_tap_reports_what_changed_and_verifies_the_expectation(device):
    device.handlers["press_element"] = lambda _: setattr(device, "screen", list(WELCOME))
    (result,) = _session(("tap", {"target": "e7", "expect_present": ["Welcome"]}))
    assert not result.is_error
    assert _text(result).split("\n") == [
        'tap e7 "Sign in" -> ok · verified: pass',
        'check present "Welcome": met',
        "screen 4436bc · changed (+2 -0 ~0)",
        '+ e8 p "Welcome, a@b.c"',
        '+ e9 a "Orders" [tap]',
    ]
    assert result.structured_content["verified"] == "pass"
    assert result.structured_content["target"]["ladder"] == [SIGN_IN]


def test_unmet_expectation_on_an_unchanged_screen_fails_without_a_tool_error(device):
    (result,) = _session(("tap", {"target": "e7", "expect_present": ["Welcome"]}))
    assert not result.is_error
    assert _text(result).split("\n") == [
        'tap e7 "Sign in" -> ok · verified: fail',
        'check present "Welcome": not met',
        "screen 21d99e · unchanged",
    ]


def test_tap_without_expectations_is_unverified(device):
    (result,) = _session(("tap", {"target": "e7", "screen": "none"}))
    assert _text(result) == (
        'tap e7 "Sign in" -> ok · verified: unknown (add expect_present/expect_absent to verify)'
    )


def test_check_is_a_verdict_not_an_error(device):
    device.screen = list(WELCOME)
    (result,) = _session(("check", {"absent": ["Welcome"], "timeout_s": 0}))
    assert not result.is_error
    assert result.structured_content["passed"] is False
    assert _text(result).split("\n")[:2] == ["check -> not passed", 'absent "Welcome": not met']


def test_check_rejects_a_locator_in_absent(device):
    (result,) = _session(("check", {"absent": ["css=#welcome"]}))
    assert result.is_error
    assert result.structured_content["error"]["kind"] == "invalid_params"


def test_keyword_that_logs_an_error_and_returns_is_a_silent_failure(device):
    device.handlers["scroll"] = lambda _: internal_logger.error("Unknown scroll direction")
    (result,) = _session(("scroll", {"direction": "down"}))
    assert result.is_error
    assert _text(result).split("\n")[:2] == [
        "scroll down -> FAIL · verified: fail",
        "[E0401] the keyword logged an error and did not raise: Unknown scroll direction.",
    ]


def test_lost_driver_session_clears_the_state_and_shows_no_rows(device, monkeypatch):
    def lost(_):
        raise HTTPException(status_code=500, detail={"code": "Code.E0106", "message": "Driver session is no longer active"})

    device.handlers["press_element"] = lost
    terminate = MagicMock(side_effect=lambda sid: expose_api.session_manager.sessions.pop(sid, None))
    monkeypatch.setattr(expose_api.session_manager, "terminate_session", terminate)
    (result,) = _session(("tap", {"target": "e7"}))
    assert result.is_error
    assert _text(result) == "[E0106] the driver session ended.\nNext: The driver session ended; start a new session."
    assert "s1" not in mcp_server._STATES
    terminate.assert_called_once_with("s1")


def test_not_found_names_the_ladder_and_lists_the_rows(device):
    def missing(_):
        raise HTTPException(status_code=404, detail={"code": "Code.E0201", "message": "Element not found"})

    device.handlers["press_element"] = missing
    (result,) = _session(("tap", {"target": "e7"}))
    lines = _text(result).split("\n")
    assert lines[:4] == [
        'tap e7 "Sign in" -> FAIL · verified: fail',
        f"[E0201] not found: tried {SIGN_IN}.",
        "Next: Pick a ref from the current screen.",
        "screen 21d99e · 7 rows",
    ]


def test_two_taps_on_one_session_do_not_overlap(device):
    events = []

    async def slow(_):
        events.append("enter")
        await asyncio.sleep(0.05)
        events.append("exit")

    device.handlers["press_element"] = slow

    async def go():
        async with Client(mcp_server.build_server()) as client:
            await asyncio.gather(
                client.call_tool("tap", {"target": "e7"}), client.call_tool("tap", {"target": "e7"}),
            )

    _run(go())
    assert events == ["enter", "exit", "enter", "exit"]


def test_timed_out_call_leaves_the_session_busy_until_it_ends(device, monkeypatch):
    monkeypatch.setenv(mcp_server.ENV_CALL_TIMEOUT_S, "0.1")
    gate = asyncio.Event()

    async def stuck(_):
        await gate.wait()

    device.handlers["press_element"] = stuck

    async def go():
        async with Client(mcp_server.build_server()) as client:
            first = await client.call_tool("tap", {"target": "e7"}, raise_on_error=False)
            second = await client.call_tool("observe", {}, raise_on_error=False)
            gate.set()
            await asyncio.sleep(0.05)
            third = await client.call_tool("observe", {}, raise_on_error=False)
            return first, second, third

    first, second, third = _run(go())
    assert _text(first).split("\n")[0] == "Did not finish in 0.1 s."
    assert second.structured_content["error"]["kind"] == "busy"
    assert not third.is_error


def test_session_id_can_be_left_out_only_with_one_session(device):
    (one,) = _session(("observe", {}))
    assert not one.is_error
    _add_session("s2")
    (two,) = _session(("observe", {}))
    assert two.is_error
    assert _text(two).split("\n")[0] == "session_id is required; open sessions: s1, s2."


def test_idle_session_is_ended_on_the_next_call(device, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(mcp_server, "_now", lambda: clock[0])
    delete = AsyncMock(side_effect=lambda sid: expose_api.session_manager.sessions.pop(sid))
    monkeypatch.setattr(expose_api, "delete_session", delete)

    async def go():
        async with Client(mcp_server.build_server()) as client:
            await client.call_tool("observe", {})
            clock[0] = 901.0
            return await client.call_tool("observe", {"session_id": "s1"}, raise_on_error=False)

    result = _run(go())
    delete.assert_awaited_once_with("s1")
    assert _text(result).split("\n")[0] == "No session 's1' (ended after 900 s idle, or never started)."


def test_run_keyword_lists_the_real_params_of_a_bad_call(device):
    (result,) = _session(("run_keyword", {"keyword": "enter_text", "params": {"field": "e3", "text": "x"}}))
    assert result.is_error
    assert _text(result).split("\n")[:3] == [
        'enter_text: no param "field"; params: element, text, index',
        "Params: element, text, index?",
        'Example: run_keyword(keyword="enter_text", params={"element": "e7", "text": "<text>"})',
    ]


def test_run_keyword_rejects_hidden_params(device):
    (result,) = _session(("run_keyword", {"keyword": "press_element", "params": {"element": "e7", "event_name": "x"}}))
    assert result.is_error
    assert _text(result).startswith('press_element: no param "event_name"')
    assert device.calls == []


def test_run_keyword_expands_an_element_ref_and_returns_the_value(device):
    device.handlers["get_text"] = lambda _: "Password for all users: secret"
    (result,) = _session(("run_keyword", {"keyword": "get_text", "params": {"element": "e6"}}))
    assert device.calls == [
        ("s1", "get_text", {"element": ["xpath=//p[normalize-space()='Password for all users: secret']"]})
    ]
    assert "result: Password for all users: secret" in _text(result)
    assert result.structured_content["result"] == "Password for all users: secret"


def test_run_keyword_clamps_keyword_timeouts(device):
    _session(("run_keyword", {"keyword": "assert_presence", "params": {"elements": "Welcome", "timeout_str": "600"}}))
    assert device.calls[0][2] == {"elements": "Welcome", "timeout_str": "60"}


def test_coordinate_keywords_need_the_env_var(monkeypatch):
    def keyword_enum():
        tool = next(t for t in _tools().tools if t.name == "run_keyword")
        return set(tool.input_schema["properties"]["keyword"]["enum"])

    monkeypatch.delenv(mcp_server.ENV_ALLOW_COORDINATE_TAPS, raising=False)
    assert not {"press_by_percentage", "press_by_coordinates", "swipe"} & keyword_enum()
    monkeypatch.setenv(mcp_server.ENV_ALLOW_COORDINATE_TAPS, "1")
    assert {"press_by_percentage", "press_by_coordinates", "swipe"} <= keyword_enum()


def test_steps_resource_holds_replayable_rows_without_the_password(device):
    failures = iter([True])

    def tap(_):
        if next(failures, False):
            raise HTTPException(status_code=404, detail={"code": "Code.E0201", "message": "Element not found"})
        device.screen = list(WELCOME)

    device.handlers["press_element"] = tap

    async def go():
        async with Client(mcp_server.build_server()) as client:
            await client.call_tool("type_text", {"target": "e3", "text": "a@b.c"})
            await client.call_tool("type_text", {"target": "e5", "text": "hunter2"})
            await client.call_tool("tap", {"target": "e7"}, raise_on_error=False)
            await client.call_tool("tap", {"target": "e7"})
            await client.call_tool("check", {"present": ["e9", "Welcome"], "absent": ["Wrong password"]})
            return await client.read_resource("optics://session/s1/steps")

    payload = json.loads(_run(go())[0].text)
    assert [(s["verb"], s["ok"]) for s in payload["steps"]] == [
        ("type_text", True), ("type_text", True), ("tap", False), ("tap", True), ("check", True),
    ]
    assert payload["modules_csv"] == (
        "module_name,module_step,param_1,param_2\n"
        "recorded,Enter Text,${email_input},a@b.c\n"
        "recorded,Enter Text,${password_input},${password}\n"
        "recorded,Press Element,${sign_in_button}\n"
        'recorded,Assert Presence,"css=a[href=""orders.html""]",rule=all\n'
    )
    assert payload["elements_csv"] == (
        "Element_Name,Element_ID,Element_ID_2,Element_ID_3\n"
        "email_input,css=#email,Email\n"
        'password_input,"css=[data-testid=""password""]",css=#password,Password\n'
        "password,<redacted>\n"
        "sign_in_button,xpath=//button[normalize-space()='Sign in'],Sign in\n"
    )
    assert "hunter2" not in json.dumps(payload)
    assert "absent checks are not recordable as keywords" in payload["notes"]
    assert '"Welcome" is not the whole text of a row, so it was not recorded' in payload["notes"]


def test_full_toolset_element_params_accept_refs(device):
    _session(("observe", {}), ("press_element", {"session_id": "s1", "element": "e7"}), toolset="full")
    assert device.calls == [("s1", "press_element", {"element": [SIGN_IN], "repeat": "1", "offset_x": "0",
                                                      "offset_y": "0", "index": "0", "aoi_x": "0", "aoi_y": "0",
                                                      "aoi_width": "100", "aoi_height": "100"})]


def test_steps_resource_redacts_every_way_of_typing_into_a_secure_field(device):
    device.screen = [{"label": "", "cls": "XCUIElementTypeSecureTextField", "act": ["tap", "input"],
                      "loc": '//XCUIElementTypeSecureTextField[@name="field_1"]'}]

    async def go():
        async with Client(mcp_server.build_server()) as client:
            await client.call_tool("type_text", {"target": "e1", "text": "hunter2"})
            await client.call_tool("run_keyword", {"keyword": "enter_number", "params": {"element": "e1", "number": "4321"}})
            await client.call_tool("tap", {"target": "e1"})
            await client.call_tool("run_keyword", {"keyword": "enter_text_using_keyboard", "params": {"text_input": "hunter2"}})
            return await client.read_resource("optics://session/s1/steps")

    payload = json.loads(_run(go())[0].text)
    assert len(payload["steps"]) == 4
    assert "hunter2" not in json.dumps(payload) and "4321" not in json.dumps(payload)
    assert payload["elements_csv"].count("<redacted>") == 3


def test_steps_resource_redacts_typing_after_a_tap_on_a_secret_locator(device):
    async def go():
        async with Client(mcp_server.build_server()) as client:
            await client.call_tool("tap", {"target": "css=#password"})
            await client.call_tool("run_keyword", {"keyword": "enter_text_using_keyboard", "params": {"text_input": "hunter2"}})
            return await client.read_resource("optics://session/s1/steps")

    payload = json.loads(_run(go())[0].text)
    assert "hunter2" not in json.dumps(payload)
    assert payload["elements_csv"].count("<redacted>") == 1


def _steps_after(*calls):
    async def go():
        async with Client(mcp_server.build_server()) as client:
            for name, args in calls:
                await client.call_tool(name, args)
            return await client.read_resource("optics://session/s1/steps")

    return json.loads(_run(go())[0].text)


_RAW_SECURE = '//XCUIElementTypeSecureTextField[@name="field_1"]'


@pytest.mark.parametrize("calls", [
    [("type_text", {"target": _RAW_SECURE, "text": "hunter2"})],
    [("tap", {"target": _RAW_SECURE}),
     ("run_keyword", {"keyword": "enter_text_using_keyboard", "params": {"text_input": "hunter2"}})],
])
def test_steps_resource_redacts_typing_into_a_secure_field_named_by_locator(device, calls):
    device.screen = [{"label": "", "cls": "XCUIElementTypeSecureTextField", "act": ["tap", "input"],
                      "loc": _RAW_SECURE}]
    payload = _steps_after(("observe", {}), *calls)
    assert "hunter2" not in json.dumps(payload)


def test_steps_resource_redacts_typing_into_a_field_showing_a_masked_value(device):
    device.screen = [{"label": "••••••••", "cls": "android.widget.EditText", "act": ["tap", "input"],
                      "rid": "com.x:id/field2", "loc": '//android.widget.EditText[@resource-id="com.x:id/field2"]'}]
    payload = _steps_after(("type_text", {"target": "e1", "text": "hunter2", "clear_first": True}))
    assert "hunter2" not in json.dumps(payload)
    assert "••••" not in payload["elements_csv"]


_MASKED_FIELD = {"label": "••••••••", "cls": "android.widget.EditText", "act": ["tap", "input"],
                 "rid": "com.x:id/field2", "loc": "//android.widget.EditText[@resource-id='com.x:id/field2']"}


@pytest.mark.parametrize("target", [
    '//android.widget.EditText[@resource-id="com.x:id/field2"]',
    "xpath=//*[@resource-id='com.x:id/field2']",
])
def test_steps_resource_redacts_a_masked_field_named_by_another_spelling_of_its_locator(device, target):
    device.screen = [{"label": "a@b.c", "cls": "android.widget.EditText", "act": ["tap", "input"],
                      "rid": "com.x:id/field1", "loc": "//android.widget.EditText[@resource-id='com.x:id/field1']"},
                     _MASKED_FIELD]
    payload = _steps_after(("observe", {}), ("type_text", {"target": target, "text": "hunter2"}))
    assert "hunter2" not in json.dumps(payload)


@pytest.mark.parametrize("screen", [
    [{"text": "", "act": ["tap", "input"], "bounds": [0, 0, 10, 10],
      "extra": {"class": "android.widget.EditText", "resource-id": "com.x:id/field2", "password": True}}],
    [{"label": "Password", "cls": "android.widget.TextView", "act": []},
     {**_MASKED_FIELD, "label": ""}],
])
def test_steps_resource_redacts_an_empty_password_field(device, screen):
    device.screen = screen
    payload = _steps_after(("observe", {}), ("type_text", {"target": f"e{len(screen)}", "text": "hunter2"}))
    assert "hunter2" not in json.dumps(payload)


@pytest.mark.parametrize(("label", "rid"), [
    ("Shipping address", "com.x:id/address"), ("Passenger name", "com.x:id/compass_city"),
])
def test_steps_resource_keeps_a_value_typed_into_an_ordinary_field(device, label, rid):
    device.screen = [{"label": label, "cls": "android.widget.EditText", "act": ["tap", "input"], "rid": rid,
                      "loc": f"//android.widget.EditText[@resource-id='{rid}']"}]
    payload = _steps_after(("type_text", {"target": "e1", "text": "221B Baker Street"}))
    assert "221B Baker Street" in payload["modules_csv"]
    assert not payload["notes"]


def test_steps_resource_records_the_indexed_rung_of_a_shared_label(device):
    device.screen = [{"label": "Add", "cls": "button", "act": ["tap"]}, {"label": "Add", "cls": "button", "act": ["tap"]}]
    payload = _steps_after(("observe", {}), ("tap", {"target": "e2"}))
    assert payload["elements_csv"] == (
        "Element_Name,Element_ID,Element_ID_2\n"
        'add_button,"xpath=(//button[normalize-space()=""Add""])[2]",Add\n'
    )


@pytest.mark.parametrize("call", [
    ("type_text", {"target": "e3", "text": "a@b.c", "expect_present": ["a@b.c"]}),
    ("run_keyword", {"keyword": "enter_text", "params": {"element": "e3", "text": "a@b.c"}, "expect_present": ["a@b.c"]}),
])
def test_typed_value_as_expectation_is_not_judged(device, call):
    (result,) = _session(call)
    assert result.structured_content["verified"] == "unknown"
    assert 'check present "a@b.c": not checked, a typed value is not in the rows' in _text(result)


def test_expectation_met_before_the_action_proves_nothing(device):
    (result,) = _session(("tap", {"target": "e7", "expect_present": ["Sign in"]}))
    assert result.structured_content["verified"] == "unknown"


def test_unreadable_screen_after_an_action_is_never_a_pass(device):
    device.handlers["press_element"] = lambda _: setattr(device, "screen", None)
    tap, check = _session(
        ("observe", {}),
        ("tap", {"target": "e7", "expect_absent": ["Sign in"]}),
        ("check", {"absent": ["Sign in"], "timeout_s": 0}),
    )[1:]
    assert not tap.is_error and tap.structured_content["verified"] == "unknown"
    assert "The screen could not be read. Expectations were not checked." in _text(tap)
    assert check.is_error and _text(check).startswith("The screen could not be read.")
    assert "s1" in mcp_server._STATES


def test_failed_navigation_keeps_the_session(device):
    def unreachable(_):
        raise HTTPException(status_code=500, detail={"code": "Code.E0102", "message": "net::ERR_NAME_NOT_RESOLVED"})

    device.handlers["launch_app"] = unreachable
    (result,) = _session(("launch_app", {"app": "https://typo.invalid"}))
    assert result.is_error and result.structured_content["error"]["kind"] == "other"
    assert "s1" in mcp_server._STATES and "s1" in expose_api.session_manager.sessions


def test_act_failure_on_an_earlier_result_is_not_a_silent_failure(device):
    from optics_framework.common.logging_config import execution_logger

    def press(_):
        internal_logger.error("Action 'press_element' failed with XPathStrategy: stale element")
        execution_logger.info("Action 'press_element' succeeded with TextElementStrategy")

    device.handlers["press_element"] = press
    (result,) = _session(("tap", {"target": "e7"}))
    assert not result.is_error
    assert result.structured_content["target"]["strategy"] == "Text"


def test_run_keyword_refuses_a_ref_without_a_locator(device):
    device.screen = [*LOGIN, {"label": "Help", "cls": "span", "act": ["tap"]}]
    (result,) = _session(("observe", {}), ("run_keyword", {"keyword": "press_element", "params": {"element": "e8"}}))[1:]
    assert result.is_error and result.structured_content["error"]["kind"] == "no_locator"
    assert device.calls == []


def test_keyword_catalog_lists_what_run_keyword_accepts():
    async def go():
        async with Client(mcp_server.build_server()) as client:
            listing = await client.list_tools_mcp()
            return listing, await client.read_resource("optics://keywords")

    listing, contents = _run(go())
    tool = next(t for t in listing.tools if t.name == "run_keyword")
    catalog = {entry["keyword_slug"] for entry in json.loads(contents[0].text)}
    assert catalog == set(tool.input_schema["properties"]["keyword"]["enum"])


def test_check_shares_one_deadline_and_gives_each_locator_a_look(device, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(mcp_server, "_now", lambda: clock[0])

    def assert_presence(params):
        clock[0] += float(params["timeout_str"])
        return False

    device.handlers["assert_presence"] = assert_presence
    _session(("check", {"present": ["css=#a", "css=#b", "css=#c"], "timeout_s": 30}))
    assert [call[2]["timeout_str"] for call in device.calls] == ["30", "1", "1"]


def test_sideways_scroll_scrolls_the_page_on_a_web_driver(device, monkeypatch):
    create = AsyncMock(return_value=expose_api.SessionResponse(session_id="s1", driver_id="d1"))
    monkeypatch.setattr(expose_api, "create_session", create)
    _session(("start_session", {"driver": "playwright"}), ("scroll", {"direction": "left"}))
    assert device.calls[-1] == ("s1", "scroll", {"direction": "left"})
