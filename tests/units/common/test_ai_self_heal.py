"""Unit tests for the keyword-based AI self-heal handler and its ActionKeyword wiring.

No network: a scripted fake LLM drives the handler and a fake keyword executor records the
keyword lines dispatched. Covers each action type, the bounded loop, give_up, done, malformed
JSON, LLM/executor errors, missing-screenshot inertness, page-source injection, and the
ActionKeyword inert/active paths.
"""
import json
from dataclasses import dataclass

import numpy as np
import pytest

from optics_framework.common import ai_self_heal as ash
from optics_framework.common.ai_self_heal import (
    AISelfHealHandler,
    HealContext,
    HealKeywordSpec,
    HEAL_ACTION_SCHEMA,
)
from optics_framework.common.error import OpticsError, Code

pytestmark = pytest.mark.white_box


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Never actually sleep during the settle delay."""
    monkeypatch.setattr(ash.time, "sleep", lambda *_a, **_k: None)


class FakeLLM:
    """Returns scripted JSON dicts from generate_json, ignoring prompt/images."""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = 0
        self.last_prompt = None
        self.last_system = None

    def generate(self, *a, **k):  # pragma: no cover - handler uses generate_json
        raise RuntimeError("unexpected generate() call")

    def generate_json(self, prompt, response_schema, images=None, system=None, temperature=None):
        self.calls += 1
        self.last_prompt = prompt
        self.last_system = system
        assert response_schema is HEAL_ACTION_SCHEMA
        assert images and isinstance(images[0], (bytes, bytearray))
        return self.scripted.pop(0)


@dataclass
class ExecResult:
    ok: bool
    message: str = ""


class FakeExecutor:
    """Records keyword lines dispatched by the handler."""

    def __init__(self, *, fail_keywords=None):
        self.calls = []
        self._fail_keywords = fail_keywords or set()

    def __call__(self, line: str) -> ExecResult:
        self.calls.append(line)
        keyword = line.split()[0] if line else ""
        if keyword in self._fail_keywords:
            return ExecResult(ok=False, message=f"Element not found for '{keyword}'")
        return ExecResult(ok=True)


def _catalog():
    return [
        HealKeywordSpec(name="press_element", signature="press_element <element> [repeat]"),
        HealKeywordSpec(name="enter_text", signature="enter_text <element> <text>"),
        HealKeywordSpec(name="scroll", signature="scroll <direction>"),
        HealKeywordSpec(name="swipe_by_percentage", signature="swipe_by_percentage <percent_x> <percent_y> <direction> [swipe_length]"),
        HealKeywordSpec(name="press_keycode", signature="press_keycode <keycode>"),
        HealKeywordSpec(name="press_by_percentage", signature="press_by_percentage <percent_x> <percent_y>"),
    ]


def _shots():
    return b"PNGBYTES"


def _no_ps():
    return None


def _ctx():
    return HealContext(intent_keyword="press_element", intent_params=["Login"], element="Login")


class TestActionSchema:
    def test_required_minimal(self):
        assert HEAL_ACTION_SCHEMA["required"] == ["reason", "action"]

    def test_no_anyof(self):
        assert "anyOf" not in json.dumps(HEAL_ACTION_SCHEMA)


class TestHandlerActions:
    def test_press_element_completes(self):
        """LLM emits press_element with text target — single step, completed."""
        llm = FakeLLM([
            {"action": "keyword", "keyword": "press_element", "params": ["Meesho"],
             "completed": True, "reason": "press target"},
        ])
        executor = FakeExecutor()
        res = AISelfHealHandler(llm, executor, lambda: _catalog()).heal(_ctx(), _shots, _no_ps)
        assert res.ok is True
        assert executor.calls == ['press_element Meesho']
        assert llm.calls == 1

    def test_scroll_then_press_element(self):
        """LLM scrolls to reveal target, then presses by text."""
        llm = FakeLLM([
            {"action": "keyword", "keyword": "scroll", "params": ["down"],
             "completed": False, "reason": "reveal target"},
            {"action": "keyword", "keyword": "press_element", "params": ["Meesho"],
             "completed": True, "reason": "press target"},
        ])
        executor = FakeExecutor()
        res = AISelfHealHandler(llm, executor, lambda: _catalog(), max_steps=2).heal(_ctx(), _shots, _no_ps)
        assert res.ok is True
        assert executor.calls == ['scroll down', 'press_element Meesho']
        assert llm.calls == 2

    def test_enter_text_completes(self):
        """LLM emits enter_text — single step completion."""
        ctx = HealContext(intent_keyword="enter_text", intent_params=["Search", "hello"], element="Search")
        llm = FakeLLM([
            {"action": "keyword", "keyword": "enter_text", "params": ["Search", "hello"],
             "completed": True, "reason": "type it"},
        ])
        executor = FakeExecutor()
        res = AISelfHealHandler(llm, executor, lambda: _catalog()).heal(ctx, _shots, _no_ps)
        assert res.ok is True
        assert executor.calls == ["enter_text Search hello"]

    def test_press_keycode_then_press_element(self):
        """LLM presses back, then targets element by text."""
        llm = FakeLLM([
            {"action": "keyword", "keyword": "press_keycode", "params": ["4"],
             "completed": False, "reason": "go back"},
            {"action": "keyword", "keyword": "press_element", "params": ["Login"],
             "completed": True, "reason": "press it"},
        ])
        executor = FakeExecutor()
        res = AISelfHealHandler(llm, executor, lambda: _catalog(), max_steps=2).heal(_ctx(), _shots, _no_ps)
        assert res.ok is True
        assert executor.calls == ['press_keycode 4', 'press_element Login']

    def test_done_action_succeeds(self):
        """LLM signals done — goal reached without a keyword."""
        llm = FakeLLM([
            {"action": "done", "reason": "target already visible and pressed"},
        ])
        executor = FakeExecutor()
        res = AISelfHealHandler(llm, executor, lambda: _catalog()).heal(_ctx(), _shots, _no_ps)
        assert res.ok is True
        assert executor.calls == []

    def test_give_up_fails(self):
        llm = FakeLLM([{"action": "give_up", "reason": "blocked"}])
        executor = FakeExecutor()
        res = AISelfHealHandler(llm, executor, lambda: _catalog()).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert "blocked" in res.message

    def test_malformed_action_treated_as_give_up(self):
        llm = FakeLLM([{"action": "frobnicate", "reason": "nonsense"}])
        executor = FakeExecutor()
        res = AISelfHealHandler(llm, executor, lambda: _catalog()).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert executor.calls == []

    def test_step_budget_exhausted(self):
        llm = FakeLLM([
            {"action": "keyword", "keyword": "scroll", "params": ["down"], "reason": "x"}
        ] * 2)
        executor = FakeExecutor()
        res = AISelfHealHandler(llm, executor, lambda: _catalog(), max_steps=2).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert llm.calls == 2
        assert executor.calls == ['scroll down', 'scroll down']

    def test_keyword_failure_does_not_abort(self):
        """A failing keyword lets the LLM retry a different approach."""
        llm = FakeLLM([
            {"action": "keyword", "keyword": "press_element", "params": ["WrongText"],
             "completed": True, "reason": "try pressing"},
            {"action": "keyword", "keyword": "press_element", "params": ["Login"],
             "completed": True, "reason": "correct target"},
        ])
        executor = FakeExecutor(fail_keywords={"press_element"})
        # Both fail because we set press_element as failing, so heal should fail
        res = AISelfHealHandler(llm, executor, lambda: _catalog(), max_steps=2).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert len(executor.calls) == 2

    def test_intermediate_press_then_final_press(self):
        """LLM presses a menu (completed=False), then the final target (completed=True)."""
        llm = FakeLLM([
            {"action": "keyword", "keyword": "press_element", "params": ["Apps"],
             "completed": False, "reason": "open apps drawer"},
            {"action": "keyword", "keyword": "press_element", "params": ["Meesho"],
             "completed": True, "reason": "press target"},
        ])
        executor = FakeExecutor()
        res = AISelfHealHandler(llm, executor, lambda: _catalog(), max_steps=2).heal(_ctx(), _shots, _no_ps)
        assert res.ok is True
        assert executor.calls == ['press_element Apps', 'press_element Meesho']

    def test_swipe_uses_percentage(self):
        llm = FakeLLM([
            {"action": "keyword", "keyword": "swipe_by_percentage", "params": ["50", "80", "up", "30"],
             "completed": False, "reason": "reveal target"},
            {"action": "give_up", "reason": "still not visible"},
        ])
        executor = FakeExecutor()
        res = AISelfHealHandler(llm, executor, lambda: _catalog(), max_steps=2).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert executor.calls == ['swipe_by_percentage 50 80 up 30']


class TestHandlerErrorHandling:
    def test_llm_error_returns_not_ok(self):
        class BoomLLM:
            def generate_json(self, *a, **k):
                raise OpticsError(Code.E0801, message="bad json")

        res = AISelfHealHandler(BoomLLM(), FakeExecutor(), lambda: _catalog()).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert "bad json" in res.message

    def test_executor_exception_returns_not_ok(self):
        def boom_executor(line):
            raise RuntimeError("device offline")

        llm = FakeLLM([{"action": "keyword", "keyword": "press_element", "params": ["X"], "reason": "x"}])
        res = AISelfHealHandler(llm, boom_executor, lambda: _catalog()).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert "device offline" in res.message

    def test_no_screenshot_is_inert(self):
        llm = FakeLLM([{"action": "keyword", "keyword": "press_element", "params": ["X"], "reason": "x"}])
        res = AISelfHealHandler(llm, FakeExecutor(), lambda: _catalog()).heal(_ctx(), lambda: None, _no_ps)
        assert res.ok is False
        assert llm.calls == 0


class TestPromptContext:
    def test_page_source_and_context_injected(self):
        llm = FakeLLM([{"action": "give_up", "reason": "x"}])
        ps = 'EditText "User" id=login_field bounds=[0,0][10,10] clickable'
        ctx = HealContext(
            intent_keyword="enter_text",
            intent_params=["User", "bob"],
            element="User",
            recent_steps=[("press_element", ["Menu"])],
            failed_strategies=["XPathStrategy", "TextDetectionStrategy"],
        )
        AISelfHealHandler(llm, FakeExecutor(), lambda: _catalog()).heal(ctx, _shots, lambda: ps)
        assert "login_field" in llm.last_prompt
        assert "CURRENT SCREEN ELEMENTS" in llm.last_prompt
        assert "enter_text" in llm.last_prompt
        assert "press_element" in llm.last_prompt  # recent step
        assert "TextDetectionStrategy" in llm.last_prompt
        assert "last-resort" in llm.last_system.lower()

    def test_keyword_catalog_in_prompt(self):
        llm = FakeLLM([{"action": "give_up", "reason": "x"}])
        AISelfHealHandler(llm, FakeExecutor(), lambda: _catalog()).heal(_ctx(), _shots, _no_ps)
        assert "AVAILABLE KEYWORDS" in llm.last_prompt
        assert "press_element <element>" in llm.last_prompt
        assert "scroll <direction>" in llm.last_prompt


# --------------------------------------------------------------------------------------------
# ActionKeyword integration: inert when off / no LLM, active when on.
# --------------------------------------------------------------------------------------------

from unittest.mock import MagicMock, patch  # noqa: E402
import tempfile  # noqa: E402

from optics_framework.api.action_keyword import ActionKeyword  # noqa: E402
from optics_framework.common.optics_builder import OpticsBuilder  # noqa: E402


class _Builder(OpticsBuilder):
    def __init__(self, *, ai_self_heal, llm):
        self.mock_driver = MagicMock()
        self.mock_element_source = MagicMock()
        self._llm = llm
        self.session_config = MagicMock()
        self.session_config.execution_output_path = tempfile.mkdtemp()
        self.session_config.ai_self_heal = ai_self_heal

    def get_driver(self):
        return self.mock_driver

    def get_element_source(self):
        return self.mock_element_source

    def get_text_detection(self):
        return None

    def get_image_detection(self):
        return None

    def get_llm(self):
        return self._llm

    @property
    def event_sdk(self):
        return MagicMock()


def _make_action_keyword(*, ai_self_heal, llm):
    with patch("optics_framework.api.action_keyword.Verifier", MagicMock()):
        return ActionKeyword(_Builder(ai_self_heal=ai_self_heal, llm=llm))


class TestActionKeywordWiring:
    def test_toggle_off_is_inert(self):
        ak = _make_action_keyword(ai_self_heal=False, llm=MagicMock(instances=[object()]))
        assert ak.ai_self_heal_enabled is False
        assert ak._llm is None
        shot = np.zeros((10, 10, 3), dtype=np.uint8)
        assert ak._ai_self_heal("X", "press_element", (), {}, shot) is False

    def test_no_screenshot_inert(self):
        ak = _make_action_keyword(ai_self_heal=True, llm=MagicMock(instances=[object()]))
        assert ak._ai_self_heal("X", "press_element", (), {}, None) is False

    def test_llm_without_instances_inert(self):
        ak = _make_action_keyword(ai_self_heal=True, llm=MagicMock(instances=[]))
        shot = np.zeros((10, 10, 3), dtype=np.uint8)
        assert ak._ai_self_heal("X", "press_element", (), {}, shot) is False

    def test_heal_catalog_returns_specs(self):
        ak = _make_action_keyword(ai_self_heal=True, llm=MagicMock(instances=[object()]))
        catalog = ak._heal_catalog()
        names = [spec.name for spec in catalog]
        assert "press_element" in names
        assert "scroll" in names
        assert "enter_text" in names
        # Signatures should be human-readable
        pe_spec = next(s for s in catalog if s.name == "press_element")
        assert "<element>" in pe_spec.signature

    def test_active_press_element_heals(self, monkeypatch):
        monkeypatch.setattr(ash.time, "sleep", lambda *_a, **_k: None)
        llm = FakeLLM([
            {"action": "keyword", "keyword": "scroll", "params": ["down"],
             "completed": False, "reason": "reveal"},
            {"action": "keyword", "keyword": "press_element", "params": ["Login"],
             "completed": True, "reason": "press it"},
        ])
        llm.instances = [object()]  # InstanceFallback-like truthiness gate
        ak = _make_action_keyword(ai_self_heal=True, llm=llm)
        # Mock strategy manager to return valid screenshot bytes and no page source.
        shot = np.zeros((10, 10, 3), dtype=np.uint8)
        ak.strategy_manager.capture_screenshot = MagicMock(return_value=shot)
        ak.strategy_manager.capture_pagesource = MagicMock(return_value=None)

        # Mock the keyword methods that _heal_execute will call.
        # scroll is non-self-healing so it's called directly.
        ak.scroll = MagicMock()
        # press_element goes through `with_self_healing` decorator, which needs a
        # working strategy_manager.locate; mock it to return a located result.
        mock_result = MagicMock()
        mock_result.is_coordinates = False
        mock_result.value = MagicMock()
        mock_result.strategy = MagicMock()
        ak.strategy_manager.locate = MagicMock(return_value=iter([mock_result]))

        assert ak._ai_self_heal("Login", "press_element", (), {}, shot) is True
        # Healed keyword is recorded as a breadcrumb (may appear more than once —
        # the inner press_element records one, and _log_heal_outcome records another).
        steps = list(ak._recent_steps)
        assert any(s == ("press_element", ["Login"]) for s in steps)
