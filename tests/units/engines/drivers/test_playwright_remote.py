from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("playwright")

from optics_framework.common.error import Code, OpticsError  # noqa: E402
from optics_framework.engines.drivers import playwright as pw_driver  # noqa: E402
from optics_framework.engines.drivers.playwright import Playwright  # noqa: E402

pytestmark = pytest.mark.white_box

HUB = "wss://hub.example.com/playwright?config=%7B%22browser%22%3A%22chrome%22%7D"


def _fake_playwright(monkeypatch):
    page = MagicMock()
    page.goto = AsyncMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    pw = MagicMock()
    pw.chromium.connect_over_cdp = AsyncMock(return_value=browser)
    pw.chromium.launch = AsyncMock(return_value=browser)
    pw.stop = AsyncMock()
    starter = MagicMock()
    starter.start = AsyncMock(return_value=pw)
    monkeypatch.setattr(pw_driver, "async_playwright", lambda: starter)
    return pw, browser


async def test_a_url_connects_to_the_remote_browser_instead_of_launching(monkeypatch):
    monkeypatch.setenv("HUB_USER", "ava@example.com")
    monkeypatch.setenv("HUB_KEY", "k-123")
    pw, browser = _fake_playwright(monkeypatch)
    config = {
        "url": HUB,
        "capabilities": {"headers": {"X-Username": "${HUB_USER}", "X-Api-Key": "${HUB_KEY}"}},
    }

    await Playwright(config)._launch_app_async(None, event_name=None)

    pw.chromium.connect_over_cdp.assert_awaited_once_with(
        HUB, headers={"X-Username": "ava@example.com", "X-Api-Key": "k-123"}, timeout=90000
    )
    pw.chromium.launch.assert_not_awaited()
    browser.new_context.assert_awaited_once_with(viewport={"width": 1280, "height": 800})


async def test_the_endpoint_itself_can_reference_the_environment(monkeypatch):
    monkeypatch.setenv("BROWSER_TOKEN", "t-9")
    pw, _ = _fake_playwright(monkeypatch)
    config = {"url": "wss://cloud.example.com?token=${BROWSER_TOKEN}", "capabilities": {"connect_timeout_ms": 150000}}

    await Playwright(config)._launch_app_async(None, event_name=None)

    pw.chromium.connect_over_cdp.assert_awaited_once_with(
        "wss://cloud.example.com?token=t-9", headers=None, timeout=150000
    )


async def test_an_unset_secret_fails_the_launch_and_stops_playwright(monkeypatch):
    monkeypatch.delenv("HUB_KEY", raising=False)
    pw, _ = _fake_playwright(monkeypatch)
    driver = Playwright({"url": HUB, "capabilities": {"headers": {"X-Api-Key": "${HUB_KEY}"}}})

    with pytest.raises(OpticsError, match="HUB_KEY is not set"):
        await driver._launch_app_async(None, event_name=None)

    pw.chromium.connect_over_cdp.assert_not_awaited()
    pw.stop.assert_awaited_once()
    assert driver._pw is None


async def test_terminate_closes_the_connection_that_holds_the_remote_session(monkeypatch):
    pw, browser = _fake_playwright(monkeypatch)
    driver = Playwright({"url": HUB})
    driver.event_sdk = MagicMock()
    await driver._launch_app_async(None, event_name=None)

    await driver._terminate_async()

    browser.close.assert_awaited_once()
    pw.stop.assert_awaited_once()
    driver.event_sdk.send_all_events.assert_called_once()
    assert (driver.page, driver._context, driver._browser, driver._pw) == (None, None, None, None)


def _captured_timeouts(monkeypatch):
    timeouts = []

    def fake_run_async(coro, timeout):
        coro.close()
        timeouts.append(timeout)

    monkeypatch.setattr(pw_driver, "run_async", fake_run_async)
    return timeouts


def test_calls_without_their_own_budget_are_bounded_by_the_action_timeout(monkeypatch):
    timeouts = _captured_timeouts(monkeypatch)
    driver = Playwright({"capabilities": {"action_timeout_ms": 40000}})
    driver.page = MagicMock()
    driver.page.mouse.click = AsyncMock()

    driver.press_coordinates(1, 2)
    driver.terminate()

    assert timeouts == [40.0, 40.0]


def test_launch_budget_covers_connect_navigation_and_slack(monkeypatch):
    timeouts = _captured_timeouts(monkeypatch)
    config = {"capabilities": {"connect_timeout_ms": 120000, "navigation_timeout_ms": 30000, "action_timeout_ms": 5000}}

    Playwright(config).launch_app("https://example.com")

    assert timeouts == [155.0]


async def test_swipe_waits_for_the_element_as_long_as_other_element_keywords():
    driver = Playwright({"capabilities": {"element_timeout_ms": 2500}})
    locator = MagicMock()
    locator.wait_for = AsyncMock()
    locator.evaluate = AsyncMock()

    await driver._swipe_element_async(locator, "down", 100)

    locator.wait_for.assert_awaited_once_with(state="visible", timeout=2500)


def test_expand_env_replaces_each_reference(monkeypatch):
    from optics_framework.common.utils import expand_env

    monkeypatch.setenv("A", "1")
    monkeypatch.setenv("B", "two")

    assert expand_env("x=${A}&y=${B}&z=$A") == "x=1&y=two&z=$A"


def test_expand_env_names_the_missing_variable(monkeypatch):
    from optics_framework.common.utils import expand_env

    monkeypatch.delenv("NOPE", raising=False)

    with pytest.raises(OpticsError) as error:
        expand_env("Bearer ${NOPE}")
    assert error.value.code == Code.E0501
    assert "NOPE" in str(error.value)


async def test_a_cancelled_close_still_stops_playwright(monkeypatch):
    import asyncio

    pw, browser = _fake_playwright(monkeypatch)
    driver = Playwright({"url": HUB})
    await driver._launch_app_async(None, event_name=None)
    browser.close.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await driver._cleanup_resources()

    pw.stop.assert_awaited_once()
    assert driver._browser is None
    assert driver._pw is None


async def test_a_connect_error_masks_the_expanded_token(monkeypatch):
    from playwright.async_api import Error as PlaywrightError

    monkeypatch.setenv("BROWSER_TOKEN", "t-9")
    pw, _ = _fake_playwright(monkeypatch)
    pw.chromium.connect_over_cdp.side_effect = PlaywrightError(
        "WebSocket error: wss://cloud.example.com?token=t-9 401 Unauthorized"
    )
    driver = Playwright({"url": "wss://cloud.example.com?token=${BROWSER_TOKEN}"})

    with pytest.raises(OpticsError) as error:
        await driver._launch_app_async(None, event_name=None)

    assert "t-9" not in str(error.value)
    assert "wss://cloud.example.com?*** 401 Unauthorized" in str(error.value)
    pw.stop.assert_awaited_once()


def test_expand_env_rejects_an_empty_secret(monkeypatch):
    from optics_framework.common.utils import expand_env

    monkeypatch.setenv("EMPTY", "")

    with pytest.raises(OpticsError, match="EMPTY is not set"):
        expand_env("Bearer ${EMPTY}")


async def test_no_headers_sends_none_rather_than_an_empty_mapping(monkeypatch):
    pw, _ = _fake_playwright(monkeypatch)

    await Playwright({"url": "http://127.0.0.1:9222"})._launch_app_async(None, event_name=None)

    pw.chromium.connect_over_cdp.assert_awaited_once_with("http://127.0.0.1:9222", headers=None, timeout=90000)


async def test_the_playwright_protocol_uses_connect(monkeypatch):
    pw, _ = _fake_playwright(monkeypatch)
    pw.firefox.connect = AsyncMock(return_value=pw.chromium.connect_over_cdp.return_value)
    config = {"url": "wss://grid.example.com/playwright", "capabilities": {"browser": "firefox", "protocol": "playwright"}}

    await Playwright(config)._launch_app_async(None, event_name=None)

    pw.firefox.connect.assert_awaited_once_with("wss://grid.example.com/playwright", headers=None, timeout=90000)
    pw.chromium.connect_over_cdp.assert_not_awaited()


async def test_an_unknown_protocol_is_a_config_error(monkeypatch):
    _fake_playwright(monkeypatch)
    driver = Playwright({"url": HUB, "capabilities": {"protocol": "webdriver"}})

    with pytest.raises(OpticsError) as error:
        await driver._launch_app_async(None, event_name=None)
    assert error.value.code == Code.E0501


async def test_the_environment_can_point_every_session_at_a_remote_browser(monkeypatch):
    monkeypatch.setenv("GRID_KEY", "k-7")
    monkeypatch.setenv(Playwright.REMOTE_ENV, '{"url": "wss://grid.example.com", "headers": {"X-Api-Key": "${GRID_KEY}"}}')
    pw, _ = _fake_playwright(monkeypatch)

    await Playwright({"url": "wss://ignored.example.com"})._launch_app_async(None, event_name=None)

    pw.chromium.connect_over_cdp.assert_awaited_once_with(
        "wss://grid.example.com", headers={"X-Api-Key": "k-7"}, timeout=90000
    )


def test_a_malformed_remote_environment_is_a_config_error(monkeypatch):
    monkeypatch.setenv(Playwright.REMOTE_ENV, "wss://not-json")
    driver = Playwright({})

    with pytest.raises(OpticsError, match="must be a JSON object"):
        driver._remote()


def test_remote_environment_keys_are_checked(monkeypatch):
    monkeypatch.setenv(Playwright.REMOTE_ENV, '{"url": "wss://grid.example.com", "connect_timeout_ms": 1}')
    driver = Playwright({})

    with pytest.raises(OpticsError, match="must be a JSON object"):
        driver._remote()


async def test_a_connect_error_masks_header_values(monkeypatch):
    from playwright.async_api import Error as PlaywrightError

    monkeypatch.setenv("GRID_KEY", "k-7")
    pw, _ = _fake_playwright(monkeypatch)
    pw.chromium.connect_over_cdp.side_effect = PlaywrightError(
        "retrieving websocket url from http://grid.example.com/?key=k-7 with Bearer literal-1"
    )
    config = {
        "url": "http://grid.example.com/?key=${GRID_KEY}",
        "capabilities": {"headers": {"Authorization": "Bearer literal-1"}},
    }

    driver = Playwright(config)

    with pytest.raises(OpticsError) as error:
        await driver._launch_app_async(None, event_name=None)

    assert "k-7" not in str(error.value)
    assert "literal-1" not in str(error.value)


def test_launch_other_app_budgets_for_a_connect_too(monkeypatch):
    timeouts = _captured_timeouts(monkeypatch)
    config = {"capabilities": {"connect_timeout_ms": 120000, "navigation_timeout_ms": 30000, "action_timeout_ms": 5000}}

    Playwright(config).launch_other_app("https://example.com")

    assert timeouts == [155.0]


def test_literal_url_credentials_are_masked_and_kept_out_of_the_repr():
    from optics_framework.engines.drivers.playwright import RemoteBrowser

    remote = RemoteBrowser("http://user:pw-1@grid.example.com/?token=t-1", {"X-Empty": ""})
    message = "retrieving websocket url from http://user:pw-1@grid.example.com/?token=t-1"

    assert "pw-1" not in remote.mask(message)
    assert "t-1" not in remote.mask(message)
    assert "pw-1" not in repr(remote)
    assert "t-1" not in repr(remote)


async def test_an_existing_context_is_adopted_with_its_open_tab(monkeypatch):
    pw, browser = _fake_playwright(monkeypatch)
    tab = MagicMock()
    default = MagicMock(pages=[tab])
    browser.contexts = [default]
    driver = Playwright({"url": "http://127.0.0.1:9222", "capabilities": {"context": "existing"}})

    await driver._launch_app_async(None, event_name=None)

    assert driver._context is default
    assert driver.page is tab
    browser.new_context.assert_not_awaited()


async def test_an_existing_context_without_tabs_gets_a_new_one(monkeypatch):
    pw, browser = _fake_playwright(monkeypatch)
    tab = MagicMock()
    default = MagicMock(pages=[])
    default.new_page = AsyncMock(return_value=tab)
    browser.contexts = [default]
    driver = Playwright({"url": "http://127.0.0.1:9222", "capabilities": {"context": "existing"}})

    await driver._launch_app_async(None, event_name=None)

    assert driver.page is tab
    browser.new_context.assert_not_awaited()


async def test_a_browser_without_contexts_gets_a_new_one_even_when_asked_to_adopt(monkeypatch):
    pw, browser = _fake_playwright(monkeypatch)
    browser.contexts = []
    driver = Playwright({"url": HUB, "capabilities": {"context": "existing"}})

    await driver._launch_app_async(None, event_name=None)

    browser.new_context.assert_awaited_once()


@pytest.mark.parametrize(
    "capabilities, message",
    [({"context": "shared"}, "context must be"), ({"browser": "firefox"}, "Chromium-based browsers only")],
)
def test_settings_a_remote_browser_cannot_honour_are_config_errors(capabilities, message):
    driver = Playwright({"url": HUB, "capabilities": capabilities})

    with pytest.raises(OpticsError, match=message) as error:
        driver._remote()
    assert error.value.code == Code.E0501


async def test_percentages_use_the_window_size_when_no_viewport_is_emulated():
    driver = Playwright({})
    driver.page = MagicMock(viewport_size=None)
    driver.page.evaluate = AsyncMock(return_value={"width": 400, "height": 800})
    driver.page.mouse.click = AsyncMock()

    await driver._press_percentage_async(50, 25, 1)

    driver.page.mouse.click.assert_awaited_once_with(200, 200)


@pytest.mark.parametrize(
    "echoed",
    ["wss://hub.example:443/session/SECRET", "wss://hub.example/session/SECRET", "http://hub.example/session/SECRET/json/version/"],
)
def test_a_token_in_the_url_path_is_masked_however_the_endpoint_is_echoed(echoed):
    from optics_framework.engines.drivers.playwright import RemoteBrowser

    remote = RemoteBrowser("wss://hub.example:443/session/SECRET")

    masked = remote.mask(f"WebSocket error: {echoed} 401 Unauthorized")

    assert "SECRET" not in masked
    assert "hub.example" in masked
