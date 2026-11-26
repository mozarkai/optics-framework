import asyncio
import base64
import json
import os
import pathlib
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client
from fastmcp.exceptions import ToolError

from optics_framework.common import expose_api
from optics_framework.common.agent_core import RESOURCE_KEYWORDS
from optics_framework.common.logging_config import (
    internal_logger,
    logging_manager,
)
from optics_framework.helper import feedback, mcp_server


async def _no_screenshot():
    raise AssertionError("the test screen has no screenshot")


@pytest.fixture
def owned(monkeypatch):
    monkeypatch.setattr(mcp_server, "_caller", lambda: "client")

    def own(*sids: str) -> None:
        for sid in sids:
            agent = SimpleNamespace(expand_refs=lambda params: params, refs=SimpleNamespace(latest=None))
            slot = mcp_server._Slot(agent, _no_screenshot, owner="client")
            monkeypatch.setitem(mcp_server._STATES, sid, slot)

    return own


def _run(coro):
    return asyncio.run(coro)


def test_readonly_observers_excluded_from_tools():
    server = mcp_server.build_server("full")
    names = {t.name for t in _run(server._list_tools())}
    for observer in RESOURCE_KEYWORDS:
        assert observer not in names


def test_http_error_translates_to_tool_error(owned):
    owned("missing")
    server = mcp_server.build_server("full")

    async def go():
        async with Client(server) as client:
            await client.call_tool("press_element", {"session_id": "missing", "element": "X"})

    call = go()
    with pytest.raises(ToolError, match="Session not found"):
        _run(call)


def test_decode_screenshot_handles_data_url():
    raw = b"abc"
    data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
    assert mcp_server._decode_screenshot(data_url) == raw


def test_stdio_logging_never_blocks_on_unread_stderr(monkeypatch):
    from rich.console import Console

    read_fd, write_fd = os.pipe()
    handler = logging_manager.internal_console_handler
    monkeypatch.setattr(handler, "console", Console(file=os.fdopen(write_fd, "w")))
    monkeypatch.setattr(mcp_server, "build_server", lambda _toolset: SimpleNamespace(run=lambda **_: None))
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
        internal_logger.handlers[:] = original_handlers  # NOSONAR restored before the pipe below closes, not at teardown
        handler.console.file.close()
        drainer.join(timeout=5)
        os.close(read_fd)


def test_the_app_argument_names_the_app_of_a_desktop_session():
    assert mcp_server._session_caps("appium", "B", {"platformName": "Mac"})["app"] == "B"


def test_an_unknown_driver_says_how_to_start_a_desktop_app():
    async def go():
        async with Client(mcp_server.build_server("full")) as client:
            return await client.call_tool("start_session", {"driver": "xa11y", "app": "Notes"}, raise_on_error=False)

    reply = _run(go())
    assert reply.is_error
    assert 'platformName' in reply.content[0].text


S1 = "0b6f9c1e-6a8e-4d3b-9b8e-2f4c1a7d5e90"
STEPS = f"optics://session/{S1}/steps"


class _Desktop:
    """A driver whose screen is a list of compact rows; it records each keyword it runs."""

    def __init__(self, rows):
        self.rows, self.ran, self.ended, self.gate, self.effect = rows, [], [], None, None

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
def desktop(monkeypatch, fake_time):
    from optics_framework.common.agent_core import AgentSession, keyword_catalog

    monkeypatch.setattr(mcp_server, "_caller", lambda: "client")
    screen = _Desktop([_row("Calendar", "window"), _row("Add Event", "button")])

    async def end(session_id):
        screen.ended.append(session_id)
    monkeypatch.setattr(expose_api, "delete_session", end)
    keywords = keyword_catalog(mcp_server._keyword_functions(), coordinates=True)
    agent = AgentSession(screen.read, screen.run, keywords)
    monkeypatch.setitem(mcp_server._STATES, S1, mcp_server._Slot(agent, _no_screenshot, owner="client"))
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


@pytest.mark.parametrize(("call", "shown"), [
    (("tap", {"target": "Add Event"}), "expect_present"),
    (("observe", {"query": "Save"}), 'no row of 2 holds "Save"'),
], ids=["unchanged_action_asks_for_an_expectation", "empty_query_counts_rows"])
def test_what_the_agent_sees_says_what_to_do_next(desktop, call, shown):
    reply, = _calls(call)
    assert shown in _shown(reply)


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

    assert desktop.ended == [S1]
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


def test_rows_with_a_screenshot_give_each_box_in_the_image(desktop, monkeypatch):
    import cv2
    import numpy as np

    png = cv2.imencode(".png", np.zeros((40, 60, 3), dtype=np.uint8))[1].tobytes()
    async def shot():
        return png
    monkeypatch.setattr(mcp_server._STATES[S1], "shot", shot)

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
    monkeypatch.setattr(mcp_server._STATES[S1], "owner", "")
    _calls(("tap", {"target": "Add Event"}))

    mcp_server._STATES.clear()
    mcp_server._ENDED.clear()
    tapped, = _calls(("tap", {"target": "Add Event", "session_id": S1}))

    assert [step["keyword"] for step in _steps_of(S1)["steps"]] == ["Press Element"]
    assert f"has ended. The MCP server stopped before it ended the session. Its 1 recorded steps are at {STEPS}" in _shown(tapped)


def test_a_typed_password_reaches_the_steps_and_the_saved_file_only_as_a_variable(desktop, monkeypatch):
    monkeypatch.setattr(mcp_server, "_caller", lambda: "")
    monkeypatch.setattr(mcp_server._STATES[S1], "owner", "")
    desktop.rows.append(_row("", "text_field", act=("tap", "input"), hint="Password"))

    _calls(("type_text", {"target": "Password", "text": "hunter2-secret"}))
    steps = _steps_of(S1)
    saved = (pathlib.Path(os.environ["OPTICS_HOME"]) / "mcp" / "steps" / f"{S1}.json").read_text()

    assert "${password_value}" in steps["modules_csv"]
    assert "<redacted>" not in steps["modules_csv"]
    assert "password_value,<redacted>" in steps["elements_csv"]
    assert any("<redacted>" in note and "ENV:" in note for note in steps["notes"])
    assert "hunter2-secret" not in json.dumps(steps) + saved


def test_a_reply_that_nests_a_long_screen_keeps_the_rows_that_fit():
    screen = "\n".join(f'button "Song number {n} {"x" * 90}" [tap]' for n in range(100))

    reply = mcp_server._reply(["session s1 (xa11y)", screen])

    assert 'button "Song number 0 ' in _shown(reply)
    assert "more lines cut" in _shown(reply)


def test_a_call_without_a_session_id_after_the_end_names_the_ended_session(desktop):
    _, tapped = _calls(("terminate_session", {}), ("tap", {"target": "Add Event"}))

    assert f"Session '{S1}' has ended. It was ended by terminate_session." in _shown(tapped)
    assert STEPS in _shown(tapped)


def test_a_failed_save_keeps_the_reply_and_leaves_no_temp_file(desktop, monkeypatch):
    monkeypatch.setattr(mcp_server, "_caller", lambda: "")
    monkeypatch.setattr(mcp_server._STATES[S1], "owner", "")
    def disk_full(*_paths):
        raise OSError("disk full")
    monkeypatch.setattr(mcp_server.os, "replace", disk_full)

    tapped, = _calls(("tap", {"target": "Add Event"}))

    assert _shown(tapped).startswith('tap e2 "Add Event" -> ok')
    assert not list((pathlib.Path(os.environ["OPTICS_HOME"]) / "mcp" / "steps").glob("*"))


@pytest.mark.parametrize(("variable", "listed"), [(None, True), ("OPTICS_NO_FEEDBACK", False), ("DO_NOT_TRACK", False)])
def test_send_feedback_is_a_core_tool_unless_the_user_opts_out(monkeypatch, variable, listed):
    if variable:
        monkeypatch.setenv(variable, "1")

    async def names():
        async with Client(mcp_server.build_server()) as client:
            return {tool.name for tool in await client.list_tools()}
    assert ("send_feedback" in asyncio.run(names())) is listed


def test_send_feedback_posts_a_scrubbed_report_and_never_asks_for_a_retry(monkeypatch):
    posted = []

    def post(url, json, **_):
        posted.append((url, json))
        return SimpleNamespace(status_code=429)
    monkeypatch.setattr(feedback.requests, "post", post)

    async def send():
        async with Client(mcp_server.build_server()) as client:
            return await client.call_tool("send_feedback", {
                "kind": "broken", "impact": "blocked", "task": "sign in",
                "description": "tap did nothing; mail me at dev@example.com",
            })
    reply = asyncio.run(send())
    (url, body), = posted
    assert url == "https://optics-framework.org/api/feedback"
    assert body["description"] == "tap did nothing; mail me at [email]"
    assert body["transport"] == "stdio"
    assert "Carry on with your task" in reply.content[0].text
