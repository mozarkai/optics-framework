"""Keywords with popup rules: a fake screen, a fake strategy manager and a fake driver, no device."""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from optics_framework.api import ActionKeyword, Verifier
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


def _builder(phone, rules):
    out = tempfile.mkdtemp()
    builder = MagicMock()
    builder.session_config = Config(popup_rules=list(rules), execution_output_path=out)
    builder.get_driver.return_value = phone.driver
    return builder, out


def keyword(phone, *rules):
    builder, out = _builder(phone, rules)
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
        optics=SimpleNamespace(build=lambda cls: ak if cls is ActionKeyword else SimpleNamespace()),
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
    ak = _with_modules(ak, phone, {})
    with pytest.raises(OpticsError) as caught:
        ak.press_element("Balance card")
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


CHAT = "<hierarchy><n text='Balance' package='in.app'/><n text='Chat with us'/><n text='Minimise'/></hierarchy>"
MINIMISE = (30, 40)


class PhoneWithChat(Phone):
    """Closing Rate Us brings up a chat bubble that takes any tap on the card until it is minimised."""

    def __init__(self):
        super().__init__()
        self.chat = False

    def _press(self, x, y, event_name=None):
        self.pressed.append((x, y))
        if (x, y) == MAYBE_LATER:
            self.popup, self.chat = False, True
        elif (x, y) == MINIMISE:
            self.chat = False
        elif (x, y) == CARD and self.chat:
            raise RuntimeError("the chat bubble took the tap")

    def locate(self, element, *args, **kwargs):
        if element == "Minimise" and self.chat:
            self.looked_up.append(element)
            yield LocateResult(MINIMISE, MagicMock())
        else:
            yield from Phone.locate(self, element, *args, **kwargs)

    def capture_pagesource(self):
        if self.chat:
            self.source_reads += 1
            return (CHAT, "ts")
        return Phone.capture_pagesource(self)


def test_the_retry_after_a_dismissed_popup_checks_the_before_each_step_rules_again():
    phone = PhoneWithChat()
    chat = rule("Chat", detect={"text_contains": ["chat with us"]}, action={"type": "tap", "target": "Minimise"}, check="before_every_step")
    ak, _ = keyword(phone, rule(), chat)
    ak.press_element("Balance card")
    assert phone.pressed == [MAYBE_LATER, MINIMISE, CARD]
    assert [(e["check"], e["rule"]) for e in ak._popup_run().drain() if e["verdict"] == "handled"] == [
        ("on_failure", "Rate Us"), ("before_every_step", "Chat")
    ]
    ak._ai_self_heal.assert_not_called()


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


CARD_NODE = "card node"


class Screen(Phone):
    """The phone as the assertions read it: the card is a page-source node with text and a state."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.driver.get_text_element.return_value = "Balance"
        self.driver.is_element_enabled.return_value = True

    def locate(self, element, *args, **kwargs):
        for result in Phone.locate(self, element, *args, **kwargs):
            yield LocateResult(CARD_NODE, result.strategy) if result.value == CARD else result

    def assert_presence(self, elements, element_type, timeout=30, rule="any"):
        for element in elements:
            next(self.locate(element))
        return True, "ts", None

    assert_visibility = assert_presence


def verifier(phone, *rules):
    """A Verifier built by build_keywords, as every entry point builds it."""
    builder, _ = _builder(phone, rules)
    session = SimpleNamespace(
        optics=SimpleNamespace(build=lambda cls: cls(builder) if cls in (ActionKeyword, Verifier) else object()),
        modules=ModuleData(modules={}), elements=ElementData(), config_handler=MagicMock(), driver=phone.driver,
    )
    keywords = build_keywords(session)
    keywords.action_keyword.strategy_manager = keywords.verifier.strategy_manager = phone
    return keywords.verifier, keywords.action_keyword._popup_run


ASSERTIONS = {
    "assert_presence": lambda v: v.assert_presence("Balance card", "1"),
    "assert_visibility": lambda v: v.assert_visibility("Balance card", "1"),
    "assert_text": lambda v: v.assert_text("Balance card", "Balance", "1"),
    "is_element": lambda v: v.is_element("Balance card", "visible", 1),
    "validate_screen": lambda v: v.validate_screen("Balance card", "1"),
}


@pytest.mark.parametrize("name", ASSERTIONS)
def test_an_assertion_that_finds_nothing_dismisses_the_popup_and_passes_on_the_next_look(name):
    phone = Screen()
    check, popups = verifier(phone, rule())
    assert ASSERTIONS[name](check) in (None, True)
    assert phone.pressed == [MAYBE_LATER]
    event, = popups().drain()
    assert (event["check"], event["verdict"], event["keyword"], event["element"]) == ("on_failure", "handled", name, "Balance card")


def test_an_assertion_runs_the_before_each_step_rules_before_it_looks():
    phone = Screen()
    check, popups = verifier(phone, rule(check="before_every_step"))
    check.is_element("Balance card", "enabled", 1)
    assert phone.looked_up[0] == "Maybe Later", "the popup went first, so the assertion's own lookup never failed"
    assert [(e["check"], e["verdict"]) for e in popups().drain()] == [("before_every_step", "handled")]


SOFT_CHECKS = {
    "assert_presence": ASSERTIONS["assert_presence"],
    "validate_screen": ASSERTIONS["validate_screen"],
    "is_element visible": ASSERTIONS["is_element"],
    "is_element invisible": lambda v: v.is_element("Balance card", "invisible", 1),
}


@pytest.mark.parametrize("name", SOFT_CHECKS)
def test_an_assertion_behind_a_dialog_no_rule_covers_fails_with_e0206(name):
    phone = Screen()
    check, _ = verifier(phone, rule("Other", detect={"text_contains": ["something else"]}))
    with pytest.raises(OpticsError) as caught:
        SOFT_CHECKS[name](check)
    assert caught.value.code == Code.E0206
    assert phone.pressed == []


@pytest.mark.parametrize("name", SOFT_CHECKS)
def test_an_assertion_behind_a_sensitive_popup_ends_the_run_with_e0408(name):
    phone = Screen()
    check, popups = verifier(phone, rule(sensitive=True))
    with pytest.raises(OpticsError) as caught:
        SOFT_CHECKS[name](check)
    assert caught.value.code == Code.E0408
    assert phone.pressed == []
    assert popups().stop


@pytest.mark.parametrize("name", SOFT_CHECKS)
def test_an_assertion_whose_popup_module_the_session_does_not_have_fails_with_e0601(name):
    phone = Screen()
    check, _ = verifier(phone, rule(action={"type": "module", "target": "Close Rate Us"}))
    with pytest.raises(OpticsError) as caught:
        SOFT_CHECKS[name](check)
    assert caught.value.code == Code.E0601


def test_an_invisible_check_does_not_pass_on_an_element_a_popup_hides():
    phone = Screen()
    check, popups = verifier(phone, rule())
    with pytest.raises(OpticsError) as caught:
        check.is_element("Balance card", "invisible", 1)
    assert caught.value.code == Code.E0401
    assert phone.pressed == [MAYBE_LATER]
    assert [e["verdict"] for e in popups().drain()] == ["handled"]


def test_an_invisible_check_passes_on_a_screen_with_no_popup_after_one_event_saying_so():
    phone = Screen(popup=False)

    def nothing(element, *args, **kwargs):
        raise OpticsError(Code.E0201, message="gone")
        yield  # a generator, so the error comes on the first step of the iteration as it does for a real lookup

    phone.locate = nothing
    check, popups = verifier(phone, rule())
    check.is_element("Balance card", "invisible", 1)
    assert [e["verdict"] for e in popups().drain()] == ["none"]


BAD_ARGUMENTS = {
    "assert_presence rule": (lambda v: v.assert_presence("Balance card", "1", rule="both"), OpticsError),
    "assert_visibility rule": (lambda v: v.assert_visibility("Balance card", "1", rule="both"), OpticsError),
    "validate_element rule": (lambda v: v.validate_element("Balance card", "1", rule="both"), OpticsError),
    "assert_presence timeout": (lambda v: v.assert_presence("Balance card", "soon"), ValueError),
    "assert_text timeout": (lambda v: v.assert_text("Balance card", "Balance", "soon"), ValueError),
    "is_element timeout": (lambda v: v.is_element("Balance card", "visible", "soon"), ValueError),
}


@pytest.mark.parametrize("name", BAD_ARGUMENTS)
def test_an_assertion_it_cannot_run_is_refused_before_any_popup_rule_acts(name):
    phone = Screen()
    check, popups = verifier(phone, rule(check="before_every_step"))
    call, error = BAD_ARGUMENTS[name]
    with pytest.raises(error):
        call(check)
    assert phone.pressed == []
    assert popups().drain() == []


def test_validate_screen_with_a_bad_rule_returns_false_before_any_popup_rule_acts():
    phone = Screen()
    check, popups = verifier(phone, rule(check="before_every_step"))
    assert check.validate_screen("Balance card", "1", rule="both") is False
    assert phone.pressed == []


def test_an_assertion_on_a_locator_that_is_not_valid_asks_no_popup_rule():
    phone = Screen()

    def not_valid(element, *args, **kwargs):
        raise OpticsError(Code.E0205, message="'id:' names no id; write it as id:<id>.")
        yield  # a generator, so the error comes on the first step of the iteration as it does for a real lookup

    phone.locate = not_valid
    check, popups = verifier(phone, rule())
    with pytest.raises(OpticsError) as caught:
        check.assert_presence("id:", "1")
    assert caught.value.code == Code.E0205
    assert phone.pressed == []
    assert popups().drain() == []

def test_an_assertion_without_rules_never_reads_the_page_source():
    phone = Screen()
    check, _ = verifier(phone)
    with pytest.raises(OpticsError) as caught:
        check.assert_presence("Balance card", "1")
    assert caught.value.code == Code.E0201
    assert phone.source_reads == 0
