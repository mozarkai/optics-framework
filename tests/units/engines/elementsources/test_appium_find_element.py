"""Unit tests for AppiumFindElement.assert_elements_visible (visibility vs. presence)."""
from unittest.mock import MagicMock
import pytest
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, WebDriverException

from optics_framework.common.error import OpticsError, Code
from optics_framework.engines.drivers.appium import largest_part
from optics_framework.engines.elementsources.appium_find_element import AppiumFindElement


@pytest.fixture
def source():
    src = AppiumFindElement(driver=MagicMock())
    return src


class TestLargestPart:
    ACTIVE = {"x": 0, "y": 0, "width": 600, "height": 400}
    OTHER = {"x": 700, "y": 0, "width": 500, "height": 400}

    def test_an_element_in_another_window_is_clipped_to_that_window(self):
        part = largest_part({"x": 720, "y": 300, "width": 200, "height": 300}, [self.ACTIVE, self.OTHER])

        assert part == {"x": 720, "y": 300, "width": 200, "height": 100}

    def test_an_element_across_two_windows_takes_the_larger_part(self):
        part = largest_part({"x": 500, "y": 10, "width": 400, "height": 50}, [self.ACTIVE, self.OTHER])

        assert part == {"x": 700, "y": 10, "width": 200, "height": 50}

    def test_an_element_between_the_windows_has_no_part(self):
        assert largest_part({"x": 620, "y": 10, "width": 60, "height": 50}, [self.ACTIVE, self.OTHER]) is None


class TestIsWithinScreenBounds:
    """_is_within_screen_bounds must intersect the element's bbox against the app area."""

    def test_element_within_window_bounds(self, source):
        located = MagicMock()
        source.get_bbox_for_element = MagicMock(return_value=((10, 10), (50, 50)))
        source.driver.app_areas.return_value = [{"x": 0, "y": 0, "width": 1080, "height": 2400}]

        assert source._is_within_screen_bounds(located) is True

    def test_element_entirely_below_window_bounds(self, source):
        """The exact bug found live on both iOS (Wallet cell, y=1760 on an 844pt screen)
        and Android (Option 47, never rendered): an element far past the window height
        must not count as within bounds."""
        located = MagicMock()
        source.get_bbox_for_element = MagicMock(return_value=((16, 1760), (374, 1813)))
        source.driver.app_areas.return_value = [{"x": 0, "y": 0, "width": 390, "height": 844}]

        assert source._is_within_screen_bounds(located) is False

    def test_element_entirely_right_of_window_bounds(self, source):
        located = MagicMock()
        source.get_bbox_for_element = MagicMock(return_value=((2000, 10), (2100, 50)))
        source.driver.app_areas.return_value = [{"x": 0, "y": 0, "width": 1080, "height": 2400}]

        assert source._is_within_screen_bounds(located) is False

    def test_element_on_the_screen_but_outside_a_desktop_window(self, source):
        located = MagicMock()
        source.driver.app_areas.return_value = [{"x": 400, "y": 200, "width": 600, "height": 400}]
        source.get_bbox_for_element = MagicMock(return_value=((420, 640), (900, 700)))

        assert source._is_within_screen_bounds(located) is False

    def test_element_in_an_inactive_window_of_the_app(self, source):
        located = MagicMock()
        source.driver.app_areas.return_value = [{"x": 0, "y": 0, "width": 600, "height": 400}, {"x": 700, "y": 0, "width": 500, "height": 400}]
        source.get_bbox_for_element = MagicMock(return_value=((720, 40), (900, 90)))

        assert source._is_within_screen_bounds(located) is True

    def test_undeterminable_bbox_is_not_within_bounds(self, source):
        located = MagicMock()
        source.get_bbox_for_element = MagicMock(return_value=None)

        assert source._is_within_screen_bounds(located) is False

    def test_app_area_error_is_not_within_bounds(self, source):
        located = MagicMock()
        source.get_bbox_for_element = MagicMock(return_value=((10, 10), (50, 50)))
        source.driver.app_areas.side_effect = WebDriverException("dead session")

        assert source._is_within_screen_bounds(located) is False


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

    @pytest.mark.parametrize(
        "locate, on_screen",
        [
            pytest.param(lambda: MagicMock(return_value=_element()), False, id="displayed-but-off-screen"),
            pytest.param(lambda: MagicMock(return_value=_element(displayed=False)), True, id="not-displayed"),
            pytest.param(lambda: MagicMock(return_value=None), True, id="not-located"),
            pytest.param(
                lambda: MagicMock(side_effect=OpticsError(Code.E0201, message="not found")), True, id="not-found"
            ),
            pytest.param(lambda: MagicMock(return_value=_element(stale=True)), True, id="stale"),
        ],
    )
    def test_element_that_is_not_visible_does_not_satisfy_rule(self, source, locate, on_screen):
        source.locate = locate()
        source._is_within_screen_bounds = MagicMock(return_value=on_screen)

        with pytest.raises(TimeoutError):
            source.assert_elements_visible(["Option 47"], timeout=0.05, rule="any")

    def test_unexpected_error_propagates(self, source):
        source.locate = MagicMock(side_effect=RuntimeError("boom"))

        with pytest.raises(OpticsError):
            source.assert_elements_visible(["Whatever"], timeout=0.05, rule="any")

    def test_all_rule_requires_every_element_visible(self, source):
        visible = _element()
        hidden = _element(displayed=False)

        def fake_locate(element, index=None):
            return visible if element == "Visible" else hidden

        source.locate = MagicMock(side_effect=fake_locate)
        source._is_within_screen_bounds = MagicMock(return_value=True)

        with pytest.raises(TimeoutError):
            source.assert_elements_visible(["Visible", "Hidden"], timeout=0.05, rule="all")

    def test_invalid_rule_raises_immediately(self, source):
        with pytest.raises(OpticsError):
            source.assert_elements_visible(["Anything"], timeout=1, rule="bogus")


class TestLocateIndex:
    """locate() must honor `index` when several elements share a locator."""

    def test_text_index_selects_nth_accessibility_id_match(self, source):
        first, second = MagicMock(name="first"), MagicMock(name="second")
        source.driver.driver.find_elements.return_value = [first, second]

        assert source.locate("NEXT", index=1) is second

    def test_text_index_none_selects_first_match(self, source):
        first = MagicMock(name="first")
        source.driver.driver.find_element.return_value = first

        assert source.locate("NEXT") is first

    def test_text_index_out_of_range_raises_e0201(self, source):
        source.driver.driver.find_elements.return_value = [MagicMock(), MagicMock()]

        with pytest.raises(OpticsError) as exc:
            source.locate("NEXT", index=2)
        assert exc.value.code == Code.E0201

    def test_text_no_matches_raises_e0201(self, source):
        source.driver.driver.find_element.side_effect = NoSuchElementException("no match")

        with pytest.raises(OpticsError) as exc:
            source.locate("NEXT")
        assert exc.value.code == Code.E0201

    def test_class_index_selects_nth_class_name_match(self, source):
        first, second = MagicMock(name="first"), MagicMock(name="second")
        source.driver.driver.find_elements.return_value = [first, second]

        assert source.locate("android.widget.Button", index=1) is second

    def test_class_index_out_of_range_raises_e0201(self, source):
        source.driver.driver.find_elements.return_value = [MagicMock(), MagicMock()]

        with pytest.raises(OpticsError) as exc:
            source.locate("android.widget.Button", index=5)
        assert exc.value.code == Code.E0201
