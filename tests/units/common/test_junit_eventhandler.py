from __future__ import annotations

import xml.etree.ElementTree as ET  # nosec B405

import pytest


from optics_framework.common.Junit_eventhandler import (
    JUnitHandlerRegistry,
)
from optics_framework.common.config_handler import Config
from optics_framework.common.events import Event, EventStatus


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


class TestKeywordFailureMessage:
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


@pytest.mark.parametrize("kind,step_type", [("test", "test"), ("suite_setup", "setup"), ("teardown", "teardown")])
async def test_step_types_follow_the_case_kind(tmp_path, kind, step_type):
    registry, sid = JUnitHandlerRegistry(), "kinds"
    registry.setup_junit_for_session(sid, _config(tmp_path))
    handler, run, ok = registry.get_handler(sid), EventStatus.RUNNING, EventStatus.PASS
    for entity, name, parent, status in [("test_case", "TC", None, run), ("module", "Mod", "TC", run), ("keyword", "Press", "Mod", ok), ("test_case", "TC", None, ok)]:
        await handler.on_event(Event(entity_type=entity, entity_id=name, name=name, status=status, parent_id=parent, extra={"session_id": sid, **({"kind": kind} if parent is None else {})}))
    registry.cleanup_session(sid)
    assert {e.get("name"): e.get("type") for e in ET.parse(tmp_path / "junit_output.xml").getroot().iter("kw")} == {"Mod": step_type, "Press": "keyword"}


def test_event_timestamp_reads_time_dot_time_when_made(monkeypatch):
    monkeypatch.setattr("time.time", lambda: 42.0)
    assert Event(entity_type="keyword", entity_id="k", name="k", status=EventStatus.PASS).timestamp == 42.0
