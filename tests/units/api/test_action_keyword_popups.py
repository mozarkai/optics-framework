"""Keywords with popup rules on a fake phone: the real keywords, strategies and guard, a fake driver and element source."""
import os

import pytest

from optics_framework.common import test_context
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.popup_guard import PopupRun
from tests.units.fakes import ScriptedModel, Layer, Node, Phone, phone_keywords
from tests.units.popup_helpers import rule

CARD, MAYBE_LATER, MINIMISE = (1, 2), (10, 20), (30, 40)
CHAT_RULE = rule("Chat", detect={"text_contains": ["chat with us"]}, action={"type": "tap", "target": "Minimise"},
                 check="before_every_step")
NOT_COVERED = rule("Other", detect={"text_contains": ["something else"]})


def app():
    return [Node("Balance card", CARD)]


def chat():
    """A chat bubble that takes any tap on the app until it is minimised."""
    return Layer([Node("Chat with us", (60, 60)), Node("Minimise", MINIMISE, closes=True)], hides=False)


def rate_us(hides=True, then=None):
    return Layer([Node("Rate Us", (50, 50), "android:id/alertTitle"), Node("Maybe Later", MAYBE_LATER, closes=True)],
                 hides, then)


@pytest.fixture
def run():
    popups = PopupRun(id="run1")
    bound = test_context.popup_run.set(popups)
    yield popups
    test_context.popup_run.reset(bound)


@pytest.fixture
def model():
    return ScriptedModel()


@pytest.fixture
def keywords(tmp_path, model, fake_time):
    def build(phone, *rules, modules=None, heal=True, ocr=False):
        return phone_keywords(phone, tmp_path, rules, modules, model if heal else None, ocr)
    return build


def seen(events, *keys):
    return [tuple(event[key] for key in keys) for event in events]


class TestPressElement:
    def test_a_popup_that_hides_the_element(self, keywords, run, model, tmp_path):
        phone = Phone(app(), rate_us())
        keywords(phone, rule()).action_keyword.press_element("Balance card")
        assert phone.taps == [MAYBE_LATER, CARD]
        event, = run.drain()
        assert seen([event], "check", "rule", "verdict", "popup_gone", "keyword", "element", "action") == [
            ("on_failure", "Rate Us", "handled", True, "press_element", "Balance card", "tap Maybe Later")]
        assert event["screenshots"] == ["popup_run1_1_before.png", "popup_run1_1_after.png"]
        assert all(os.path.exists(tmp_path / name) for name in event["screenshots"])
        assert model.prompts == []

    def test_a_tap_target_only_ocr_reads_is_found_through_the_locate_ladder(self, keywords, run):
        popup = Layer([Node("Rate Us", (50, 50), "android:id/alertTitle"), Node("Maybe Later", MAYBE_LATER, closes=True, ocr=True)])
        phone = Phone(app(), popup)
        keywords(phone, rule(), ocr=True).action_keyword.press_element("Balance card")
        assert phone.taps == [MAYBE_LATER, CARD]
        assert seen(run.drain(), "verdict") == [("handled",)]

    def test_a_popup_that_takes_the_tap_is_dismissed_but_the_tap_is_not_repeated(self, keywords, run, model):
        phone = Phone(app(), rate_us(hides=False))
        with pytest.raises(OpticsError) as caught:
            keywords(phone, rule()).action_keyword.press_element("Balance card")
        assert caught.value.code == Code.X0201
        assert phone.taps == [CARD, MAYBE_LATER]
        assert seen(run.drain(), "verdict") == [("handled",)]
        assert len(model.prompts) == 1

    def test_a_rule_that_runs_a_module(self, keywords, run):
        phone = Phone(app(), rate_us())
        close = rule(action={"type": "module", "target": "Close Rate Us"})
        keywords(phone, close, modules={"Close Rate Us": [("press element", ["Maybe Later"])]}).action_keyword.press_element(
            "Balance card")
        assert phone.taps == [MAYBE_LATER, CARD]
        assert [e for e in seen(run.drain(), "verdict", "action") if e[0] != "none"] == [("handled", "module Close Rate Us")]

    def test_a_rule_whose_module_the_session_does_not_have(self, keywords, run):
        phone = Phone(app(), rate_us())
        with pytest.raises(OpticsError) as caught:
            keywords(phone, rule(action={"type": "module", "target": "Close Rate Us"})).action_keyword.press_element(
                "Balance card")
        assert caught.value.code == Code.E0601
        assert "Close Rate Us" in str(caught.value)
        assert phone.taps == []

    def test_no_rules(self, keywords, run):
        phone = Phone(app(), rate_us())
        with pytest.raises(OpticsError) as caught:
            keywords(phone, heal=False).action_keyword.press_element("Balance card")
        assert caught.value.code == Code.E0201
        assert phone.source_reads == 0

    def test_a_dialog_no_rule_covers(self, keywords, run, model, tmp_path):
        phone = Phone(app(), rate_us())
        with pytest.raises(OpticsError) as caught:
            keywords(phone, NOT_COVERED).action_keyword.press_element("Balance card")
        assert caught.value.code == Code.E0206
        assert phone.taps == []
        assert model.prompts == []
        event, = run.drain()
        assert all(os.path.exists(tmp_path / name) for name in (*event["screenshots"], event["page_source"]))

    def test_a_rule_that_checks_before_each_step(self, keywords, run):
        phone = Phone(app(), rate_us())
        keywords(phone, rule(check="before_every_step")).action_keyword.press_element("Balance card")
        assert phone.taps == [MAYBE_LATER, CARD]
        assert phone.lookups == ["Balance card"]
        assert seen(run.drain(), "check", "verdict") == [("before_every_step", "handled")]

    def test_the_retry_after_a_dismissed_popup_checks_the_before_each_step_rules_again(self, keywords, run, model):
        phone = Phone(app(), rate_us(then=chat()))
        keywords(phone, rule(), CHAT_RULE).action_keyword.press_element("Balance card")
        assert phone.taps == [MAYBE_LATER, MINIMISE, CARD]
        assert [e[1:] for e in seen(run.drain(), "verdict", "check", "rule") if e[0] == "handled"] == [
            ("on_failure", "Rate Us"), ("before_every_step", "Chat")]
        assert model.prompts == []

    def test_a_rule_that_stops_the_run(self, keywords, run):
        phone = Phone(app(), rate_us())
        with pytest.raises(OpticsError) as caught:
            keywords(phone, rule(then="stop_run")).action_keyword.press_element("Balance card")
        assert (caught.value.code, caught.value.details) == (Code.E0408, {"stop_run": True})
        assert run.stop == "Popup 'Rate Us' was dismissed and then ended the run"

    def test_a_screen_without_a_popup_or_the_element(self, keywords, run, model):
        with pytest.raises(OpticsError) as caught:
            keywords(Phone([]), rule()).action_keyword.press_element("Balance card")
        assert caught.value.code == Code.E0201
        assert len(model.prompts) == 1
        assert seen(run.drain(), "verdict") == [("none",)]


ASSERTIONS = [
    pytest.param("assert_presence", lambda v: v.assert_presence("Balance card", "0"), id="assert_presence"),
    pytest.param("assert_visibility", lambda v: v.assert_visibility("Balance card", "0"), id="assert_visibility"),
    pytest.param("assert_text", lambda v: v.assert_text("Balance card", "Balance card", "0"), id="assert_text"),
    pytest.param("is_element", lambda v: v.is_element("Balance card", "visible", 0), id="is_element visible"),
    pytest.param("validate_screen", lambda v: v.validate_screen("Balance card", "0"), id="validate_screen"),
]
SOFT_CHECKS = [
    pytest.param(lambda v: v.assert_presence("Balance card", "0"), id="assert_presence"),
    pytest.param(lambda v: v.validate_screen("Balance card", "0"), id="validate_screen"),
    pytest.param(lambda v: v.is_element("Balance card", "visible", 0), id="is_element visible"),
    pytest.param(lambda v: v.is_element("Balance card", "invisible", 0), id="is_element invisible"),
]
BAD_ARGUMENTS = [
    pytest.param(lambda v: v.assert_presence("Balance card", "0", rule="both"), OpticsError, id="assert_presence rule"),
    pytest.param(lambda v: v.assert_visibility("Balance card", "0", rule="both"), OpticsError, id="assert_visibility rule"),
    pytest.param(lambda v: v.validate_element("Balance card", "0", rule="both"), OpticsError, id="validate_element rule"),
    pytest.param(lambda v: v.assert_presence("Balance card", "soon"), ValueError, id="assert_presence timeout"),
    pytest.param(lambda v: v.assert_text("Balance card", "Balance card", "soon"), ValueError, id="assert_text timeout"),
    pytest.param(lambda v: v.is_element("Balance card", "visible", "soon"), ValueError, id="is_element timeout"),
]


class TestAssertions:
    @pytest.mark.parametrize(("name", "check"), ASSERTIONS)
    def test_a_popup_that_hides_the_element(self, keywords, run, name, check):
        phone = Phone(app(), rate_us())
        assert check(keywords(phone, rule()).verifier) in (None, True)
        assert phone.taps == [MAYBE_LATER]
        assert seen(run.drain(), "check", "verdict", "keyword", "element") == [
            ("on_failure", "handled", name, "Balance card")]

    def test_a_rule_that_checks_before_each_step(self, keywords, run):
        phone = Phone(app(), rate_us())
        keywords(phone, rule(check="before_every_step")).verifier.is_element("Balance card", "enabled", 0)
        assert phone.lookups == ["Balance card"]
        assert seen(run.drain(), "check", "verdict") == [("before_every_step", "handled")]

    @pytest.mark.parametrize("check", SOFT_CHECKS)
    def test_a_dialog_no_rule_covers(self, keywords, run, check):
        phone = Phone(app(), rate_us())
        with pytest.raises(OpticsError) as caught:
            check(keywords(phone, NOT_COVERED).verifier)
        assert caught.value.code == Code.E0206
        assert phone.taps == []

    @pytest.mark.parametrize("check", SOFT_CHECKS)
    def test_a_sensitive_popup(self, keywords, run, check):
        phone = Phone(app(), rate_us())
        with pytest.raises(OpticsError) as caught:
            check(keywords(phone, rule(sensitive=True)).verifier)
        assert caught.value.code == Code.E0408
        assert phone.taps == []
        assert run.stop == "Popup 'Rate Us' ended the run; nothing was pressed"

    @pytest.mark.parametrize("check", SOFT_CHECKS)
    def test_a_rule_whose_module_the_session_does_not_have(self, keywords, run, check):
        with pytest.raises(OpticsError) as caught:
            check(keywords(Phone(app(), rate_us()), rule(action={"type": "module", "target": "Close Rate Us"})).verifier)
        assert caught.value.code == Code.E0601

    def test_an_invisible_check_on_an_element_a_popup_hides(self, keywords, run):
        phone = Phone(app(), rate_us())
        with pytest.raises(OpticsError) as caught:
            keywords(phone, rule()).verifier.is_element("Balance card", "invisible", 0)
        assert caught.value.code == Code.E0401
        assert phone.taps == [MAYBE_LATER]
        assert seen(run.drain(), "verdict") == [("handled",)]

    def test_an_invisible_check_on_a_screen_without_a_popup(self, keywords, run):
        keywords(Phone([]), rule()).verifier.is_element("Balance card", "invisible", 0)
        assert seen(run.drain(), "verdict") == [("none",)]

    @pytest.mark.parametrize(("call", "error"), BAD_ARGUMENTS)
    def test_arguments_it_cannot_run(self, keywords, run, call, error):
        phone = Phone(app(), rate_us())
        with pytest.raises(error):
            call(keywords(phone, rule(check="before_every_step")).verifier)
        assert phone.taps == []
        assert run.drain() == []

    def test_validate_screen_with_a_rule_it_does_not_know(self, keywords, run):
        phone = Phone(app(), rate_us())
        assert keywords(phone, rule(check="before_every_step")).verifier.validate_screen("Balance card", "0", rule="both") is False
        assert phone.taps == []

    def test_a_locator_that_is_not_valid(self, keywords, run):
        phone = Phone(app(), rate_us())
        with pytest.raises(OpticsError) as caught:
            keywords(phone, rule()).verifier.assert_presence("id:", "0")
        assert caught.value.code == Code.E0205
        assert phone.taps == []
        assert run.drain() == []

    def test_no_rules(self, keywords, run):
        phone = Phone(app(), rate_us())
        with pytest.raises(OpticsError) as caught:
            keywords(phone).verifier.assert_presence("Balance card", "0")
        assert caught.value.code == Code.E0201
        assert phone.source_reads == 0
