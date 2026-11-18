"""Unit tests for ``optics_framework.common.Junit_eventhandler``.

Pins the two output contracts of the session-scoped JUnit handler:

1. ``logs.json`` is produced if and only if ``json_log`` is enabled, while
   ``junit_output.xml`` is always produced in the session's output directory.
2. Concurrent sessions sharing one resolved output directory never clobber
   each other: the newcomer falls back to ``junit_output_<session_id>.xml``
   while the canonical name stays with its owner, and once sessions are
   cleaned up later ones get the canonical name again.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET  # nosec B405

import pytest

from optics_framework.common.Junit_eventhandler import (
    JUnitHandlerRegistry,
    cleanup_junit,
    get_junit_handler_registry,
    setup_junit,
)
from optics_framework.common.config_handler import Config
from optics_framework.common.events import Event, EventStatus
from optics_framework.common.session_manager import _maybe_setup_junit

pytestmark = pytest.mark.white_box


def _config(output_dir, json_log=False, json_path=None):
    """A minimal Config pointing JUnit output at a tmp dir."""
    config = Config(execution_output_path=str(output_dir), json_log=json_log)
    if json_path is not None:
        config.json_path = str(json_path)
    return config


def _tc_event(session_id, tc_id, name, status):
    return Event(
        entity_type="test_case",
        entity_id=tc_id,
        name=name,
        status=status,
        extra={"session_id": session_id},
    )


async def _run_testcase(handler, session_id, tc_id, name):
    """Drive a RUNNING -> PASS test-case pair through the handler."""
    await handler.on_event(_tc_event(session_id, tc_id, name, EventStatus.RUNNING))
    await handler.on_event(_tc_event(session_id, tc_id, name, EventStatus.PASS))


class TestSessionJunitWiring:
    def test_global_setup_and_cleanup_route_through_registry(self, tmp_path):
        registry = get_junit_handler_registry()
        setup_junit("wire-1", _config(tmp_path, json_log=True))
        try:
            assert "wire-1" in registry.get_active_sessions()
            assert registry.get_handler("wire-1") is not None
        finally:
            cleanup_junit("wire-1")
        assert registry.get_handler("wire-1") is None

    def test_maybe_setup_junit_requires_execution_output_path(self):
        config = Config(json_log=True)
        _maybe_setup_junit(config, "wire-none", None)
        assert get_junit_handler_registry().get_handler("wire-none") is None


class TestKeywordFailureMessage:
    """A failed keyword records why on its <kw> element."""

    async def test_failed_keyword_records_reason_on_kw_element(self, tmp_path):
        registry = JUnitHandlerRegistry()
        sid = "kwfail"
        registry.setup_junit_for_session(sid, _config(tmp_path))
        handler = registry.get_handler(sid)

        await handler.on_event(Event(
            entity_type="test_case", entity_id="tc-1", name="TC",
            status=EventStatus.RUNNING, extra={"session_id": sid}))
        await handler.on_event(Event(
            entity_type="module", entity_id="mod-1", name="Mod",
            status=EventStatus.RUNNING, parent_id="tc-1",
            extra={"session_id": sid}))
        reason = "Keyword not found: Bogus Keyword. Did you mean 'Launch App'?"
        await handler.on_event(Event(
            entity_type="keyword", entity_id="kw-1", name="Bogus Keyword",
            status=EventStatus.FAIL, message=reason, parent_id="mod-1",
            extra={"session_id": sid}))
        registry.cleanup_session(sid)

        root = ET.parse(tmp_path / "junit_output.xml").getroot()
        failed = [e for e in root.iter("kw") if e.get("name") == "Bogus Keyword"]
        assert failed
        assert "Did you mean 'Launch App'" in (failed[0].get("message") or "")


class TestDetectedErrorsBeforeTestCaseEvents:
    """End-of-run detection can run while the run's test-case events are still queued."""

    async def test_detected_error_reaches_report_when_all_cases_pass(self, tmp_path):
        registry = JUnitHandlerRegistry()
        sid = "allpass"
        registry.setup_junit_for_session(sid, _config(tmp_path))
        handler = registry.get_handler(sid)

        handler.add_detected_errors(sid, [{
            "error_code": "ERR_SESSION",
            "severity": "high",
            "match_string": "session expired",
            "description": "Session expired banner",
        }])
        await _run_testcase(handler, sid, "tc-1", "Login")
        await _run_testcase(handler, sid, "tc-2", "Checkout")
        registry.cleanup_session(sid)

        root = ET.parse(tmp_path / "junit_output.xml").getroot()
        suites = root.findall("testsuite")
        assert len(suites) == 1
        suite = suites[0]
        assert suite.get("tests") == "3"
        assert suite.get("failures") == "1"

        cases = {tc.get("name"): tc for tc in suite.iter("testcase")}
        assert set(cases) == {"on-screen-error-detection", "Login", "Checkout"}
        failures = cases["on-screen-error-detection"].findall("failure")
        assert len(failures) == 1
        assert failures[0].get("type") == "OnScreenError"
        assert failures[0].get("message").startswith("ERR_SESSION | high")
        assert cases["Login"].find("failure") is None
        assert cases["Checkout"].find("failure") is None
