import itertools
import time
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


async def test_a_second_launch_navigates_the_open_browser(monkeypatch):
    pw, browser, page = _fake_playwright(monkeypatch)
    page.is_closed = MagicMock(return_value=False)
    driver = Playwright(CONFLICTING)

    await driver._launch_app_async("https://example.com", event_name=None)
    await driver._launch_app_async("https://example.com/cart", event_name=None)

    pw.firefox.launch.assert_awaited_once()
    assert page.goto.await_count == 2


def test_special_keys_are_pressed_not_typed():
    driver = _driver()
    driver.page.keyboard.press = AsyncMock()
    driver.page.keyboard.type = AsyncMock()

    driver.enter_text_using_keyboard(pw_driver.SpecialKey.ENTER)

    driver.page.keyboard.press.assert_awaited_once_with("Enter")
    driver.page.keyboard.type.assert_not_awaited()


def test_secret_prefix_is_not_typed():
    driver = _driver()
    located = MagicMock()
    located.fill = AsyncMock()

    driver.enter_text_element(located, "@:secret")

    located.fill.assert_awaited_once_with("secret")


def test_go_back_uses_browser_history():
    driver = _driver()
    driver.page.go_back = AsyncMock()

    driver.go_back()

    driver.page.go_back.assert_awaited_once()


def _settling_driver(monkeypatch, fingerprints, config=None):
    monkeypatch.setattr(Playwright, "SETTLE_QUIET_S", 0.03)
    monkeypatch.setattr(Playwright, "SETTLE_POLL_S", 0.01)
    driver = _driver(config)
    driver.page.evaluate = AsyncMock(side_effect=fingerprints)
    return driver


def test_settle_returns_once_the_dom_stops_changing(monkeypatch):
    driver = _settling_driver(monkeypatch, itertools.chain(["shell", "form"], itertools.repeat("form")))

    started = time.monotonic()
    driver.wait_for_page_settled()

    assert time.monotonic() - started < 1
    assert driver.page.evaluate.await_count >= 4


@pytest.mark.parametrize(
    "unsettled",
    [pw_driver.PlaywrightError("Execution context was destroyed"), None],
    ids=["mid-navigation", "still-parsing"],
)
def test_settle_keeps_waiting_while_the_page_is_unreadable(monkeypatch, unsettled):
    driver = _settling_driver(monkeypatch, itertools.chain([unsettled] * 5, itertools.repeat("form")))

    driver.wait_for_page_settled()

    # Five unreadable polls, then "form" seen at least twice across the quiet window.
    assert driver.page.evaluate.await_count >= 7


def test_settle_gives_up_without_error_on_a_page_that_never_goes_quiet(monkeypatch):
    ticking = (str(n) for n in itertools.count())
    driver = _settling_driver(monkeypatch, ticking, config={"settle_timeout_ms": 100})

    started = time.monotonic()
    driver.wait_for_page_settled()

    assert 0.1 <= time.monotonic() - started < 1


def test_settle_timeout_of_zero_skips_the_wait(monkeypatch):
    driver = _settling_driver(monkeypatch, itertools.repeat("form"), config={"settle_timeout_ms": 0})

    driver.wait_for_page_settled()

    driver.page.evaluate.assert_not_awaited()
