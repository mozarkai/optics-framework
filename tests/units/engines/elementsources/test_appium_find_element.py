"""Unit tests for AppiumFindElement.assert_elements_visible (visibility vs. presence)."""
from unittest.mock import MagicMock
import pytest
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException

from optics_framework.common.error import OpticsError, Code
from optics_framework.engines.elementsources.appium_find_element import AppiumFindElement


@pytest.fixture
def source():
    src = AppiumFindElement(driver=MagicMock())
    return src


class TestIsWithinScreenBounds:
    """_is_within_screen_bounds must intersect the element's bbox against the window size."""

    def test_element_within_window_bounds(self, source):
        located = MagicMock()
        source.get_bbox_for_element = MagicMock(return_value=((10, 10), (50, 50)))
        source.driver.driver.get_window_size.return_value = {"width": 1080, "height": 2400}

        assert source._is_within_screen_bounds(located) is True

    def test_element_entirely_below_window_bounds(self, source):
        """The exact bug found live on both iOS (Wallet cell, y=1760 on an 844pt screen)
        and Android (Option 47, never rendered): an element far past the window height
        must not count as within bounds."""
        located = MagicMock()
        source.get_bbox_for_element = MagicMock(return_value=((16, 1760), (374, 1813)))
        source.driver.driver.get_window_size.return_value = {"width": 390, "height": 844}

        assert source._is_within_screen_bounds(located) is False

    def test_element_entirely_right_of_window_bounds(self, source):
        located = MagicMock()
        source.get_bbox_for_element = MagicMock(return_value=((2000, 10), (2100, 50)))
        source.driver.driver.get_window_size.return_value = {"width": 1080, "height": 2400}

        assert source._is_within_screen_bounds(located) is False

    def test_undeterminable_bbox_is_not_within_bounds(self, source):
        located = MagicMock()
        source.get_bbox_for_element = MagicMock(return_value=None)

        assert source._is_within_screen_bounds(located) is False

    def test_get_window_size_error_is_not_within_bounds(self, source):
        located = MagicMock()
        source.get_bbox_for_element = MagicMock(return_value=((10, 10), (50, 50)))
        source.driver.driver.get_window_size.side_effect = WebDriverException("dead session")

        assert source._is_within_screen_bounds(located) is False


class TestAssertElementsVisible:
    """assert_elements_visible must require is_displayed() AND on-screen bounds, not just
    locate() success."""

    def test_visible_element_satisfies_any_rule(self, source):
        visible_element = MagicMock()
        visible_element.is_displayed.return_value = True
        source.locate = MagicMock(return_value=visible_element)
        source._is_within_screen_bounds = MagicMock(return_value=True)

        result, timestamp = source.assert_elements_visible(["Option 1"], timeout=1, rule="any")

        assert result is True
        assert timestamp is not None

    def test_displayed_but_off_screen_does_not_satisfy_rule(self, source):
        """The exact bug found live: is_displayed() alone reports True for elements well
        outside the current screen bounds (confirmed on both iOS and Android) -- pairing
        it with _is_within_screen_bounds is what actually catches this."""
        offscreen_but_displayed = MagicMock()
        offscreen_but_displayed.is_displayed.return_value = True
        source.locate = MagicMock(return_value=offscreen_but_displayed)
        source._is_within_screen_bounds = MagicMock(return_value=False)

        with pytest.raises(TimeoutError):
            source.assert_elements_visible(["Option 47"], timeout=0.05, rule="any")

    def test_located_but_not_displayed_does_not_satisfy_rule(self, source):
        """The exact bug this method fixes: an element present in the tree but off-screen
        (is_displayed() == False) must not count as found."""
        offscreen_element = MagicMock()
        offscreen_element.is_displayed.return_value = False
        source.locate = MagicMock(return_value=offscreen_element)

        with pytest.raises(TimeoutError):
            source.assert_elements_visible(["Option 47"], timeout=0.05, rule="any")

    def test_locate_returning_none_does_not_satisfy_rule(self, source):
        source.locate = MagicMock(return_value=None)

        with pytest.raises(TimeoutError):
            source.assert_elements_visible(["Missing"], timeout=0.05, rule="any")

    def test_locate_not_found_is_treated_as_not_visible(self, source):
        """_is_located_and_displayed must swallow locate()'s not-found OpticsError, not propagate it."""
        source.locate = MagicMock(side_effect=OpticsError(Code.E0201, message="not found"))

        with pytest.raises(TimeoutError):
            source.assert_elements_visible(["Whatever"], timeout=0.05, rule="any")

    def test_stale_element_on_is_displayed_is_treated_as_not_visible(self, source):
        """An element that goes stale between locate() and is_displayed() (e.g. list
        re-rendered mid-scroll) must count as not visible, not blow up the assertion."""
        stale_element = MagicMock()
        stale_element.is_displayed.side_effect = StaleElementReferenceException("stale")
        source.locate = MagicMock(return_value=stale_element)

        with pytest.raises(TimeoutError):
            source.assert_elements_visible(["Whatever"], timeout=0.05, rule="any")

    def test_unexpected_error_propagates(self, source):
        """A genuine bug (e.g. a bad mock/typo) must not be silently swallowed as 'not visible'."""
        source.locate = MagicMock(side_effect=RuntimeError("boom"))

        with pytest.raises(OpticsError):
            source.assert_elements_visible(["Whatever"], timeout=0.05, rule="any")

    def test_all_rule_requires_every_element_visible(self, source):
        visible = MagicMock()
        visible.is_displayed.return_value = True
        hidden = MagicMock()
        hidden.is_displayed.return_value = False

        def fake_locate(element, index=None):
            return visible if element == "Visible" else hidden

        source.locate = MagicMock(side_effect=fake_locate)
        source._is_within_screen_bounds = MagicMock(return_value=True)

        with pytest.raises(TimeoutError):
            source.assert_elements_visible(["Visible", "Hidden"], timeout=0.05, rule="all")

    def test_invalid_rule_raises_immediately(self, source):
        with pytest.raises(OpticsError):
            source.assert_elements_visible(["Anything"], timeout=1, rule="bogus")
