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

import json
import xml.etree.ElementTree as ET  # nosec B405
from pathlib import Path

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


def _testcase_names(junit_file):
    root = ET.parse(junit_file).getroot()
    return {tc.get("name") for tc in root.iter("testcase")}


class TestJsonLogGate:
    async def test_json_log_disabled_writes_junit_only(self, tmp_path):
        registry = JUnitHandlerRegistry()
        registry.setup_junit_for_session("gate-off", _config(tmp_path, json_log=False))
        handler = registry.get_handler("gate-off")
        assert handler is not None
        assert handler.json_output_path is None

        await _run_testcase(handler, "gate-off", "tc-1", "Login")
        registry.cleanup_session("gate-off")

        junit_file = tmp_path / "junit_output.xml"
        assert junit_file.exists()
        assert _testcase_names(junit_file) == {"Login"}
        assert not (tmp_path / "logs.json").exists()

    async def test_json_log_enabled_writes_both(self, tmp_path):
        registry = JUnitHandlerRegistry()
        registry.setup_junit_for_session("gate-on", _config(tmp_path, json_log=True))
        handler = registry.get_handler("gate-on")
        assert handler.json_output_path == tmp_path / "logs.json"

        await _run_testcase(handler, "gate-on", "tc-1", "Login")
        registry.cleanup_session("gate-on")

        assert (tmp_path / "junit_output.xml").exists()
        events = json.loads((tmp_path / "logs.json").read_text(encoding="utf-8"))
        assert [e["entity_id"] for e in events] == ["tc-1", "tc-1"]
        assert all(e["extra"]["session_id"] == "gate-on" for e in events)

    async def test_json_log_custom_path_used_when_enabled(self, tmp_path):
        registry = JUnitHandlerRegistry()
        custom = tmp_path / "custom" / "events.json"
        registry.setup_junit_for_session(
            "gate-custom", _config(tmp_path, json_log=True, json_path=custom)
        )
        try:
            assert registry.get_handler("gate-custom").json_output_path == custom
        finally:
            registry.cleanup_session("gate-custom")


class TestConcurrentSessionIsolation:
    async def test_overlapping_sessions_write_distinct_junit_files(self, tmp_path):
        registry = JUnitHandlerRegistry()
        registry.setup_junit_for_session("iso-a", _config(tmp_path))
        registry.setup_junit_for_session("iso-b", _config(tmp_path))

        first = registry.get_handler("iso-a")
        second = registry.get_handler("iso-b")
        assert first.output_path == tmp_path / "junit_output.xml"
        assert second.output_path == tmp_path / "junit_output_iso-b.xml"

        await _run_testcase(first, "iso-a", "tc-a", "Case A")
        await _run_testcase(second, "iso-b", "tc-b", "Case B")
        registry.cleanup_session("iso-a")
        registry.cleanup_session("iso-b")

        canonical = tmp_path / "junit_output.xml"
        newcomer = tmp_path / "junit_output_iso-b.xml"
        assert {p for p in tmp_path.glob("*.xml")} == {canonical, newcomer}
        assert _testcase_names(canonical) == {"Case A"}
        assert _testcase_names(newcomer) == {"Case B"}

    async def test_canonical_name_returns_once_its_owner_leaves(self, tmp_path):
        registry = JUnitHandlerRegistry()
        registry.setup_junit_for_session("seq-a", _config(tmp_path))
        registry.setup_junit_for_session("seq-b", _config(tmp_path))
        registry.cleanup_session("seq-a")

        registry.setup_junit_for_session("seq-c", _config(tmp_path))
        assert registry.get_handler("seq-c").output_path == tmp_path / "junit_output.xml"
        assert registry.get_handler("seq-b").output_path == tmp_path / "junit_output_seq-b.xml"

        registry.cleanup_session("seq-b")
        registry.cleanup_session("seq-c")

    def test_sequential_sessions_reuse_canonical_name(self, tmp_path):
        registry = JUnitHandlerRegistry()
        registry.setup_junit_for_session("run-1", _config(tmp_path))
        assert registry.get_handler("run-1").output_path == tmp_path / "junit_output.xml"
        registry.cleanup_session("run-1")

        registry.setup_junit_for_session("run-2", _config(tmp_path))
        assert registry.get_handler("run-2").output_path == tmp_path / "junit_output.xml"
        registry.cleanup_session("run-2")


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

    def test_maybe_setup_junit_sets_json_path_only_when_enabled(self, tmp_path):
        config = Config(execution_output_path=str(tmp_path), json_log=True)
        try:
            _maybe_setup_junit(config, "wire-json-1", str(tmp_path))
            assert config.json_path == str((tmp_path / "logs.json").expanduser())
            handler = get_junit_handler_registry().get_handler("wire-json-1")
            assert handler.json_output_path == Path(config.json_path)
        finally:
            cleanup_junit("wire-json-1")

    def test_maybe_setup_junit_without_json_log_still_sets_up_junit(self, tmp_path):
        config = Config(execution_output_path=str(tmp_path), json_log=False)
        try:
            _maybe_setup_junit(config, "wire-json-2", str(tmp_path))
            assert config.json_path is None
            handler = get_junit_handler_registry().get_handler("wire-json-2")
            assert handler.output_path == tmp_path / "junit_output.xml"
            assert handler.json_output_path is None
        finally:
            cleanup_junit("wire-json-2")

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
