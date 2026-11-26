from dataclasses import dataclass

import pytest

from optics_framework.common import ai_self_heal as ash
from optics_framework.common import test_context
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.ai_self_heal import (
    AISelfHealHandler,
    HealContext,
    HealKeywordSpec,
    HEAL_ACTION_SCHEMA,
)
from optics_framework.common.runner.test_runnner import pop_heal
from tests.units.fakes import Node, Phone, ScriptedModel, phone_keywords

LOGIN = (50, 90)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
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
        assert images
        assert isinstance(images[0], (bytes, bytearray))
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


class TestPromptContext:

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


SCROLL_DOWN = {"action": "keyword", "keyword": "scroll", "params": ["down"], "completed": False, "reason": "reveal"}
PRESS_LOGIN = {"action": "keyword", "keyword": "press_element", "params": ["Login"], "completed": True, "reason": "press it"}


def below_the_fold():
    return Phone([Node("Welcome", (50, 10))], below=[Node("Login", LOGIN)])


class TestPressElement:
    def test_a_heal_that_scrolls_to_the_element_and_presses_it(self, tmp_path):
        phone = below_the_fold()
        phone_keywords(phone, tmp_path, llm=ScriptedModel(SCROLL_DOWN, PRESS_LOGIN)).action_keyword.press_element("Sign in")
        assert (phone.swipes, phone.taps) == (["down"], [LOGIN])

    def test_a_curation_that_fails_keeps_every_step_of_the_heal(self, tmp_path):
        model = ScriptedModel(SCROLL_DOWN, PRESS_LOGIN, ConnectionResetError("the curation call dropped"))
        keywords = phone_keywords(below_the_fold(), tmp_path, llm=model)
        keywords.action_keyword.press_element("Sign in")
        assert [step["keyword"] for step in pop_heal(keywords.keyword_map)["suggested_steps"]] == ["scroll", "press_element"]

    def test_an_llm_that_cannot_be_built_leaves_the_step_to_fail_unhealed(self, tmp_path):
        keywords = phone_keywords(below_the_fold(), tmp_path, llm=ImportError("the gemini extra is not installed"))
        with pytest.raises(OpticsError) as caught:
            keywords.action_keyword.press_element("Sign in")
        assert caught.value.code == Code.E0201

    def test_an_llm_config_error_stops_the_session(self, tmp_path):
        phone, llm = Phone([]), OpticsError(Code.E0501, "credentials_path does not exist")
        with pytest.raises(OpticsError) as caught:
            phone_keywords(phone, tmp_path, llm=llm)
        assert caught.value.code == Code.E0501

    def test_the_heal_prompt_names_the_suite_element(self, tmp_path):
        model = ScriptedModel()
        action = phone_keywords(Phone([]), tmp_path, llm=model).action_keyword
        named = test_context.element_names.set({"//*[@content-desc='Chronometer']": "stopwatch_tab"})
        try:
            with pytest.raises(OpticsError):
                action.press_element("//*[@content-desc='Chronometer']")
        finally:
            test_context.element_names.reset(named)
        assert "TARGET ELEMENT NAME (from the test suite): stopwatch_tab" in model.prompts[0]
