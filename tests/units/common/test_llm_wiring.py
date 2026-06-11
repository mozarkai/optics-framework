"""Unit tests for LLM config wiring and LiveController.run_natural_language.

Covers: Config round-tripping the llm_models block, enabled-filtering, the builder
returning no LLM when none is enabled, and the commit-on-done recording semantics
(a successful NL run is recorded; a failed/aborted one is not) plus the streaming
NLStep adapter.
"""
import types

import pytest

from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.common.session_manager import _get_enabled_config_list
from optics_framework.common.nl_agent import AgentStep, AgentResult, ExecResult
from optics_framework.helper.live import LiveController, NLStep

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
        from optics_framework.common.optics_builder import OpticsBuilder

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

    def test_no_llm_engine_surfaces_actionable_message(self, monkeypatch):
        ctrl = _bare_controller()

        def _raise():
            from optics_framework.common.error import OpticsError, Code
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
