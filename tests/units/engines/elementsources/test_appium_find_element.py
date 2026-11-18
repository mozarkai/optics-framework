from unittest.mock import MagicMock
import pytest
from selenium.common.exceptions import StaleElementReferenceException

from optics_framework.common.error import OpticsError, Code
from optics_framework.engines.elementsources.appium_find_element import AppiumFindElement


@pytest.fixture
def source():
    src = AppiumFindElement(driver=MagicMock())
    return src


class TestIsWithinScreenBounds:
    def test_element_in_an_inactive_window_of_the_app(self, source):
        located = MagicMock()
        source.driver.app_areas.return_value = [{"x": 0, "y": 0, "width": 600, "height": 400}, {"x": 700, "y": 0, "width": 500, "height": 400}]
        source.get_bbox_for_element = MagicMock(return_value=((720, 40), (900, 90)))

        assert source._is_within_screen_bounds(located) is True


def _element(displayed=True, stale=False):
    element = MagicMock()
    if stale:
        element.is_displayed.side_effect = StaleElementReferenceException("stale")
    else:
        element.is_displayed.return_value = displayed
    return element


class TestAssertElementsVisible:
    def test_visible_element_satisfies_any_rule(self, source):
        source.locate = MagicMock(return_value=_element())
        source._is_within_screen_bounds = MagicMock(return_value=True)

        result, timestamp = source.assert_elements_visible(["Option 1"], timeout=1, rule="any")

        assert result is True
        assert timestamp is not None

    def test_unexpected_error_propagates(self, source):
        source.locate = MagicMock(side_effect=RuntimeError("boom"))

        with pytest.raises(OpticsError):
            source.assert_elements_visible(["Whatever"], timeout=0.05, rule="any")


class TestLocateIndex:
    def test_text_index_selects_nth_accessibility_id_match(self, source):
        first, second = MagicMock(name="first"), MagicMock(name="second")
        source.driver.driver.find_elements.return_value = [first, second]

        assert source.locate("NEXT", index=1) is second

    def test_class_index_out_of_range_raises_e0201(self, source):
        source.driver.driver.find_elements.return_value = [MagicMock(), MagicMock()]

        with pytest.raises(OpticsError) as exc:
            source.locate("android.widget.Button", index=5)
        assert exc.value.code == Code.E0201
