import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("playwright")

from optics_framework.common.error import Code, OpticsError  # noqa: E402
from optics_framework.engines.drivers import playwright as pw_driver  # noqa: E402
from optics_framework.engines.drivers.playwright import Playwright  # noqa: E402

pytestmark = pytest.mark.white_box


def _driver(config=None):
    driver = Playwright(config or {})
    driver.page = MagicMock()
    return driver


def test_enter_text_still_accepts_a_selector_string():
    driver = _driver()
    locator = MagicMock()
    locator.evaluate = AsyncMock(return_value=True)
    locator.fill = AsyncMock()
    driver.page.locator.return_value = locator

    driver.enter_text_element("//input[@id='user-name']", "standard_user")

    driver.page.locator.assert_called_once_with("xpath=//input[@id='user-name']")
    locator.fill.assert_awaited_once_with("standard_user")


def test_clear_text_empties_a_located_field():
    driver = _driver()
    located = MagicMock()
    located.evaluate = AsyncMock(return_value=True)
    located.fill = AsyncMock()

    driver.clear_text_element(located)

    located.fill.assert_awaited_once_with("")


def test_clear_text_selects_all_with_the_platform_modifier():
    driver = _driver()
    driver.page.keyboard.press = AsyncMock()

    driver.clear_text()

    assert [c.args[0] for c in driver.page.keyboard.press.await_args_list] == ["ControlOrMeta+A", "Backspace"]


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


async def test_a_relative_storage_state_is_read_from_the_project_without_extension_cookies(monkeypatch, tmp_path):
    _, browser, _ = _fake_playwright(monkeypatch)
    page_cookie = {"name": "sid", "domain": ".example.com", "partitionKey": "https://example.com"}
    extension_cookie = {"name": "cf", "domain": ".example.com", "partitionKey": "chrome-extension://abc"}
    (tmp_path / "auth.json").write_text(json.dumps({"cookies": [page_cookie, extension_cookie], "origins": []}))
    event_sdk = SimpleNamespace(config_handler=SimpleNamespace(config=SimpleNamespace(project_path=str(tmp_path))))
    config = {"capabilities": {"browser": "firefox", "storage_state": "auth.json"}}

    await Playwright(config, event_sdk)._launch_app_async("https://example.com", event_name=None)

    browser.new_context.assert_awaited_once_with(
        viewport={"width": 1280, "height": 800}, storage_state={"cookies": [page_cookie], "origins": []}
    )


async def test_a_missing_storage_state_file_fails_the_launch(monkeypatch, tmp_path):
    _fake_playwright(monkeypatch)
    config = {"capabilities": {"browser": "firefox", "storage_state": str(tmp_path / "missing.json")}}
    driver = Playwright(config)

    with pytest.raises(OpticsError) as err:
        await driver._start_browser()
    assert err.value.code == Code.E0501


def test_storage_state_is_refused_with_the_browsers_own_context():
    config = {"url": "http://127.0.0.1:9222", "capabilities": {"context": "existing", "storage_state": "auth.json"}}
    driver = Playwright(config)

    with pytest.raises(OpticsError) as err:
        driver._remote()
    assert err.value.code == Code.E0501


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
    located.evaluate = AsyncMock(return_value=True)
    located.fill = AsyncMock()

    driver.enter_text_element(located, "@:secret")

    located.fill.assert_awaited_once_with("secret")
