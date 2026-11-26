"""StrategyManager on the fake phone: its element source, OCR engine and template matching are the ports."""
import pytest
from selenium.common.exceptions import InvalidSessionIdException, StaleElementReferenceException, WebDriverException

from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.elementsources.appium_find_element import AppiumFindElement
from tests.units.fakes import Node, Phone, PhoneDriver, PhoneSource, phone_screen

import optics_framework.engines.drivers.appium  # noqa: F401


def source_of(phone):
    return PhoneSource(phone, PhoneDriver(phone))


class TestLocate:
    def test_an_image_is_found_by_template_matching(self):
        screen = phone_screen(Phone([Node("", (50, 60), image="button.png")]), images=True)
        assert [(type(r.strategy).__name__, r.value) for r in screen.locate("button.png")] == [
            ("ImageDetectionStrategy", (50, 60))]

    def test_a_source_whose_code_cannot_be_read_is_still_asked(self):
        built = {}
        exec("class NoSourceFile(PhoneSource):\n    def locate(self, element, index=None):\n"
             "        return PhoneSource.locate(self, element, index)", {"PhoneSource": PhoneSource}, built)
        phone = Phone([Node("Submit", (50, 50))])
        screen = phone_screen(phone, sources=[built["NoSourceFile"](phone, PhoneDriver(phone))])
        assert [r.value.text for r in screen.locate("Submit")] == ["Submit"]


class TestAssertVisibility:
    def test_a_text_only_element_is_read_by_ocr(self, fake_time):
        passed, _, _ = phone_screen(Phone([Node("Submit", (50, 50))]), ocr=True).assert_visibility(
            ["TEXT_ONLY:Submit"], "Text", timeout=1, rule="any")
        assert passed is True

    def test_a_text_only_element_is_not_looked_up_in_the_element_tree(self, fake_time):
        phone = Phone([Node("Submit", (50, 50))])
        screen = phone_screen(phone)
        with pytest.raises(OpticsError) as caught:
            screen.assert_visibility(["TEXT_ONLY:Submit"], "Text", timeout=1, rule="any")
        assert caught.value.code == Code.E0201
        assert phone.lookups == []


class TestAssertPresence:
    def test_a_miss_without_ocr_says_ocr_is_off(self):
        screen = phone_screen(Phone([]))
        with pytest.raises(OpticsError) as caught:
            screen.assert_presence(["Submit"], "Text", timeout=0, rule="any")
        assert (caught.value.code, caught.value.summary) == (Code.E0201, "'Submit' is not on the screen.")
        assert "OCR is off" in caught.value.hint


class WaitOnly(PhoneSource):
    """A source that can only wait for elements, so an assertion round asks it through the default one-look check."""

    check_elements = ElementSourceInterface.check_elements

    def assert_elements(self, elements, timeout=30, rule="any"):
        if not any(self.phone.find(element) for element in elements):
            raise TimeoutError(elements)


def wait_only_screen():
    phone = Phone([Node("Submit", (50, 50))])
    return phone_screen(phone, sources=[WaitOnly(phone, PhoneDriver(phone))])


class TestWaitOnlySource:
    def test_one_shown_element_is_enough_for_any(self, fake_time):
        assert wait_only_screen().assert_presence(["Missing", "Submit"], "Text", timeout=1, rule="any")[0] is True

    def test_a_missing_element_fails_all(self, fake_time):
        screen = wait_only_screen()
        with pytest.raises(OpticsError) as caught:
            screen.assert_presence(["Missing", "Submit"], "Text", timeout=1, rule="all")
        assert caught.value.code == Code.E0201


class TreeLess(PhoneSource):
    """A source with a page source that cannot list the screen's elements, as AppiumFindElement."""

    def get_interactive_elements(self, filter_config=None, compact=False):
        raise NotImplementedError("does not list elements")


class TestGetInteractiveElements:
    def test_a_source_that_cannot_list_them_is_not_asked(self):
        phone = Phone([Node("Submit", (50, 50))])
        screen = phone_screen(phone, sources=[TreeLess(phone, PhoneDriver(phone))])
        with pytest.raises(OpticsError) as caught:
            screen.get_interactive_elements()
        assert str(caught.value) == "E0202: Could not list the elements on the screen"

    def test_ocr_lists_them_when_no_source_can(self):
        phone = Phone([Node("Submit", (50, 50))])
        assert phone_screen(phone, ocr=True, sources=[TreeLess(phone, PhoneDriver(phone))]).get_interactive_elements() == [
            {"text": "Submit", "bounds": {"x1": 45, "y1": 45, "x2": 55, "y2": 55}, "act": ["tap"],
             "extra": {"class": "Text"}, "ocr": True, "ocr_index": 0}]


class _StaleAfterFind:
    """A handle the driver gives out but can no longer resolve, as xa11y does inside an AppKit table."""

    @property
    def rect(self):
        raise StaleElementReferenceException("The element is no longer in the tree")


class _TableWebDriver:
    def native_ui_platform(self):
        return None

    def find_element(self, by, value):
        return _StaleAfterFind()


class TestEvidenceNeverDecidesTheVerdict:
    def test_a_found_element_passes_when_its_bounds_cannot_be_read(self):
        manager = phone_screen(None, sources=[AppiumFindElement(_TableWebDriver()), source_of(Phone([]))])
        passed, _, frame = manager.assert_presence(["//button[@name='% CPU']"], "XPath", timeout=1, rule="any")
        assert passed is True
        assert frame is not None


def _dead():
    return InvalidSessionIdException("A session is either terminated or not started")


def _raises(error):
    def method(*_args, **_kwargs):
        raise error
    return method


def _locate(manager):
    return list(manager.locate("//button[@id='search']"))


@pytest.mark.parametrize(("method", "error", "call", "code"), [
    pytest.param("locate", _dead(), _locate, Code.E0106, id="locate"),
    pytest.param("locate", OpticsError(Code.E0201, message="Element of type XPath not found", cause=_dead()), _locate,
                 Code.E0106, id="locate wrapped"),
    pytest.param("check_elements", _dead(), lambda m: m.assert_presence(["text=Search"], "Text", timeout=10, rule="any"),
                 Code.E0106, id="assert"),
    pytest.param("get_page_source", _dead(), lambda m: m.capture_pagesource(), Code.E0106, id="page source"),
    pytest.param("capture", _dead(), lambda m: m.capture_screenshot(), Code.E0106, id="screenshot"),
    pytest.param("capture_screenshot_bytes", _dead(), lambda m: m.capture_screenshot_bytes(), Code.E0106,
                 id="screenshot bytes"),
    pytest.param("get_interactive_elements", _dead(), lambda m: m.get_interactive_elements(), Code.E0106,
                 id="screen elements"),
    pytest.param("locate", WebDriverException("stale element reference"), _locate, Code.E0201, id="a locate that fails"),
    pytest.param("capture_screenshot_bytes", RuntimeError("hub timeout"), lambda m: m.capture_screenshot_bytes(),
                 Code.E0303, id="a screenshot that fails"),
    pytest.param("get_interactive_elements", RuntimeError("tree read timed out"), lambda m: m.get_interactive_elements(),
                 Code.E0202, id="screen elements that fail"),
])
def test_a_source_that_fails(fake_time, method, error, call, code):
    source = source_of(Phone([]))
    setattr(source, method, _raises(error))
    screen = phone_screen(None, sources=[source])
    with pytest.raises(OpticsError) as caught:
        call(screen)
    assert caught.value.code == code
