"""Unit tests for LLM config wiring and LiveController.run_natural_language.

Covers: Config round-tripping the llm_models block, enabled-filtering, the builder
returning no LLM when none is enabled, and the commit-on-done recording semantics
(a successful NL run is recorded; a failed/aborted one is not) plus the streaming
NLStep adapter.
"""
import types
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.common.session_manager import _get_enabled_config_list
from optics_framework.common.nl_agent import AgentStep, AgentResult, ExecResult
from optics_framework.helper.live import LiveController, NLStep
from optics_framework.helper.live import NLRunStatus
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.error import OpticsError, Code
from optics_framework.common import utils

pytestmark = pytest.mark.white_box


class TestConfigWiring:
    def test_default_has_disabled_gemini(self):
        cfg = Config()
        assert cfg.llm_models == [
            {"gemini": DependencyConfig(enabled=False, url=None, capabilities={})}
        ]

    def test_enabled_filtering(self):
        cfg = Config(
            llm_models=[
                {"gemini": DependencyConfig(enabled=True, capabilities={"model": "gemini-2.5-flash"})},
                {"other": DependencyConfig(enabled=False)},
            ]
        )
        enabled = _get_enabled_config_list(cfg, "llm_models")
        assert len(enabled) == 1
        assert "gemini" in enabled[0]
        assert enabled[0]["gemini"]["capabilities"] == {"model": "gemini-2.5-flash"}

    def test_builder_returns_none_without_llm(self):
        session = types.SimpleNamespace(
            event_sdk=None,
            config=types.SimpleNamespace(project_path=None),
        )
        builder = OpticsBuilder(session)
        # No add_llm called -> llm_config is None -> get_llm returns None, nothing imported.
        assert builder.get_llm() is None


def _bare_controller():
    """A LiveController shell that skips the heavy __init__ (no device/session)."""
    ctrl = LiveController.__new__(LiveController)
    ctrl.recorded = []
    ctrl.saved = True
    ctrl._nl_available = None
    return ctrl


class _FakeStrategyManager:
    def __init__(self, result=None, elements_result=None):
        self._result = result
        self._elements_result = elements_result
        # screen_elements() decodes the PNG bytes it's given instead of capturing a second
        # screenshot -- kept as a spy so tests can assert it's never called.
        self.capture_screenshot = MagicMock(name="capture_screenshot")

    def capture_pagesource(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    def get_interactive_elements(self, filter_config=None):
        if isinstance(self._elements_result, Exception):
            raise self._elements_result
        return self._elements_result


def _ps_controller(strategy_result):
    ctrl = LiveController.__new__(LiveController)
    ctrl._action_keyword = types.SimpleNamespace(
        strategy_manager=_FakeStrategyManager(strategy_result)
    )
    return ctrl


def _elements_controller(elements_result, driver_type="appium"):
    ctrl = LiveController.__new__(LiveController)
    ctrl._action_keyword = types.SimpleNamespace(
        strategy_manager=_FakeStrategyManager(elements_result=elements_result),
        element_source=types.SimpleNamespace(REQUIRED_DRIVER_TYPE=driver_type),
    )
    return ctrl


def _png_bytes(width=4, height=4):
    ok, buf = cv2.imencode(".png", np.zeros((height, width, 3), dtype=np.uint8))
    assert ok
    return buf.tobytes()


_PS_XML = (
    "<hierarchy class='hierarchy'>"
    "<android.widget.EditText class='android.widget.EditText' text='gullak' "
    "resource-id='com.sonyliv:id/search_edit_text' clickable='true' bounds='[26,162][1054,293]'/>"
    "</hierarchy>"
)


class TestControllerPageSource:
    def test_returns_stripped_source(self):
        ctrl = _ps_controller((_PS_XML, "ts"))
        out = ctrl.page_source()
        assert out and "search_edit_text" in out
        assert "EditText" in out and 'text="gullak"' in out

    def test_returns_none_when_unavailable(self):
        ctrl = _ps_controller(OpticsError(Code.E0403, message="No pagesource"))
        assert ctrl.page_source() is None

    def test_returns_none_when_no_action_keyword(self):
        ctrl = LiveController.__new__(LiveController)
        ctrl._action_keyword = None
        assert ctrl.page_source() is None


class TestControllerScreenElements:
    def test_returns_elements_and_scales_bounds_for_appium(self, monkeypatch):
        elements = [{"text": "Search", "bounds": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}}]
        ctrl = _elements_controller(elements, driver_type="appium")

        calls = []
        monkeypatch.setattr(
            utils, "scale_interactive_element_bounds",
            lambda els, src, ss: calls.append((els, src, ss)),
        )

        out = ctrl.screen_elements(_png_bytes())

        assert out == elements
        assert len(calls) == 1
        assert calls[0][0] == elements
        assert isinstance(calls[0][2], np.ndarray)  # decoded from the passed PNG bytes
        # No second device screenshot -- bounds come from decoding what was already
        # captured for the LLM, not a fresh capture_screenshot() round trip.
        ctrl._action_keyword.strategy_manager.capture_screenshot.assert_not_called()

    def test_skips_decode_for_non_appium_source(self, monkeypatch):
        elements = [{"text": "Row", "bounds": {"x1": 0, "y1": 0, "x2": 5, "y2": 5}}]
        ctrl = _elements_controller(elements, driver_type="playwright")

        calls = []
        monkeypatch.setattr(
            utils, "scale_interactive_element_bounds",
            lambda els, src, ss: calls.append(ss),
        )

        out = ctrl.screen_elements(_png_bytes())

        assert out == elements
        assert calls == [None]  # scale called, but with no frame to scale against
        ctrl._action_keyword.strategy_manager.capture_screenshot.assert_not_called()

    def test_returns_none_when_get_interactive_elements_unavailable(self):
        ctrl = _elements_controller(OpticsError(Code.E0202, message="No interactive elements"))
        assert ctrl.screen_elements(_png_bytes()) is None

    def test_returns_none_when_no_action_keyword(self):
        ctrl = LiveController.__new__(LiveController)
        ctrl._action_keyword = None
        assert ctrl.screen_elements(_png_bytes()) is None

    def test_undecodable_png_bytes_still_returns_elements(self, monkeypatch):
        # Bounds scaling is best-effort: bytes that fail to decode into a frame must not
        # sink the structured elements the NL agent otherwise has available this step.
        elements = [{"text": "Search", "bounds": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}}]
        ctrl = _elements_controller(elements, driver_type="appium")
        monkeypatch.setattr(utils, "scale_interactive_element_bounds", lambda *a, **k: None)

        assert ctrl.screen_elements(b"not a real png") == elements


class FakeAgent:
    """Drives the on_step adapter then returns a scripted AgentResult."""

    def __init__(self, result, emit_steps):
        self._result = result
        self._emit_steps = emit_steps

    def run(self, instruction, on_step=None, should_abort=None):
        if on_step is not None:
            for step in self._emit_steps:
                on_step(step)
        return self._result


def _keyword_step(keyword, params, ok=True):
    """A decision emission (observation None) is followed by an executed emission."""
    decision = AgentStep(thought=f"do {keyword}", action="keyword", keyword=keyword, params=params)
    executed = AgentStep(
        thought=f"do {keyword}", action="keyword", keyword=keyword, params=params,
        observation="PASS" if ok else "FAIL",
        exec_result=ExecResult(ok=ok, strategy="OCR", elapsed=0.2, message=None if ok else "boom"),
    )
    return decision, executed


class TestRunNaturalLanguage:
    def test_commit_on_done_records_and_streams(self, monkeypatch):
        ctrl = _bare_controller()
        d1, e1 = _keyword_step("press_element", ["Search"])
        d2, e2 = _keyword_step("enter_text", ["Search", "movies for kids"])
        result = AgentResult(
            status="done",
            successful_steps=[("press_element", ["Search"]), ("enter_text", ["Search", "movies for kids"])],
            message="Goal reached.",
        )
        monkeypatch.setattr(ctrl, "_get_nl_agent", lambda: FakeAgent(result, [d1, e1, d2, e2]))

        seen = []
        summary = ctrl.run_natural_language("search movies", on_step=seen.append)

        assert summary.status == "PASS"
        assert summary.steps == 2
        # commit-on-done: the buffered steps are appended to the recording.
        assert ctrl.recorded == [
            ("press_element", ["Search"]),
            ("enter_text", ["Search", "movies for kids"]),
        ]
        assert ctrl.saved is False
        # streamed: 2 thinking lines + 2 keyword child lines.
        kinds = [s.kind for s in seen]
        assert kinds == ["thinking", "keyword", "thinking", "keyword"]
        kw_steps = [s for s in seen if s.kind == "keyword"]
        assert kw_steps[1].result.raw == "enter_text Search 'movies for kids'"
        assert all(isinstance(s, NLStep) for s in seen)

    def test_failed_run_does_not_record(self, monkeypatch):
        ctrl = _bare_controller()
        d1, e1 = _keyword_step("press_element", ["A"], ok=False)
        result = AgentResult(status="failed", successful_steps=[], message="Too many failures.")
        monkeypatch.setattr(ctrl, "_get_nl_agent", lambda: FakeAgent(result, [d1, e1]))

        summary = ctrl.run_natural_language("do it", on_step=lambda s: None)
        assert summary.status == "FAIL"
        assert ctrl.recorded == []
        assert ctrl.saved is True

    def test_empty_instruction(self):
        ctrl = _bare_controller()
        summary = ctrl.run_natural_language("   ", on_step=lambda s: None)
        assert summary.status == "FAIL"
        assert "Empty" in (summary.message or "")

    def test_agent_crash_returns_enum_status(self, monkeypatch):
        # An unexpected (non-Optics) exception from agent.run must not crash the
        # controller and must return a genuine NLRunStatus enum, not a raw "FAIL"
        # string (regression: the field is typed NLRunStatus).
        class _BoomAgent:
            def run(self, *a, **k):
                raise RuntimeError("kaboom")

        ctrl = _bare_controller()
        monkeypatch.setattr(ctrl, "_get_nl_agent", lambda: _BoomAgent())
        summary = ctrl.run_natural_language("do it", on_step=lambda s: None)
        assert isinstance(summary.status, NLRunStatus)
        assert summary.status is NLRunStatus.FAIL
        assert "RuntimeError" in (summary.message or "")

    def test_no_llm_engine_surfaces_actionable_message(self, monkeypatch):
        ctrl = _bare_controller()

        def _raise():
            raise OpticsError(Code.E0501, message="No LLM engine enabled. Enable a 'gemini' entry under llm_models in config.yaml.")

        monkeypatch.setattr(ctrl, "_get_nl_agent", _raise)
        summary = ctrl.run_natural_language("do it", on_step=lambda s: None)
        assert summary.status == "FAIL"
        assert "llm_models" in (summary.message or "")

    def test_availability_reads_config(self):
        ctrl = _bare_controller()
        ctrl.session = types.SimpleNamespace(
            config=types.SimpleNamespace(
                llm_models=[{"gemini": DependencyConfig(enabled=True)}]
            )
        )
        assert ctrl.natural_language_available() is True

        ctrl2 = _bare_controller()
        ctrl2.session = types.SimpleNamespace(
            config=types.SimpleNamespace(
                llm_models=[{"gemini": DependencyConfig(enabled=False)}]
            )
        )
        assert ctrl2.natural_language_available() is False
