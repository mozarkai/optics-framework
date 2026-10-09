from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("playwright")

from optics_framework.engines.drivers import playwright as pw_driver  # noqa: E402
from optics_framework.engines.drivers.playwright import Playwright  # noqa: E402

pytestmark = pytest.mark.white_box


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


def test_get_text_reads_a_located_element():
    driver = _driver()
    located = MagicMock()
    located.inner_text = AsyncMock(return_value="1")

    assert driver.get_text_element(located) == "1"
    driver.page.locator.assert_not_called()


def test_settings_are_read_from_capabilities():
    driver = Playwright({"enabled": True, "capabilities": {"browser": "firefox", "headless": True}})

    assert driver._setting("browser", "chromium") == "firefox"
    assert driver._setting("headless", False) is True
    assert driver._setting("viewport", {"width": 1280, "height": 800}) == {"width": 1280, "height": 800}


def test_top_level_settings_still_work():
    driver = Playwright({"enabled": True, "browser": "webkit", "capabilities": {}})

    assert driver._setting("browser", "chromium") == "webkit"


CONFLICTING = {
    "enabled": True,
    "browser": "webkit",
    "headless": False,
    "viewport": {"width": 1280, "height": 800},
    "navigation_timeout_ms": 1000,
    "navigation_wait_until": "load",
    "capabilities": {
        "browser": "firefox",
        "headless": True,
        "viewport": {"width": 390, "height": 844},
        "navigation_timeout_ms": 5000,
        "navigation_wait_until": "networkidle",
    },
}


def _fake_playwright(monkeypatch):
    page = MagicMock()
    page.goto = AsyncMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    pw = MagicMock()
    pw.firefox.launch = AsyncMock(return_value=browser)
    pw.webkit.launch = AsyncMock(return_value=browser)
    starter = MagicMock()
    starter.start = AsyncMock(return_value=pw)
    monkeypatch.setattr(pw_driver, "async_playwright", lambda: starter)
    return pw, browser, page


async def test_launch_uses_capabilities_over_top_level_settings(monkeypatch):
    pw, browser, page = _fake_playwright(monkeypatch)

    await Playwright(CONFLICTING)._launch_app_async("https://example.com", event_name=None)

    pw.firefox.launch.assert_awaited_once_with(headless=True)
    pw.webkit.launch.assert_not_awaited()
    browser.new_context.assert_awaited_once_with(viewport={"width": 390, "height": 844})
    page.goto.assert_awaited_once_with("https://example.com", timeout=5000, wait_until="networkidle")


async def test_launch_falls_back_to_top_level_settings(monkeypatch):
    pw, browser, page = _fake_playwright(monkeypatch)
    config = {key: value for key, value in CONFLICTING.items() if key != "capabilities"}

    await Playwright(config)._launch_app_async("https://example.com", event_name=None)

    pw.webkit.launch.assert_awaited_once_with(headless=False)
    browser.new_context.assert_awaited_once_with(viewport={"width": 1280, "height": 800})
    page.goto.assert_awaited_once_with("https://example.com", timeout=1000, wait_until="load")
