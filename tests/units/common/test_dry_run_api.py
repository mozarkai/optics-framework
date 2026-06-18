"""Tests for the dry-run REST API and the reusable folder→suite loader.

These cover the pure pieces that don't need a live driver: the engine's
dry-run result surfacing, the inline/folder suite builders, and request-model
validation. End-to-end HTTP execution needs a configured driver, so it is left
to the integration smoke described in docs/usage/REST_API_usage.md.
"""
import asyncio
import os
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from optics_framework.common import expose_api
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.events import EventManager
from optics_framework.common.execution import DryRunExecutor
from optics_framework.common.models import TestCaseNode
from optics_framework.common.runner.test_runnner import Runner
from optics_framework.common.session_manager import Session
# Aliased: importing the model under its real name makes pytest try to collect
# it as a test class (it is named TestCaseResult).
from optics_framework.common.runner.printers import TestCaseResult as TCResult
from optics_framework.common.expose_api import (
    DryRunRequest,
    DryRunResponse,
    _build_dry_run_suite,
    _build_inline_dry_run_suite,
    _safe_project_path,
)
from optics_framework.helper.execute import load_suite_from_folder

client = TestClient(expose_api.app)

_INLINE_SUITE = {
    "driver_sources": [{"appium": {"enabled": True}}],
    "test_cases": {"TC1": ["Mod1"], "TC2": ["Mod1"]},
    "modules": {"Mod1": [["Sleep", ["1"]]]},
}

CONTACT_SAMPLE = (
    Path(__file__).resolve().parents[3]
    / "optics_framework"
    / "samples"
    / "contact"
)


class _FakeEventManager:
    """Records published events; publish_event is awaited by the executor."""

    def __init__(self):
        self.events = []

    async def publish_event(self, event):
        self.events.append(event)


class _FakePrinter:
    def __init__(self, state):
        self.test_state = state


class _FakeRunner:
    def __init__(self, state):
        self.result_printer = _FakePrinter(state)
        self.dry_run_called = False

    async def dry_run_all(self):
        self.dry_run_called = True


class _FakeSession:
    session_id = "sess-1"
    test_cases = object()  # truthy: a real session would hold a TestCaseNode


def _result(name: str, status: str) -> TCResult:
    return TCResult(id=name, name=name, elapsed="0.0", status=status, modules=[])


def test_dry_run_executor_returns_test_state():
    """DryRunExecutor.execute returns a copy of runner.result_printer.test_state."""
    state = {"TC1": _result("TC1", "PASS")}
    executor = DryRunExecutor(
        test_case=cast(TestCaseNode, object()),
        event_manager=cast(EventManager, _FakeEventManager()),
    )
    runner = _FakeRunner(state)

    result = asyncio.run(
        executor.execute(cast(Session, _FakeSession()), cast(Runner, runner))
    )

    assert runner.dry_run_called is True
    assert result == state
    assert result is not state  # dict(...) copy, not the runner's live mapping


def test_build_inline_dry_run_suite_builds_linked_list():
    """Inline test_cases/modules/elements build the execution graph + data."""
    request = DryRunRequest(
        driver_sources=[{"appium": {"enabled": True}}],
        test_cases={"TC1": ["Mod1"]},
        modules={"Mod1": [["Press Element", ["${btn}"]], ["Sleep", ["1"]]]},
        elements={"btn": ["xpath=//a", "text=Login"]},
    )

    suite = _build_inline_dry_run_suite(request, None)

    tc_node = suite.execution_queue
    assert tc_node.name == "TC1"
    module_node = tc_node.modules_head
    assert module_node is not None and module_node.name == "Mod1"
    first_kw = module_node.keywords_head
    assert first_kw is not None
    assert first_kw.name == "Press Element"
    assert first_kw.params == ["${btn}"]
    assert first_kw.next is not None and first_kw.next.name == "Sleep"
    assert suite.elements.get_element("btn") == ["xpath=//a", "text=Login"]
    assert any(
        "appium" in entry and entry["appium"].enabled
        for entry in suite.config.driver_sources
    )


def test_build_dry_run_suite_requires_a_source():
    """No inline test_cases and no project_path is a client error (ValueError)."""
    request = DryRunRequest(driver_sources=[{"appium": {"enabled": True}}])
    with pytest.raises(ValueError, match="test suite"):
        _build_dry_run_suite(request)


def test_dry_run_request_modules_coerce_to_tuples():
    """JSON arrays for module steps coerce to (keyword, params) tuples."""
    request = DryRunRequest(
        test_cases={"TC1": ["Mod1"]},
        modules={"Mod1": [["Tap", ["a", "b"]]]},
    )
    step = request.modules["Mod1"][0]
    assert isinstance(step, tuple)
    assert step == ("Tap", ["a", "b"])


def test_dry_run_response_defaults_to_pass():
    response = DryRunResponse(execution_id="abc")
    assert response.status == "PASS"
    assert response.test_cases == []


def test_load_suite_from_folder_loads_contact_sample():
    """The reusable folder loader populates a suite from a real project folder."""
    suite = load_suite_from_folder(str(CONTACT_SAMPLE))

    assert suite.config.project_path == str(CONTACT_SAMPLE)
    assert suite.execution_queue is not None
    assert suite.execution_queue.name  # head test case has a name
    assert suite.modules_data.modules  # at least one module loaded
    assert suite.elements_data.elements  # at least one element loaded


def test_build_inline_dry_run_suite_respects_include():
    """include filters which test cases reach the execution queue."""
    request = DryRunRequest(
        test_cases={"TC_Keep": ["Mod1"], "TC_Drop": ["Mod1"]},
        modules={"Mod1": [["Sleep", ["1"]]]},
        include=["TC_Keep"],
    )

    suite = _build_inline_dry_run_suite(request, None)

    names = []
    node = suite.execution_queue
    while node is not None:
        names.append(node.name)
        node = node.next
    assert names == ["TC_Keep"]


# --- HTTP endpoint (TestClient) ---------------------------------------------
#
# These exercise the route, status mapping, and session teardown. The error
# paths need no driver; the success/failure paths mock session creation and the
# engine so a real driver is never required.


def _patch_engine(monkeypatch, *, returns=None, raises=None):
    """Replace expose_api.ExecutionEngine with a fake whose execute is stubbed."""

    class _FakeEngine:
        def __init__(self, *args, **kwargs):
            # Intentionally empty: the fake ignores ExecutionEngine's
            # constructor args; only execute() is exercised.
            pass

        async def execute(self, params):
            if raises is not None:
                raise raises
            return returns

    monkeypatch.setattr(expose_api, "ExecutionEngine", _FakeEngine)


def _patch_session(monkeypatch, terminated, session_id="sess-x"):
    """Stub session create/terminate so no real driver/session is built."""
    monkeypatch.setattr(
        expose_api.session_manager, "create_session", lambda *a, **k: session_id
    )
    monkeypatch.setattr(
        expose_api.session_manager,
        "terminate_session",
        lambda sid: terminated.append(sid),
    )


def test_dry_run_endpoint_no_suite_returns_400():
    resp = client.post(
        "/v1/dry_run", json={"driver_sources": [{"appium": {"enabled": True}}]}
    )
    assert resp.status_code == 400
    assert "test suite" in resp.text


def test_dry_run_endpoint_bad_project_path_returns_400():
    """A nonexistent project_path is a client error, never a process exit."""
    resp = client.post("/v1/dry_run", json={"project_path": "/no/such/optics/dir"})
    assert resp.status_code == 400


def test_dry_run_endpoint_success_and_teardown(monkeypatch):
    terminated = []
    _patch_session(monkeypatch, terminated, session_id="sess-ok")
    _patch_engine(
        monkeypatch,
        returns={"TC1": _result("TC1", "PASS"), "TC2": _result("TC2", "PASS")},
    )

    resp = client.post("/v1/dry_run", json=_INLINE_SUITE)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "PASS"
    assert {tc["name"] for tc in body["test_cases"]} == {"TC1", "TC2"}
    assert terminated == ["sess-ok"]  # session torn down


def test_dry_run_endpoint_reports_fail_when_a_test_case_fails(monkeypatch):
    terminated = []
    _patch_session(monkeypatch, terminated)
    _patch_engine(
        monkeypatch,
        returns={"TC1": _result("TC1", "PASS"), "TC2": _result("TC2", "FAIL")},
    )

    resp = client.post("/v1/dry_run", json=_INLINE_SUITE)

    assert resp.status_code == 200
    assert resp.json()["status"] == "FAIL"


# --- project_path confinement (path-traversal defense) ----------------------


def test_safe_project_path_allows_within_root(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTICS_PROJECTS_ROOT", str(tmp_path))
    sub = tmp_path / "proj"
    sub.mkdir()
    assert _safe_project_path(str(sub)) == os.path.realpath(str(sub))
    # the root itself is allowed
    assert _safe_project_path(str(tmp_path)) == os.path.realpath(str(tmp_path))


def test_safe_project_path_rejects_outside_root(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "secret").mkdir()
    monkeypatch.setenv("OPTICS_PROJECTS_ROOT", str(allowed))
    with pytest.raises(OpticsError):
        _safe_project_path(str(tmp_path / "secret"))


def test_safe_project_path_rejects_traversal(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("OPTICS_PROJECTS_ROOT", str(allowed))
    # ../ escapes resolve outside the root and are rejected
    with pytest.raises(OpticsError):
        _safe_project_path(str(allowed / ".." / "etc"))


def test_safe_project_path_rejects_sibling_prefix(monkeypatch, tmp_path):
    # "/root-evil" must not be accepted just because it starts with "/root"
    allowed = tmp_path / "root"
    allowed.mkdir()
    (tmp_path / "root-evil").mkdir()
    monkeypatch.setenv("OPTICS_PROJECTS_ROOT", str(allowed))
    with pytest.raises(OpticsError):
        _safe_project_path(str(tmp_path / "root-evil"))


def test_dry_run_endpoint_project_path_outside_root_returns_400(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTICS_PROJECTS_ROOT", str(tmp_path))
    resp = client.post("/v1/dry_run", json={"project_path": "/etc"})
    assert resp.status_code == 400


def test_dry_run_endpoint_execution_error_maps_status_and_terminates(monkeypatch):
    terminated = []
    _patch_session(monkeypatch, terminated, session_id="sess-err")
    error = OpticsError(Code.E0701, message="boom")
    _patch_engine(monkeypatch, raises=error)

    resp = client.post("/v1/dry_run", json=_INLINE_SUITE)

    assert resp.status_code == error.status_code
    assert terminated == ["sess-err"]  # session torn down even on failure
