"""Unit tests for SeleniumFindElement's visibility-checking building block.

assert_elements_visible itself is disabled (raises NotImplementedError) pending a fix for
a separate pre-existing bug (SeleniumFindElement.locate()/execute_script() raise AttributeError
because the injected driver is the SeleniumDriver wrapper, not the raw webdriver.Remote it
wraps). _is_located_and_visible has the real bounds-check logic ready to wire back in once
that's fixed and this has been verified live -- tested directly here in the meantime.

Selenium's own is_displayed() only checks CSS (display/visibility/opacity) -- an element
scrolled out of the current viewport still reports True, so the real check must also verify
a viewport-intersection (via execute_script) before counting it as visible.
"""
from unittest.mock import MagicMock
import pytest
from selenium.common.exceptions import StaleElementReferenceException

from optics_framework.engines.elementsources.selenium_find_element import SeleniumFindElement


@pytest.fixture
def source():
    return SeleniumFindElement(driver=MagicMock())


class TestAssertElementsVisibleNotYetImplemented:
    def test_raises_not_implemented_regardless_of_state(self, source):
        with pytest.raises(NotImplementedError):
            source.assert_elements_visible(["Anything"], timeout=1, rule="any")

    def test_raises_not_implemented_even_with_no_driver(self):
        source = SeleniumFindElement(driver=None)
        with pytest.raises(NotImplementedError):
            source.assert_elements_visible(["Anything"], timeout=1, rule="any")


class TestIsLocatedAndVisible:
    """Direct tests of the retained-for-later bounds-check logic."""

    def test_displayed_and_in_viewport_is_visible(self, source):
        element = MagicMock()
        element.is_displayed.return_value = True
        source.locate = MagicMock(return_value=element)
        source.driver.execute_script = MagicMock(return_value=True)

        assert source._is_located_and_visible("Option 1") is True

    def test_displayed_but_scrolled_out_of_viewport_is_not_visible(self, source):
        """CSS-visible (display:block) but outside the current scroll viewport must not count."""
        element = MagicMock()
        element.is_displayed.return_value = True
        source.locate = MagicMock(return_value=element)
        source.driver.execute_script = MagicMock(return_value=False)

        assert source._is_located_and_visible("Option 47") is False

    def test_not_displayed_is_not_visible(self, source):
        element = MagicMock()
        element.is_displayed.return_value = False
        source.locate = MagicMock(return_value=element)

        assert source._is_located_and_visible("Hidden") is False

    def test_locate_returning_none_is_not_visible(self, source):
        source.locate = MagicMock(return_value=None)

        assert source._is_located_and_visible("Missing") is False

    def test_stale_element_on_is_displayed_is_not_visible(self, source):
        """An element that goes stale between locate() and is_displayed() (e.g. list
        re-rendered mid-scroll) must count as not visible, not blow up the check."""
        stale_element = MagicMock()
        stale_element.is_displayed.side_effect = StaleElementReferenceException("stale")
        source.locate = MagicMock(return_value=stale_element)

        assert source._is_located_and_visible("Whatever") is False

    def test_stale_element_on_execute_script_is_not_visible(self, source):
        """Same as above, but the element goes stale during the viewport-rect check."""
        element = MagicMock()
        element.is_displayed.return_value = True
        source.locate = MagicMock(return_value=element)
        source.driver.execute_script = MagicMock(side_effect=StaleElementReferenceException("stale"))

        assert source._is_located_and_visible("Whatever") is False
