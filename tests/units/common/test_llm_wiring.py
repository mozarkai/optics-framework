import types
from unittest.mock import MagicMock


from optics_framework.common.nl_agent import AgentStep, AgentResult, ExecResult
from optics_framework.helper.live import LiveController
from optics_framework.common.error import OpticsError, Code


def _bare_controller():
    """A LiveController shell that skips the heavy __init__ (no device/session)."""
    ctrl = LiveController.__new__(LiveController)
    ctrl.recorded = []
    ctrl.saved = True
    ctrl._nl_available = None
    ctrl._action_keyword = MagicMock()
    ctrl._nl_new_elements = {}
    ctrl._agent_elements = set()
    return ctrl


class _FakeStrategyManager:
    def __init__(self, result=None, elements_result=None):
        self._result = result
        self._elements_result = elements_result
        self.compact_arg = None
        # screen_elements() decodes the PNG it is handed instead of taking a second
        # screenshot -- kept as a spy so tests can assert it is never called.
        self.capture_screenshot = MagicMock(name="capture_screenshot")

    def capture_pagesource(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    def get_interactive_elements(self, filter_config=None, compact=False):
        self.compact_arg = compact
        if isinstance(self._elements_result, Exception):
            raise self._elements_result
        return self._elements_result


def _ps_controller(strategy_result):
    ctrl = LiveController.__new__(LiveController)
    ctrl._action_keyword = types.SimpleNamespace(
        strategy_manager=_FakeStrategyManager(strategy_result)
    )
    return ctrl


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
        assert out
        assert "search_edit_text" in out
        assert "EditText" in out
        assert 'text="gullak"' in out


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

    def test_failed_run_does_not_record(self, monkeypatch):
        ctrl = _bare_controller()
        d1, e1 = _keyword_step("press_element", ["A"], ok=False)
        result = AgentResult(status="failed", successful_steps=[], message="Too many failures.")
        monkeypatch.setattr(ctrl, "_get_nl_agent", lambda: FakeAgent(result, [d1, e1]))

        summary = ctrl.run_natural_language("do it", on_step=lambda s: None)
        assert summary.status == "FAIL"
        assert ctrl.recorded == []
        assert ctrl.saved is True

    def test_no_llm_engine_surfaces_actionable_message(self, monkeypatch):
        ctrl = _bare_controller()

        def _raise():
            raise OpticsError(Code.E0501, message="No LLM engine enabled. Enable a 'gemini' entry under llm_models in config.yaml.")

        monkeypatch.setattr(ctrl, "_get_nl_agent", _raise)
        summary = ctrl.run_natural_language("do it", on_step=lambda s: None)
        assert summary.status == "FAIL"
        assert "llm_models" in (summary.message or "")
