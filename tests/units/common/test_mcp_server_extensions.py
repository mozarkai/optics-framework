"""Unit tests for the MCP server's onboarding/discovery/recording/suite tools.

Device-less: `expose_api` functions and `mcp_diagnostics` device probes are
mocked. Exercises tool registration, the driver-aware source defaults, the
recording hook on keyword calls, and the record -> save -> replay round trip
through a temp workspace.
"""
import asyncio
import base64
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from optics_framework.common import expose_api  # noqa: E402
from optics_framework.common.config_handler import Config, DependencyConfig  # noqa: E402
from optics_framework.helper import mcp_authoring, mcp_diagnostics, mcp_server  # noqa: E402

pytestmark = pytest.mark.white_box


def _run(coro):
    return asyncio.run(coro)


def _exec_response(result):
    return expose_api.ExecutionResponse(
        execution_id="x", status="SUCCESS", data={expose_api.KEY_RESULT: result}
    )


def _png_b64(width: int, height: int) -> str:
    raw = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )
    return base64.b64encode(raw).decode("ascii")


def _fake_session():
    config = Config(
        driver_sources=[
            {
                "appium": DependencyConfig(
                    enabled=True,
                    capabilities={"platformName": "Android", "udid": "emulator-5554"},
                )
            }
        ],
        elements_sources=[{"appium_page_source": DependencyConfig(enabled=True)}],
    )
    return SimpleNamespace(config=config)


@pytest.fixture(autouse=True)
def _clean_recorders():
    mcp_server.mcp_authoring.RECORDERS._recorders.clear()
    yield
    mcp_server.mcp_authoring.RECORDERS._recorders.clear()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(mcp_server.ENV_WORKSPACE, str(tmp_path))
    return tmp_path


async def _call(server, name, args):
    async with Client(server) as client:
        return await client.call_tool(name, args)


def _content_json(result):
    for chunk in result.content:
        text = getattr(chunk, "text", None)
        if text:
            return json.loads(text)
    return getattr(result, "data", None)


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #
def test_extension_tools_registered():
    server = mcp_server.build_server()
    names = {t.name for t in _run(server._list_tools())}
    expected = {
        "doctor", "list_devices", "list_available_sources", "list_sessions",
        "session_info", "get_screen_size", "find_elements", "get_current_app",
        "list_installed_apps", "start_recording", "stop_recording",
        "recording_status", "list_recorded_steps", "edit_step", "remove_step",
        "clear_recording", "save_test_case", "list_test_cases", "get_test_case",
        "delete_test_case", "save_suite", "list_suites", "get_suite",
        "run_test_case", "run_suite", "export_optics_project", "import_optics_project",
    }
    assert expected <= names
    # keyword tools still present alongside the new ones
    assert "press_element" in names


# --------------------------------------------------------------------------- #
# onboarding / discovery
# --------------------------------------------------------------------------- #
def test_start_session_applies_default_element_sources():
    server = mcp_server.build_server()
    create = AsyncMock(return_value=expose_api.SessionResponse(session_id="s1", driver_id="d1"))
    with patch.object(expose_api, "create_session", new=create):
        _run(_call(server, "start_session", {"driver": "appium"}))
    config = create.call_args.args[0]
    assert config.elements_sources == [
        "appium_find_element",
        "appium_page_source",
        "appium_screenshot",
    ]


def test_start_session_keeps_explicit_element_sources():
    server = mcp_server.build_server()
    create = AsyncMock(return_value=expose_api.SessionResponse(session_id="s1"))
    with patch.object(expose_api, "create_session", new=create):
        _run(_call(server, "start_session", {"driver": "appium", "elements_sources": ["appium_page_source"]}))
    assert create.call_args.args[0].elements_sources == ["appium_page_source"]


def test_list_available_sources_tool():
    server = mcp_server.build_server()
    result = _run(_call(server, "list_available_sources", {"driver": "appium"}))
    payload = _content_json(result)
    assert payload["elements_sources"]["sources"][0] == "appium_find_element"


def test_doctor_tool(monkeypatch):
    server = mcp_server.build_server()
    monkeypatch.setattr(
        mcp_diagnostics, "run_doctor", lambda *a, **k: {"ok": True, "version": "x", "checks": []}
    )
    result = _run(_call(server, "doctor", {}))
    assert _content_json(result)["ok"] is True


def test_list_devices_tool(monkeypatch):
    server = mcp_server.build_server()
    monkeypatch.setattr(
        mcp_diagnostics, "list_connected_devices", lambda: [{"udid": "e", "platform": "android"}]
    )
    result = _run(_call(server, "list_devices", {}))
    assert _content_json(result) == [{"udid": "e", "platform": "android"}]


# --------------------------------------------------------------------------- #
# reliability / discovery
# --------------------------------------------------------------------------- #
def test_get_screen_size_tool():
    server = mcp_server.build_server()
    observe = AsyncMock(return_value=_exec_response(_png_b64(1080, 2400)))
    with patch.object(expose_api.session_manager, "get_session", return_value=_fake_session()), \
            patch.object(expose_api, "run_keyword_endpoint", new=observe):
        result = _run(_call(server, "get_screen_size", {"session_id": "s1"}))
    assert _content_json(result) == {"width": 1080, "height": 2400}


def test_find_elements_tool_filters_and_compacts():
    server = mcp_server.build_server()
    elements = [
        {
            "text": "Login",
            "bounds": {"x1": 0, "y1": 0, "x2": 100, "y2": 40},
            "xpath": "//a",
            "extra": {"resource-id": "com.x:id/login", "class": "android.widget.Button",
                      "tag": "android.widget.Button", "clickable": "true"},
        },
        {
            "text": "Cancel",
            "bounds": {"x1": 0, "y1": 500, "x2": 100, "y2": 540},
            "xpath": "//b",
            "extra": {"class": "android.widget.TextView", "tag": "android.widget.TextView"},
        },
    ]
    observe = AsyncMock(return_value=_exec_response(elements))
    with patch.object(expose_api.session_manager, "get_session", return_value=_fake_session()), \
            patch.object(expose_api, "run_keyword_endpoint", new=observe):
        result = _run(_call(server, "find_elements", {"session_id": "s1", "clickable": "true"}))
    payload = _content_json(result)
    assert payload["total"] == 1
    assert payload["elements"][0]["id"] == "com.x:id/login"
    assert payload["elements"][0]["center"] == [50, 20]


def test_get_current_app_rejects_non_android():
    server = mcp_server.build_server()
    ios_session = SimpleNamespace(
        config=Config(
            driver_sources=[{"appium": DependencyConfig(enabled=True, capabilities={"platformName": "iOS"})}]
        )
    )
    with patch.object(expose_api.session_manager, "get_session", return_value=ios_session):
        with pytest.raises(ToolError, match="Android-only"):
            _run(_call(server, "get_current_app", {"session_id": "s1"}))


def test_list_sessions_and_session_info():
    server = mcp_server.build_server()
    session = _fake_session()
    with patch.object(expose_api.session_manager, "sessions", {"s1": session}), \
            patch.object(expose_api.session_manager, "get_session", return_value=session):
        listed = _content_json(_run(_call(server, "list_sessions", {})))
        info = _content_json(_run(_call(server, "session_info", {"session_id": "s1"})))
    assert listed[0]["session_id"] == "s1"
    assert listed[0]["drivers"] == ["appium"]
    assert info["platform"] == "android"
    assert info["device"] == "emulator-5554"


# --------------------------------------------------------------------------- #
# recording hook + save + replay round trip
# --------------------------------------------------------------------------- #
def test_recording_hook_captures_successful_keyword():
    server = mcp_server.build_server()
    execute = AsyncMock(return_value=_exec_response("ok"))
    with patch.object(expose_api.session_manager, "get_session", return_value=_fake_session()), \
            patch.object(expose_api, "execute_keyword", new=execute):
        _run(_call(server, "start_recording", {"session_id": "s1"}))
        _run(_call(server, "press_element", {"session_id": "s1", "element": "Login"}))
        status = _content_json(_run(_call(server, "recording_status", {"session_id": "s1"})))
    assert status["step_count"] == 1
    assert status["steps"][0]["keyword"] == "Press Element"
    assert status["steps"][0]["params"] == ["Login"]


def test_observer_keywords_not_recorded():
    server = mcp_server.build_server()
    execute = AsyncMock(return_value=_exec_response("data"))
    with patch.object(expose_api.session_manager, "get_session", return_value=_fake_session()), \
            patch.object(expose_api, "execute_keyword", new=execute):
        _run(_call(server, "start_recording", {"session_id": "s1"}))
        _run(_call(server, "get_text", {"session_id": "s1", "element": "x"}))
        status = _content_json(_run(_call(server, "recording_status", {"session_id": "s1"})))
    assert status["step_count"] == 0


def test_record_save_and_replay(workspace):
    server = mcp_server.build_server()
    execute = AsyncMock(return_value=_exec_response("ok"))
    with patch.object(expose_api.session_manager, "get_session", return_value=_fake_session()), \
            patch.object(expose_api, "execute_keyword", new=execute):
        _run(_call(server, "start_recording", {"session_id": "s1"}))
        _run(_call(server, "press_element", {"session_id": "s1", "element": "Login"}))
        _run(_call(server, "enter_text", {"session_id": "s1", "element": "Field", "text": "hi"}))
        saved = _content_json(_run(_call(server, "save_test_case", {"session_id": "s1", "name": "Flow"})))
        assert saved["step_count"] == 2
        # saving from the recording clears the buffer
        after = _content_json(_run(_call(server, "recording_status", {"session_id": "s1"})))
        assert after["step_count"] == 0
        # replay
        run = _content_json(_run(_call(server, "run_test_case", {"session_id": "s1", "name": "Flow"})))
    assert run["status"] == "SUCCESS"
    assert run["passed"] == 2 and run["failed"] == 0
    assert [s["keyword"] for s in run["steps"]] == ["Press Element", "Enter Text"]
    # the CSV project is on disk under the workspace
    assert os.path.isfile(os.path.join(str(workspace), "modules", "modules.csv"))


def test_run_test_case_failure_reports_screenshot(workspace):
    server = mcp_server.build_server()
    store = mcp_authoring.SuiteStore(str(workspace))
    store.save_test_case("Flow", [mcp_authoring.RecordedStep("press_element", ["Login"])])

    execute = AsyncMock(side_effect=HTTPException(status_code=500, detail="boom"))
    observe = AsyncMock(return_value=_exec_response(_png_b64(10, 10)))
    with patch.object(expose_api.session_manager, "get_session", return_value=_fake_session()), \
            patch.object(expose_api, "execute_keyword", new=execute), \
            patch.object(expose_api, "run_keyword_endpoint", new=observe):
        run = _content_json(_run(_call(server, "run_test_case", {"session_id": "s1", "name": "Flow"})))
    assert run["status"] == "FAIL"
    step = run["steps"][0]
    assert step["ok"] is False and "boom" in step["detail"]
    assert step["screenshot"] == _png_b64(10, 10)


def test_save_test_case_conflict_becomes_tool_error(workspace):
    server = mcp_server.build_server()
    steps_json = json.dumps([{"keyword": "Press Element", "params": ["A"]}])
    with patch.object(expose_api.session_manager, "get_session", return_value=_fake_session()):
        _run(_call(server, "save_test_case", {"session_id": "s1", "name": "Dup", "steps": steps_json}))
        with pytest.raises(ToolError, match="already exists"):
            _run(_call(server, "save_test_case", {"session_id": "s1", "name": "Dup", "steps": steps_json}))


def test_suite_save_list_and_run(workspace):
    server = mcp_server.build_server()
    store = mcp_authoring.SuiteStore(str(workspace))
    store.save_test_case("A", [mcp_authoring.RecordedStep("press_element", ["x"])])
    store.save_test_case("B", [mcp_authoring.RecordedStep("press_element", ["y"])])
    execute = AsyncMock(return_value=_exec_response("ok"))
    with patch.object(expose_api.session_manager, "get_session", return_value=_fake_session()), \
            patch.object(expose_api, "execute_keyword", new=execute):
        _run(_call(server, "save_suite", {"name": "S", "test_cases": '["A", "B"]'}))
        run = _content_json(_run(_call(server, "run_suite", {"session_id": "s1", "name": "S"})))
    assert run["status"] == "SUCCESS" and run["total"] == 2
    assert [c["test_case"] for c in run["test_cases"]] == ["A", "B"]
