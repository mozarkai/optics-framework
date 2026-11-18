from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("playwright")

from optics_framework.common.error import Code, OpticsError  # noqa: E402
from optics_framework.engines.drivers import playwright as pw_driver  # noqa: E402
from optics_framework.engines.drivers.playwright import Playwright  # noqa: E402


def _driver(config=None):
    driver = Playwright(config or {})
    driver.page = MagicMock()
    return driver


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
