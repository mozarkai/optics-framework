"""Unit tests for the AI self-heal handler and its ActionKeyword wiring.

No network: a scripted fake LLM drives the handler and a fake driver records the primitive
calls. Covers each action type, the bounded loop, give_up, malformed JSON, LLM/driver errors,
missing-screenshot inertness, page-source injection, and the ActionKeyword inert/active paths.
"""
import json

import numpy as np
import pytest

from optics_framework.common import ai_self_heal as ash
from optics_framework.common.ai_self_heal import (
    AISelfHealHandler,
    HealContext,
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


class FakeDriver:
    """Records the driver primitive calls the handler dispatches."""

    def __init__(self):
        self.calls = []

    def press_percentage_coordinates(self, px, py, repeat, event_name=None):
        self.calls.append(("tap", px, py, repeat))

    def enter_text(self, text, event_name=None):
        self.calls.append(("enter_text", text))

    def swipe_percentage(self, x, y, direction, length, event_name=None):
        self.calls.append(("swipe", x, y, direction, length))

    def scroll(self, direction, duration, event_name=None):
        self.calls.append(("scroll", direction, duration))


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
    def test_tap_completes(self):
        llm = FakeLLM([{"action": "tap", "percent_x": 50, "percent_y": 60, "reason": "press it"}])
        driver = FakeDriver()
        res = AISelfHealHandler(llm, driver).heal(_ctx(), _shots, _no_ps)
        assert res.ok is True
        assert driver.calls == [("tap", 50.0, 60.0, 1)]
        assert llm.calls == 1

    def test_type_taps_then_enters(self):
        llm = FakeLLM([
            {"action": "type", "percent_x": 10, "percent_y": 20, "text": "hello", "reason": "fill"}
        ])
        driver = FakeDriver()
        res = AISelfHealHandler(llm, driver).heal(_ctx(), _shots, _no_ps)
        assert res.ok is True
        assert driver.calls == [("tap", 10.0, 20.0, 1), ("enter_text", "hello")]

    def test_scroll_then_tap(self):
        llm = FakeLLM([
            {"action": "scroll", "direction": "down", "reason": "reveal"},
            {"action": "tap", "percent_x": 40, "percent_y": 80, "reason": "now press"},
        ])
        driver = FakeDriver()
        res = AISelfHealHandler(llm, driver, max_steps=2).heal(_ctx(), _shots, _no_ps)
        assert res.ok is True
        assert driver.calls == [("scroll", "down", 1000), ("tap", 40.0, 80.0, 1)]
        assert llm.calls == 2

    def test_swipe_uses_int_coords_and_default_length(self):
        llm = FakeLLM([
            {"action": "swipe", "percent_x": 50.7, "percent_y": 50.2, "direction": "up", "reason": "x"},
            {"action": "give_up", "reason": "done trying"},
        ])
        driver = FakeDriver()
        res = AISelfHealHandler(llm, driver, max_steps=2).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert driver.calls[0] == ("swipe", 50, 50, "up", 50)

    def test_give_up_fails(self):
        llm = FakeLLM([{"action": "give_up", "reason": "blocked"}])
        res = AISelfHealHandler(llm, FakeDriver()).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert "blocked" in res.message

    def test_malformed_action_treated_as_give_up(self):
        llm = FakeLLM([{"action": "frobnicate", "reason": "nonsense"}])
        driver = FakeDriver()
        res = AISelfHealHandler(llm, driver).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert driver.calls == []

    def test_step_budget_exhausted(self):
        llm = FakeLLM([{"action": "scroll", "direction": "down", "reason": "x"}] * 2)
        driver = FakeDriver()
        res = AISelfHealHandler(llm, driver, max_steps=2).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert llm.calls == 2
        assert driver.calls == [("scroll", "down", 1000), ("scroll", "down", 1000)]


class TestHandlerErrorHandling:
    def test_llm_error_returns_not_ok(self):
        class BoomLLM:
            def generate_json(self, *a, **k):
                raise OpticsError(Code.E0801, message="bad json")

        res = AISelfHealHandler(BoomLLM(), FakeDriver()).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert "bad json" in res.message

    def test_driver_error_returns_not_ok(self):
        class BadDriver(FakeDriver):
            def press_percentage_coordinates(self, *a, **k):
                raise RuntimeError("device offline")

        llm = FakeLLM([{"action": "tap", "percent_x": 1, "percent_y": 1, "reason": "x"}])
        res = AISelfHealHandler(llm, BadDriver()).heal(_ctx(), _shots, _no_ps)
        assert res.ok is False
        assert "device offline" in res.message

    def test_no_screenshot_is_inert(self):
        llm = FakeLLM([{"action": "tap", "percent_x": 1, "percent_y": 1, "reason": "x"}])
        res = AISelfHealHandler(llm, FakeDriver()).heal(_ctx(), lambda: None, _no_ps)
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
        AISelfHealHandler(llm, FakeDriver()).heal(ctx, _shots, lambda: ps)
        assert "login_field" in llm.last_prompt
        assert "CURRENT SCREEN ELEMENTS" in llm.last_prompt
        assert "enter_text" in llm.last_prompt
        assert "press_element" in llm.last_prompt  # recent step
        assert "TextDetectionStrategy" in llm.last_prompt
        assert "last-resort" in llm.last_system.lower()


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

    def test_active_tap_heals(self, monkeypatch):
        monkeypatch.setattr(ash.time, "sleep", lambda *_a, **_k: None)
        llm = FakeLLM([{"action": "tap", "percent_x": 25, "percent_y": 75, "reason": "press"}])
        llm.instances = [object()]  # InstanceFallback-like truthiness gate
        ak = _make_action_keyword(ai_self_heal=True, llm=llm)
        # No page source from the mocked strategy manager.
        ak.strategy_manager.capture_pagesource = MagicMock(return_value=None)
        shot = np.zeros((10, 10, 3), dtype=np.uint8)
        assert ak._ai_self_heal("Login", "press_element", (), {}, shot) is True
        ak.driver.press_percentage_coordinates.assert_called_once()
        # Healed keyword is recorded as a breadcrumb.
        assert list(ak._recent_steps) == [("press_element", ["Login"])]
