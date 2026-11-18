"""Unit tests for the keyword-based AI self-heal handler and its ActionKeyword wiring.

No network: a scripted fake LLM drives the handler and a fake keyword executor records the
keyword lines dispatched. Covers each action type, the bounded loop, give_up, done, malformed
JSON, LLM/executor errors, missing-screenshot inertness, page-source injection, the
ActionKeyword inert/active paths, and ActionKeyword._heal_execute's own shlex parsing,
dispatch-table lookup, and error surfacing in isolation.
"""
from dataclasses import dataclass

import numpy as np
import pytest

from optics_framework.common import ai_self_heal as ash
from optics_framework.common import test_context
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


class TestHandlerActions:


    def test_pressing_the_row_the_locator_names_completes_the_heal(self):
        rows = [
            {"label": "Timers", "cls": "FrameLayout", "act": ["tap"], "rid": "app:id/tab_menu_timer", "bounds": [0, 0, 100, 20]},
            {"label": "Stopwatch", "cls": "FrameLayout", "act": ["tap"], "rid": "app:id/tab_menu_stopwatch", "bounds": [0, 30, 100, 50]},
        ]
        llm = FakeLLM([
            {"action": "keyword", "keyword": "press_element", "target": "e2", "completed": False, "reason": "tab first"},
            {"action": "keyword", "keyword": "press_element", "target": "e1", "completed": True, "reason": "drift"},
        ])
        ctx = HealContext(intent_keyword="press_element", element="//*[@content-desc='Stop watch']")
        res = AISelfHealHandler(llm, FakeExecutor(), _catalog).heal(ctx, _shots, _no_ps, lambda: rows)
        assert res.ok is True
        assert llm.calls == 1


class TestSuggestedSteps:


    def test_budget_exhausted_reports_passed_steps_uncurated(self):
        llm = FakeLLM([
            {"action": "keyword", "keyword": "scroll", "params": ["down"], "reason": "x"}
        ] * 2)
        res = AISelfHealHandler(
            llm, FakeExecutor(), lambda: _catalog(), max_steps=2, curate=True
        ).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert res.suggested_steps == [("scroll", ["down"]), ("scroll", ["down"])]


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

    def _prompt_with_rows(self, element, rows, element_name=""):
        llm = FakeLLM([{"action": "give_up", "reason": "x"}])
        ctx = HealContext(intent_keyword="press_element", intent_params=[element], element=element, element_name=element_name)
        AISelfHealHandler(llm, FakeExecutor(), lambda: _catalog()).heal(ctx, _shots, _no_ps, lambda: rows)
        return llm.last_prompt

    def _closest(self, *args, **kwargs):
        return self._prompt_with_rows(*args, **kwargs).split(ash.CLOSEST_ROWS_HEADER, 1)[1].strip().splitlines()

    def test_no_element_name_line_without_a_name(self):
        assert "TARGET ELEMENT NAME" not in self._prompt_with_rows("Login", [])

    def test_system_prompt_keeps_the_visible_target_rule_without_rows(self):
        llm = FakeLLM([{"action": "give_up", "reason": "x"}])
        AISelfHealHandler(llm, FakeExecutor(), lambda: _catalog()).heal(_ctx(), _shots, lambda: 'Button "Login"')
        assert "If the target is visible, act on it" in llm.last_system
        assert "CURRENT SCREEN row matches" not in llm.last_system


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

        # scroll is non-locating, so `_heal_execute` calls the real method; let it
        # run against the mocked `ak.driver` and assert the call reached the driver
        # with the parsed params (`_heal_dispatch` binds the real method at __init__,
        # so reassigning `ak.scroll` here would never be consulted — see press_element
        # below for the correct way to intercept a locating keyword).
        # press_element runs `_locate_and_act`, which needs a working
        # strategy_manager.locate; mock it to return a located result.
        mock_result = MagicMock()
        mock_result.is_coordinates = False
        mock_result.value = MagicMock()
        mock_result.strategy = MagicMock()
        ak.strategy_manager.locate = MagicMock(return_value=iter([mock_result]))

        assert ak._ai_self_heal("Login", "press_element", (), shot) is True
        ak.driver.scroll.assert_called_once_with("down", 1000, event_name=None)
        # Healed keyword is recorded as a breadcrumb (may appear more than once —
        # the inner press_element records one, and _log_heal_outcome records another).
        steps = list(ak._recent_steps)
        assert any(s == ("press_element", ["Login"]) for s in steps)

    def test_heal_prompt_carries_the_suite_element_name(self):
        llm = FakeLLM([{"action": "give_up", "reason": "x"}])
        llm.instances = [object()]
        ak = _make_action_keyword(ai_self_heal=True, llm=llm)
        ak.strategy_manager.capture_pagesource = MagicMock(return_value=None)
        token = test_context.element_names.set({"//*[@content-desc='Chronometer']": "stopwatch_tab"})
        try:
            ak._ai_self_heal("//*[@content-desc='Chronometer']", "press_element", (), np.zeros((10, 10, 3), dtype=np.uint8))
        finally:
            test_context.element_names.reset(token)
        assert "TARGET ELEMENT NAME (from the test suite): stopwatch_tab" in llm.last_prompt


# --------------------------------------------------------------------------------------------
# ActionKeyword._heal_execute: shlex parsing, dispatch-table lookup, error surfacing.
#
# These substitute entries in `_heal_dispatch` directly rather than reassigning
# `ak.<method>` — `_heal_execute` looks the callable up fresh from `_heal_dispatch` on
# every call, so that's the correct interception point (see the comment on
# `test_active_press_element_heals` above for why reassigning the instance attribute
# doesn't work).
# --------------------------------------------------------------------------------------------


class TestHealExecute:


    def test_unbalanced_quotes_return_parse_error(self):
        ak = _make_action_keyword(ai_self_heal=True, llm=MagicMock(instances=[object()]))
        result = ak._heal_execute('scroll "unterminated')
        assert result.ok is False
        assert "Parse error" in result.message
