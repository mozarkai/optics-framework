"""press_element with popup rules: a fake screen, a fake strategy manager and a fake driver, no device."""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from optics_framework.api import ActionKeyword
from optics_framework.api.keywords import build_keywords
from optics_framework.common.config_handler import Config
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.models import ElementData, ModuleData
from optics_framework.common.strategies import LocateResult
from optics_framework.engines.drivers.appium_popup import AppiumScreen
from tests.units.popup_helpers import rule

PLAIN = "<hierarchy><n text='Balance' package='in.app'/></hierarchy>"
RATE_US = ("<hierarchy><n text='Balance' package='in.app'/><n resource-id='android:id/alertTitle' text='Rate Us'/>"
           "<n text='Maybe Later'/></hierarchy>")
MAYBE_LATER, CARD = (10, 20), (1, 2)


class Phone:
    """A screen with a popup that covers the card until 'Maybe Later' is tapped. With ``comes_back`` it returns after each dismissal."""

    def __init__(self, popup=True, comes_back=False):
        self.popup, self.comes_back = popup, comes_back
        self.looked_up, self.pressed, self.source_reads = [], [], 0
        self.driver = MagicMock()
        self.driver.press_coordinates.side_effect = self._press
        self.driver.read_alert.return_value = None
        self.driver.popup_screen.side_effect = AppiumScreen

    def _press(self, x, y, event_name=None):
        self.pressed.append((x, y))
        if (x, y) == MAYBE_LATER:
            self.popup = False

    def locate(self, element, *args, **kwargs):
        self.looked_up.append(element)
        if self.comes_back and element == "Balance card":
            self.popup = True
        if element == "Maybe Later" and self.popup:
            yield LocateResult(MAYBE_LATER, MagicMock())
        elif element == "Balance card" and not self.popup:
            yield LocateResult(CARD, MagicMock())
        else:
            raise OpticsError(Code.E0201, message=f"{element} not found")

    sees_screen = True

    def require_screen(self):
        pass

    def capture_pagesource(self):
        self.source_reads += 1
        return (RATE_US if self.popup else PLAIN, "ts")

    def capture_pagesource_from(self):
        return self.capture_pagesource(), None

    def capture_screenshot(self):
        return np.zeros((8, 8, 3), dtype="uint8")


def keyword(phone, *rules):
    out = tempfile.mkdtemp()
    builder = MagicMock()
    builder.session_config = Config(popup_rules=list(rules), execution_output_path=out)
    builder.get_driver.return_value = phone.driver
    ak = ActionKeyword(builder)
    ak.strategy_manager = phone
    ak._ai_self_heal = MagicMock(return_value=False)
    return ak, out


def test_a_step_that_finds_nothing_dismisses_the_popup_and_passes_on_the_retry_with_one_popup_event():
    phone = Phone()
    ak, out = keyword(phone, rule())
    ak.press_element("Balance card")
    assert phone.pressed == [MAYBE_LATER, CARD]
    event, = ak._popup_run().drain()
    assert (event["verdict"], event["rule"], event["popup_gone"], event["keyword"], event["element"]) == \
        ("handled", "Rate Us", True, "press_element", "Balance card")
    assert all(os.path.exists(os.path.join(out, name)) for name in event["screenshots"])
    assert len(event["screenshots"]) == 2
    ak._ai_self_heal.assert_not_called()


def test_a_step_whose_action_ran_and_failed_is_never_retried_after_the_popup_is_dismissed():
    phone = Phone()

    def covered_card(element, *args, **kwargs):
        if element == "Balance card":
            yield LocateResult(CARD, MagicMock())
        else:
            yield from Phone.locate(phone, element)

    def press(x, y, event_name=None):
        phone.pressed.append((x, y))
        if (x, y) == CARD and phone.popup:
            raise RuntimeError("the popup took the tap")
        if (x, y) == MAYBE_LATER:
            phone.popup = False

    phone.locate = covered_card
    phone.driver.press_coordinates.side_effect = press
    ak, _ = keyword(phone, rule())
    with pytest.raises(OpticsError) as caught:
        ak.press_element("Balance card")
    assert caught.value.code == Code.X0201
    assert phone.pressed == [CARD, MAYBE_LATER], "the popup was dismissed, but the tap was not repeated"
    assert [e["verdict"] for e in ak._popup_run().drain()] == ["handled"]


def _with_modules(ak, phone, modules):
    session = SimpleNamespace(
        optics=SimpleNamespace(build=lambda cls: ak if cls is ActionKeyword else object()),
        modules=ModuleData(modules=modules), elements=ElementData(), config_handler=MagicMock(), driver=phone.driver,
    )
    return build_keywords(session).action_keyword


def test_a_rule_that_runs_a_module_dismisses_the_popup_with_the_modules_own_steps():
    phone = Phone()
    ak, _ = keyword(phone, rule(action={"type": "module", "target": "Close Rate Us"}))
    _with_modules(ak, phone, {"Close Rate Us": [("press element", ["Maybe Later"])]}).press_element("Balance card")
    assert phone.pressed == [MAYBE_LATER, CARD]
    assert [(e["verdict"], e["action"]) for e in ak._popup_run().drain() if e["verdict"] != "none"] == [
        ("handled", "module Close Rate Us")
    ]


def test_a_rule_whose_module_the_session_does_not_have_fails_the_step_with_e0601_not_a_popup_that_stayed():
    phone = Phone()
    ak, _ = keyword(phone, rule(action={"type": "module", "target": "Close Rate Us"}))
    with pytest.raises(OpticsError) as caught:
        _with_modules(ak, phone, {}).press_element("Balance card")
    assert caught.value.code == Code.E0601
    assert "Close Rate Us" in str(caught.value)
    assert phone.pressed == []


def test_without_rules_nothing_changes_and_the_page_source_is_never_read():
    phone = Phone()
    ak, _ = keyword(phone)
    with pytest.raises(OpticsError) as caught:
        ak.press_element("Balance card")
    assert caught.value.code == Code.E0201
    assert phone.source_reads == 0
    ak._ai_self_heal.assert_called_once()


def test_a_dialog_no_rule_covers_fails_the_step_with_e0206_and_skips_self_heal():
    phone = Phone()
    ak, out = keyword(phone, rule("Other", detect={"text_contains": ["something else"]}))
    with pytest.raises(OpticsError) as caught:
        ak.press_element("Balance card")
    assert caught.value.code == Code.E0206
    assert phone.pressed == []
    ak._ai_self_heal.assert_not_called()
    event, = ak._popup_run().drain()
    assert all(os.path.exists(os.path.join(out, name)) for name in (*event["screenshots"], event["page_source"]))


def test_a_rule_that_checks_before_each_step_clears_the_popup_before_the_lookup():
    phone = Phone()
    ak, _ = keyword(phone, rule(check="before_every_step"))
    ak.press_element("Balance card")
    assert phone.pressed == [MAYBE_LATER, CARD]
    assert phone.looked_up[0] == "Maybe Later", "the popup went first, so the step's own lookup never failed"
    event, = ak._popup_run().drain()
    assert (event["check"], event["verdict"]) == ("before_every_step", "handled")


def test_a_stop_rule_ends_the_run_through_the_error_and_the_run_record():
    phone = Phone()
    ak, _ = keyword(phone, rule(then="stop_run"))
    with pytest.raises(OpticsError) as caught:
        ak.press_element("Balance card")
    assert caught.value.code == Code.E0408
    assert caught.value.details == {"stop_run": True}
    assert ak._popup_run().stop


def test_a_screen_without_a_popup_is_left_to_self_heal_after_one_event_saying_so():
    phone = Phone(popup=False)

    def nothing(element, *a, **k):
        raise OpticsError(Code.E0201, message="gone")
        yield  # a generator, so the error comes on the first step of the iteration as it does for a real lookup

    phone.locate = nothing
    ak, _ = keyword(phone, rule())
    with pytest.raises(OpticsError) as caught:
        ak.press_element("Balance card")
    assert caught.value.code == Code.E0201
    ak._ai_self_heal.assert_called_once()
    assert [e["verdict"] for e in ak._popup_run().drain()] == ["none"]
