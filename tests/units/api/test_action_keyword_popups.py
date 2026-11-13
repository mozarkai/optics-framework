"""press_element with popup rules: a fake screen, a fake strategy manager and a fake driver, no device."""
import os
import tempfile
from unittest.mock import MagicMock

import numpy as np
import pytest

from optics_framework.api import ActionKeyword
from optics_framework.common import fallback
from optics_framework.common.config_handler import Config
from optics_framework.common.error import POPUP_CODES, Code, OpticsError
from optics_framework.common.strategies import LocateResult
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

    def capture_pagesource(self):
        self.source_reads += 1
        return (RATE_US if self.popup else PLAIN, "ts")

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
    assert os.path.exists(os.path.join(out, "popup_1_before.png")) and os.path.exists(os.path.join(out, "popup_1_after.png"))
    ak._ai_self_heal.assert_not_called()


def test_without_rules_nothing_changes_and_the_page_source_is_never_read():
    phone = Phone()
    ak, _ = keyword(phone)
    with pytest.raises(OpticsError) as caught:
        ak.press_element("Balance card")
    assert caught.value.code == Code.E0201 and phone.source_reads == 0
    ak._ai_self_heal.assert_called_once()


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


def test_a_dialog_no_rule_covers_fails_the_step_with_e0206_and_skips_self_heal():
    phone = Phone()
    ak, out = keyword(phone, rule("Other", detect={"text_contains": ["something else"]}))
    with pytest.raises(OpticsError) as caught:
        ak.press_element("Balance card")
    assert caught.value.code == Code.E0206 and phone.pressed == []
    ak._ai_self_heal.assert_not_called()
    assert os.path.exists(os.path.join(out, "popup_1_before.png")) and os.path.exists(os.path.join(out, "popup_1_source.xml"))


def test_a_popup_that_keeps_coming_back_fails_the_step_with_e0207_after_the_per_step_limit():
    phone = Phone(comes_back=True)
    ak, _ = keyword(phone, rule(max_per_step=2))
    with pytest.raises(OpticsError) as caught:
        ak.press_element("Balance card")
    assert caught.value.code == Code.E0207
    assert phone.pressed == [MAYBE_LATER, MAYBE_LATER]
    assert [e["verdict"] for e in ak._popup_run().drain()] == ["handled", "handled", "limit"]
    ak._ai_self_heal.assert_not_called()


def test_a_stop_rule_ends_the_run_through_the_error_and_the_run_record():
    phone = Phone()
    ak, _ = keyword(phone, rule(then="stop_run"))
    with pytest.raises(OpticsError) as caught:
        ak.press_element("Balance card")
    assert caught.value.code == Code.E0208 and caught.value.details == {"stop_run": True}
    assert ak._popup_run().stop


def test_a_rule_that_checks_before_each_step_clears_the_popup_before_the_lookup():
    phone = Phone()
    ak, _ = keyword(phone, rule(check="before_every_step"))
    ak.press_element("Balance card")
    assert phone.pressed == [MAYBE_LATER, CARD]
    assert phone.looked_up[0] == "Maybe Later", "the popup went first, so the step's own lookup never failed"
    event, = ak._popup_run().drain()
    assert (event["check"], event["verdict"]) == ("before_every_step", "handled")


def test_the_runners_run_record_receives_the_events_when_it_owns_the_run():
    from optics_framework.common import test_context
    from optics_framework.common.popup_guard import PopupRun
    run = PopupRun()
    token = test_context.popup_run.set(run)
    try:
        phone = Phone()
        ak, _ = keyword(phone, rule())
        ak.press_element("Balance card")
    finally:
        test_context.popup_run.reset(token)
    assert len(run.events) == 1 and not ak._own_popup_run.events


@pytest.mark.parametrize("code, advances", [
    (Code.E0201, True), (Code.X0201, True), (Code.E0205, True),
    *[(code, False) for code in POPUP_CODES],
])
def test_the_fallback_ladder_tries_the_next_value_only_for_element_not_found(code, advances):
    tried = []

    def call(combo):
        tried.append(combo)
        if combo == ("first",):
            raise OpticsError(code, message="x")
        return "ok"

    if advances:
        assert fallback.run([["first", "second"]], call, "keyword 'press_element'") == "ok"
        assert tried == [("first",), ("second",)]
    else:
        with pytest.raises(OpticsError) as caught:
            fallback.run([["first", "second"]], call, "keyword 'press_element'")
        assert caught.value.code == code and tried == [("first",)]


def test_a_target_in_the_page_source_is_tapped_by_its_coordinates_without_asking_the_locate_ladder():
    phone = Phone()
    phone.capture_pagesource = lambda: (RATE_US.replace("<n text='Maybe Later'/>", "<n text='Maybe Later' bounds='[0,0][20,40]'/>")
                                        if phone.popup else PLAIN, "ts")
    ak, _ = keyword(phone, rule())
    ak.press_element("Balance card")
    assert phone.pressed == [MAYBE_LATER, CARD]
    assert "Maybe Later" not in phone.looked_up
