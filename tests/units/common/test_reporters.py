import asyncio
import os
import time

from optics_framework.common.Html_eventhandler import HtmlEventHandler
from optics_framework.common.Junit_eventhandler import JUnitEventHandler
from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.common.events import Event, EventStatus


def _run(coro):
    return asyncio.run(coro)


def test_html_report_contains_summary_hierarchy_metadata_and_failure_screenshot(tmp_path):
    session_id = "session-1"
    screenshot = tmp_path / "failure.jpg"
    screenshot.write_bytes(b"fake image")
    now = time.time()
    screenshot_mtime = now + 2
    os.utime(screenshot, (screenshot_mtime, screenshot_mtime))

    config = Config(
        file_log=True,
        execution_output_path=str(tmp_path),
        driver_sources=[
            {
                "appium": DependencyConfig(
                    enabled=True,
                    url="http://localhost:4723",
                    capabilities={
                        "deviceName": "Pixel 8",
                        "udid": "ABC123",
                        "platformName": "Android",
                    },
                )
            }
        ],
    )
    handler = HtmlEventHandler(tmp_path / "report.html", session_id=session_id, config=config)

    _run(handler.on_event(Event(entity_type="test_case", entity_id="tc1", name="Login", status=EventStatus.RUNNING, extra={"session_id": session_id}, start_time=now)))
    _run(handler.on_event(Event(entity_type="module", entity_id="mod1", parent_id="tc1", name="Open app", status=EventStatus.RUNNING, extra={"session_id": session_id}, start_time=now + 1)))
    _run(handler.on_event(Event(entity_type="keyword", entity_id="kw1", parent_id="mod1", name="Tap Login", status=EventStatus.RUNNING, extra={"session_id": session_id}, start_time=now + 1)))
    _run(handler.on_event(Event(entity_type="keyword", entity_id="kw1", parent_id="mod1", name="Tap Login", status=EventStatus.FAIL, message="Button missing", extra={"session_id": session_id}, start_time=now + 1, end_time=now + 3, elapsed=2.0, logs=["raw log"])))
    _run(handler.on_event(Event(entity_type="module", entity_id="mod1", parent_id="tc1", name="Open app", status=EventStatus.FAIL, extra={"session_id": session_id}, start_time=now + 1, end_time=now + 3, elapsed=2.0)))
    _run(handler.on_event(Event(entity_type="test_case", entity_id="tc1", name="Login", status=EventStatus.FAIL, extra={"session_id": session_id}, start_time=now, end_time=now + 3, elapsed=3.0)))

    report = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert '"overall_status": "FAIL"' in report
    assert '"name": "Login"' in report
    assert '"name": "Open app"' in report
    assert '"name": "Tap Login"' in report
    assert '"message": "Button missing"' in report
    assert '"screenshot": "failure.jpg"' in report
    assert '"deviceName": "Pixel 8"' in report
    assert '"udid": "ABC123"' in report
    assert ': text("")' not in report


def test_junit_handler_updates_existing_keyword_and_closes_without_uuid_key_error(tmp_path):
    handler = JUnitEventHandler(tmp_path / "junit.xml")
    session_id = "session-1"
    now = time.time()

    _run(handler.on_event(Event(entity_type="test_case", entity_id="tc1", name="Login", status=EventStatus.RUNNING, extra={"session_id": session_id}, start_time=now)))
    _run(handler.on_event(Event(entity_type="module", entity_id="mod1", parent_id="tc1", name="Open app", status=EventStatus.RUNNING, extra={"session_id": session_id}, start_time=now + 1)))
    _run(handler.on_event(Event(entity_type="keyword", entity_id="kw1", parent_id="mod1", name="Tap Login", status=EventStatus.RUNNING, extra={"session_id": session_id}, start_time=now + 1)))
    _run(handler.on_event(Event(entity_type="keyword", entity_id="kw1", parent_id="mod1", name="Tap Login", status=EventStatus.PASS, extra={"session_id": session_id}, start_time=now + 1, end_time=now + 2, elapsed=1.0)))
    _run(handler.on_event(Event(entity_type="module", entity_id="mod1", parent_id="tc1", name="Open app", status=EventStatus.PASS, extra={"session_id": session_id}, start_time=now + 1, end_time=now + 2, elapsed=1.0)))
    _run(handler.on_event(Event(entity_type="test_case", entity_id="tc1", name="Login", status=EventStatus.PASS, extra={"session_id": session_id}, start_time=now, end_time=now + 2, elapsed=2.0)))

    handler.close()

    report = (tmp_path / "junit.xml").read_text(encoding="utf-8")
    assert report.count('name="Tap Login"') == 1
    assert '<testcase name="Login"' in report
    assert 'status="PASS"' in report
