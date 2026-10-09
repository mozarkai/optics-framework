from unittest.mock import AsyncMock, MagicMock

from optics_framework.engines.drivers.playwright import Playwright


def _driver(config=None):
    driver = Playwright(config or {})
    driver.page = MagicMock()
    return driver


def test_enter_text_fills_a_locator_returned_by_an_element_source():
    driver = _driver()
    located = MagicMock()
    located.fill = AsyncMock()

    driver.enter_text_element(located, "standard_user")

    located.fill.assert_awaited_once_with("standard_user")
    driver.page.locator.assert_not_called()


def test_enter_text_still_accepts_a_selector_string():
    driver = _driver()
    locator = MagicMock()
    locator.fill = AsyncMock()
    driver.page.locator.return_value = locator

    driver.enter_text_element("//input[@id='user-name']", "standard_user")

    driver.page.locator.assert_called_once_with("xpath=//input[@id='user-name']")
    locator.fill.assert_awaited_once_with("standard_user")


def test_clear_text_empties_a_located_field():
    driver = _driver()
    located = MagicMock()
    located.fill = AsyncMock()

    driver.clear_text_element(located)

    located.fill.assert_awaited_once_with("")


def test_settings_are_read_from_capabilities():
    driver = Playwright({"enabled": True, "capabilities": {"browser": "firefox", "headless": True}})

    assert driver._setting("browser", "chromium") == "firefox"
    assert driver._setting("headless", False) is True
    assert driver._setting("viewport", {"width": 1280, "height": 800}) == {"width": 1280, "height": 800}


def test_top_level_settings_still_work():
    driver = Playwright({"enabled": True, "browser": "webkit", "capabilities": {}})

    assert driver._setting("browser", "chromium") == "webkit"
