"""Unit tests for PlaywrightFindElement.assert_elements (true presence, not visibility).

`locate()` requires `state="visible"` (correct for actually interacting with an element),
so `assert_elements`/`_is_present` must NOT go through `locate()` -- otherwise "presence"
silently becomes "visibility", reporting elements that exist but aren't yet visible/scrolled
into view as "not found".
"""
from unittest.mock import MagicMock, AsyncMock
import pytest

from optics_framework.engines.elementsources.playwright_find_element import PlaywrightFindElement


@pytest.fixture
def source():
    driver = MagicMock()
    driver.page = MagicMock()
    return PlaywrightFindElement(driver=driver)


class TestIsPresent:
    """_is_present must report True for DOM-attached elements regardless of visibility."""

    def test_present_and_visible_element_is_present(self, source):
        locator = MagicMock()
        locator.count = AsyncMock(return_value=1)
        source._build_locator = MagicMock(return_value=locator)

        assert source._is_present("Visible Text") is True

    def test_present_but_not_visible_element_is_still_present(self, source):
        """The exact bug this fixes: an element attached to the DOM but not currently
        visible (e.g. off-screen, not yet scrolled to) must still count as present."""
        locator = MagicMock()
        locator.count = AsyncMock(return_value=1)  # attached to DOM
        source._build_locator = MagicMock(return_value=locator)

        # Note: locator.wait_for is never even called here -- _is_present must not
        # require visibility the way locate() does.
        assert source._is_present("Off-screen Text") is True
        locator.wait_for.assert_not_called()

    def test_absent_element_is_not_present(self, source):
        locator = MagicMock()
        locator.count = AsyncMock(return_value=0)
        source._build_locator = MagicMock(return_value=locator)

        assert source._is_present("Missing") is False

    def test_image_type_is_never_present(self, source):
        """_build_locator returns None for Image type; Playwright doesn't support image location."""
        source._build_locator = MagicMock(return_value=None)

        assert source._is_present("template.png") is False

    def test_locator_error_is_treated_as_not_present(self, source):
        source._build_locator = MagicMock(side_effect=RuntimeError("boom"))

        assert source._is_present("Whatever") is False


class TestAssertElementsPresence:
    """assert_elements (via _is_present) must not require visibility."""

    def test_present_but_invisible_element_satisfies_any_rule(self, source):
        source._is_present = MagicMock(return_value=True)

        result, timestamp = source.assert_elements(["Off-screen Option"], timeout=1, rule="any")

        assert result is True
        assert timestamp is not None

    def test_absent_element_times_out(self, source):
        source._is_present = MagicMock(return_value=False)

        with pytest.raises(TimeoutError):
            source.assert_elements(["Missing"], timeout=0.05, rule="any")

    def test_invalid_rule_raises_immediately(self, source):
        from optics_framework.common.error import OpticsError

        with pytest.raises(OpticsError):
            source.assert_elements(["Anything"], timeout=1, rule="bogus")


class TestLocateUnaffected:
    """locate() must still require visibility -- only the presence check changed."""

    def test_locate_still_waits_for_visible_state(self, source):
        locator = MagicMock()
        locator.count = AsyncMock(return_value=1)
        locator.wait_for = AsyncMock(return_value=None)
        locator.first = locator
        source._build_locator = MagicMock(return_value=locator)

        result = source.locate("Some Text")

        assert result is locator
        locator.wait_for.assert_called_once_with(state="visible", timeout=3000)
