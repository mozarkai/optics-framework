"""Unit tests for the Optics MCP server (`optics mcp`).

No device/driver: `expose_api.execute_keyword` / `create_session` /
`run_keyword_endpoint` are mocked. Covers dynamic per-keyword tool registration
(str-typed schemas with an injected session_id), the read-only observers being
surfaced as resources instead of tools, param stringification at the
ExecuteRequest boundary, error translation to ToolError, and the screenshot
resource returning raw image bytes.

`fastmcp` is an optional extra; the whole module skips when it is absent.
"""
import asyncio
import base64
import inspect
import json
import os
import pathlib
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("fastmcp")

from fastapi import HTTPException
from fastmcp import Client
from fastmcp.exceptions import ToolError

from optics_framework.common import expose_api
from optics_framework.common.agent_core import RESOURCE_KEYWORDS
from optics_framework.common.logging_config import (
    internal_logger,
    logging_manager,
)
from optics_framework.helper import mcp_server

pytestmark = pytest.mark.white_box


@pytest.fixture
def owned(monkeypatch):
    """Make the given session ids belong to the test's MCP client."""
    monkeypatch.setattr(mcp_server, "_caller", lambda: "client")

    def own(*sids: str) -> None:
        for sid in sids:
            agent = SimpleNamespace(expand_refs=lambda params: params, refs=SimpleNamespace(latest=None))
            slot = mcp_server._Slot(agent, AsyncMock(), owner="client")
            monkeypatch.setitem(mcp_server._STATES, sid, slot)

    return own


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
    for observer in RESOURCE_KEYWORDS:
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


def test_tool_dispatch_forwards_stringified_params(owned):
    owned("sess-1")
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
def test_mcp_tool_to_dispatch_binds_real_signatures(slug, params, owned):
    owned("s1")
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


def test_http_error_translates_to_tool_error(owned):
    owned("missing")
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
    slugs = {entry["keyword"] for entry in catalog}
    assert "press_element" in slugs
    assert "capture_screenshot" not in slugs


def test_screenshot_resource_returns_raw_bytes(owned):
    owned("sess-1")
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


def test_screenshot_tool_returns_rendered_image(owned):
    """The screenshot tool yields image/png ImageContent (renders inline)."""
    owned("s1")
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
    assert image.type == "image"
    assert image.mime_type == "image/png"
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


@pytest.mark.parametrize("named", [{"app": "A"}, {"appium:app": "A"}, {"appName": "A"}, {"appium:options": {"app": "A"}}])
def test_the_app_argument_yields_to_an_app_in_the_capabilities(named):
    caps = mcp_server._session_caps("appium", "B", {"platformName": "Mac", **named})
    assert "B" not in caps.values()


def test_the_app_argument_names_the_app_of_a_desktop_session():
    assert mcp_server._session_caps("appium", "B", {"platformName": "Mac"})["app"] == "B"


def test_an_unknown_driver_says_how_to_start_a_desktop_app():
    server = mcp_server.build_server("full")
    error = HTTPException(status_code=500, detail={
        "code": "E0601", "message": "Module 'xa11y' not found in package 'optics_framework.engines.drivers'"})

    async def go():
        async with Client(server) as client:
            return await client.call_tool("start_session", {"driver": "xa11y", "app": "Notes"}, raise_on_error=False)

    with patch.object(expose_api, "create_session", new=AsyncMock(side_effect=error)):
        reply = _run(go())

    assert 'platformName' in reply.content[0].text


S1 = "0b6f9c1e-6a8e-4d3b-9b8e-2f4c1a7d5e90"
STEPS = f"optics://session/{S1}/steps"


class _Desktop:
    """A driver whose screen is a list of compact rows; it records each keyword it runs."""

    def __init__(self, rows):
        self.rows, self.ran, self.gate, self.effect = rows, [], None, None

    async def read(self, _png):
        return [dict(row, extra=dict(row["extra"])) for row in self.rows]

    async def run(self, keyword, params):
        self.ran.append(keyword)
        if self.gate:
            await self.gate.wait()
        if self.effect:
            self.effect()
        return None, "XPath"


def _row(text, cls, act=("tap",), area="", hint=""):
    extra = {"class": cls, **({"area": area} if area else {}), **({"hint": hint} if hint else {})}
    loc = f'//{cls}[@name="{hint or text}"]'
    return {"text": text, "act": list(act), "bounds": [0, 0, 10, 10], "loc": loc, "extra": extra}


@pytest.fixture
def desktop(monkeypatch):
    """One open session ``S1`` of this test's client over a fake desktop screen."""
    from optics_framework.common.agent_core import AgentSession, keyword_catalog

    monkeypatch.setattr(mcp_server, "_caller", lambda: "client")
    monkeypatch.setattr(expose_api, "delete_session", AsyncMock())
    screen = _Desktop([_row("Calendar", "window"), _row("Add Event", "button")])
    keywords = keyword_catalog(mcp_server._keyword_functions(), coordinates=True)
    agent = AgentSession(screen.read, screen.run, keywords)
    monkeypatch.setitem(mcp_server._STATES, S1, mcp_server._Slot(agent, AsyncMock(), owner="client"))
    monkeypatch.setitem(expose_api.session_manager.sessions, S1, object())
    return screen


def _calls(*calls):
    async def go():
        async with Client(mcp_server.build_server("core")) as client:
            return [await client.call_tool(tool, args, raise_on_error=False) for tool, args in calls]

    return _run(go())


def _shown(reply):
    """What a client that prefers structured content, as Claude Code does, puts in front of the agent."""
    if reply.structured_content is not None:
        return json.dumps(reply.structured_content)
    return "\n".join(c.text for c in reply.content if getattr(c, "text", None))


def test_rows_with_one_label_show_their_group_to_a_client_that_prefers_structured_content(desktop):
    desktop.rows += [_row("Add to Playlist", "button", area="Preet Re"), _row("Add to Playlist", "button", area="Co2")]

    seen, = _calls(("observe", {"query": "Add to Playlist"}))

    assert 'in "Preet Re"' in _shown(seen)
    assert 'in "Co2"' in _shown(seen)


def test_an_action_that_changed_nothing_asks_for_an_expectation_in_what_the_agent_sees(desktop):
    tapped, = _calls(("tap", {"target": "Add Event"}))

    assert "expect_present" in _shown(tapped)


def test_a_query_that_matches_nothing_says_how_many_rows_it_searched(desktop):
    seen, = _calls(("observe", {"query": "Save"}))

    assert 'no row of 2 holds "Save"' in _shown(seen)


def test_a_query_finds_a_field_by_its_name_when_it_shows_its_value(desktop):
    desktop.rows.append(_row("Optics Demo", "text_field", act=("tap", "input"), hint="Name"))

    seen, = _calls(("observe", {"query": "Name"}))

    assert '"Optics Demo"' in _shown(seen)


def test_the_steps_of_an_ended_session_stay_readable_by_their_owner(desktop, monkeypatch):
    async def go():
        async with Client(mcp_server.build_server("core")) as client:
            await client.call_tool("tap", {"target": "Add Event"})
            ended = await client.call_tool("terminate_session", {})
            steps = json.loads((await client.read_resource(STEPS))[0].text)
            monkeypatch.setattr(mcp_server, "_caller", lambda: "someone else")
            with pytest.raises(Exception, match="No session"):
                await client.read_resource(STEPS)
            return ended, steps

    ended, steps = _run(go())

    assert STEPS in _shown(ended)
    assert [step["keyword"] for step in steps["steps"]] == ["Press Element"]
    assert "Press Element" in steps["modules_csv"]


def test_a_check_names_each_unmet_expectation_and_shows_the_screen(desktop):
    checked, = _calls(("check", {"present": ["Calendar"], "absent": ["Add Event"], "timeout_s": 0}))

    assert 'present "Calendar": met' in _shown(checked)
    assert 'absent "Add Event": not met' in _shown(checked)
    assert 'button "Add Event"' in _shown(checked)


def test_batch_presses_a_key_by_the_name_press_key_takes(desktop):
    ran, = _calls(("batch", {"steps": [{"tool": "tap", "target": "Add Event"}, {"tool": "press_key", "key": "Escape"}]}))

    assert not ran.is_error, _shown(ran)
    assert desktop.ran == ["press_element", "press_keycode"]


def test_rows_with_a_screenshot_give_each_box_in_the_image(desktop):
    import cv2
    import numpy as np

    png = cv2.imencode(".png", np.zeros((40, 60, 3), dtype=np.uint8))[1].tobytes()
    mcp_server._STATES[S1].shot = AsyncMock(return_value=png)

    seen, = _calls(("observe", {"screenshot": True}))

    assert 'button "Add Event" [tap] @0,0,10,10' in _shown(seen)
    assert any(c.type == "image" for c in seen.content)


def _steps_of(sid):
    async def go():
        async with Client(mcp_server.build_server("core")) as client:
            return json.loads((await client.read_resource(f"optics://session/{sid}/steps"))[0].text)

    return _run(go())


def test_the_steps_of_a_local_session_outlive_a_server_restart(desktop, monkeypatch):
    monkeypatch.setattr(mcp_server, "_caller", lambda: "")
    mcp_server._STATES[S1].owner = ""
    _calls(("tap", {"target": "Add Event"}))

    mcp_server._STATES.clear()
    mcp_server._ENDED.clear()
    tapped, = _calls(("tap", {"target": "Add Event", "session_id": S1}))

    assert [step["keyword"] for step in _steps_of(S1)["steps"]] == ["Press Element"]
    assert f"has ended. The MCP server stopped before it ended the session. Its 1 recorded steps are at {STEPS}" in _shown(tapped)


def test_a_paused_session_stays_open_for_its_own_client(desktop):
    mcp_server._STATES[S1].last_used -= 10_000

    tapped, = _calls(("tap", {"target": "Add Event"}))

    assert not tapped.is_error, _shown(tapped)
    assert desktop.ran == ["press_element"]


def test_an_idle_session_ends_when_another_client_calls(desktop, monkeypatch):
    mcp_server._STATES[S1].last_used -= 10_000
    monkeypatch.setattr(mcp_server, "_caller", lambda: "someone else")
    _calls(("observe", {}))
    monkeypatch.setattr(mcp_server, "_caller", lambda: "client")

    tapped, = _calls(("tap", {"target": "Add Event", "session_id": S1}))

    assert "It ended after 900 s idle." in _shown(tapped)


def test_a_typed_password_reaches_the_steps_and_the_saved_file_only_as_a_variable(desktop, monkeypatch):
    monkeypatch.setattr(mcp_server, "_caller", lambda: "")
    mcp_server._STATES[S1].owner = ""
    desktop.rows.append(_row("", "text_field", act=("tap", "input"), hint="Password"))

    _calls(("type_text", {"target": "Password", "text": "hunter2-secret"}))
    steps = _steps_of(S1)
    saved = (pathlib.Path(os.environ["OPTICS_HOME"]) / "mcp" / "steps" / f"{S1}.json").read_text()

    assert "${password_value}" in steps["modules_csv"]
    assert "<redacted>" not in steps["modules_csv"]
    assert "password_value,<redacted>" in steps["elements_csv"]
    assert any("<redacted>" in note and "ENV:" in note for note in steps["notes"])
    assert "hunter2-secret" not in json.dumps(steps) + saved


def test_terminate_keeps_the_step_of_an_action_still_running(desktop):
    async def go():
        desktop.gate = asyncio.Event()
        async with Client(mcp_server.build_server("core")) as client:
            tap = asyncio.create_task(client.call_tool("tap", {"target": "Add Event"}))
            while not desktop.ran:
                await asyncio.sleep(0.01)
            end = asyncio.create_task(client.call_tool("terminate_session", {}))
            await asyncio.sleep(0.1)
            desktop.gate.set()
            await tap
            ended = await end
            steps = json.loads((await client.read_resource(STEPS))[0].text)
            return ended, steps

    ended, steps = _run(go())

    assert "Its 1 recorded steps" in _shown(ended)
    assert [step["keyword"] for step in steps["steps"]] == ["Press Element"]


def test_a_reply_that_nests_a_long_screen_keeps_the_rows_that_fit():
    screen = "\n".join(f'button "Song number {n} {"x" * 90}" [tap]' for n in range(100))

    reply = mcp_server._reply(["session s1 (xa11y)", screen])

    assert 'button "Song number 0 ' in _shown(reply)
    assert "more lines cut" in _shown(reply)


def test_a_call_without_a_session_id_after_the_end_names_the_ended_session(desktop):
    _, tapped = _calls(("terminate_session", {}), ("tap", {"target": "Add Event"}))

    assert f"Session '{S1}' has ended. It was ended by terminate_session." in _shown(tapped)
    assert STEPS in _shown(tapped)


def test_an_unverified_key_that_closed_a_draft_asks_for_an_expectation(desktop):
    desktop.effect = desktop.rows.pop

    pressed, = _calls(("press_key", {"key": "Escape"}))

    assert "verified: unknown (add expect_present/expect_absent to verify)" in _shown(pressed)


def test_a_failed_save_keeps_the_reply_and_leaves_no_temp_file(desktop, monkeypatch):
    monkeypatch.setattr(mcp_server, "_caller", lambda: "")
    mcp_server._STATES[S1].owner = ""
    monkeypatch.setattr(mcp_server.os, "replace", MagicMock(side_effect=OSError("disk full")))

    tapped, = _calls(("tap", {"target": "Add Event"}))

    assert _shown(tapped).startswith('tap e2 "Add Event" -> ok')
    assert not list((pathlib.Path(os.environ["OPTICS_HOME"]) / "mcp" / "steps").glob("*"))
