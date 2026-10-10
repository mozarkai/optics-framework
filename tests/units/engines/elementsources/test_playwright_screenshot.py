"""PlaywrightScreenshot capture retry -- regression cover for headed Chromium under xvfb
(GitHub-hosted Ubuntu runners), where Page.captureScreenshot intermittently fails with
"Unable to capture screenshot" on a loaded, visible page and succeeds a moment later.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

pytest.importorskip("playwright")
from playwright.async_api import Error as PlaywrightError  # noqa: E402

from optics_framework.engines.elementsources import playwright_screenshot as pws  # noqa: E402
from optics_framework.engines.elementsources.playwright_screenshot import PlaywrightScreenshot  # noqa: E402

pytestmark = pytest.mark.white_box

PNG = b"\x89PNG fake"
TRANSIENT = PlaywrightError("Protocol error (Page.captureScreenshot): Unable to capture screenshot")


@pytest.fixture
def sleep(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(pws, "asyncio", SimpleNamespace(sleep=mock))
    return mock


def _source(*outcomes) -> tuple[PlaywrightScreenshot, AsyncMock]:
    screenshot = AsyncMock(side_effect=list(outcomes))
    driver = MagicMock()
    driver.page.screenshot = screenshot
    return PlaywrightScreenshot(driver=driver), screenshot


def test_transient_failure_is_retried_after_a_short_wait(sleep):
    source, screenshot = _source(TRANSIENT, PNG)

    assert source.capture_screenshot_bytes() == PNG
    assert screenshot.await_count == 2
    assert sleep.await_args_list == [call(0.1)]


def test_recovers_on_the_last_attempt(sleep):
    source, screenshot = _source(TRANSIENT, TRANSIENT, PNG)

    assert source.capture_screenshot_bytes() == PNG
    assert screenshot.await_count == 3
    assert sleep.await_args_list == [call(0.1), call(0.3)]


def test_gives_up_after_three_attempts(sleep):
    source, screenshot = _source(TRANSIENT, TRANSIENT, TRANSIENT)

    with pytest.raises(RuntimeError, match="Unable to capture screenshot"):
        source.capture_screenshot_bytes()
    assert screenshot.await_count == 3
    assert sleep.await_args_list == [call(0.1), call(0.3)]


def test_other_errors_are_not_retried(sleep):
    source, screenshot = _source(PlaywrightError("Target page, context or browser has been closed"))

    with pytest.raises(RuntimeError, match="has been closed"):
        source.capture_screenshot_bytes()
    assert screenshot.await_count == 1
    sleep.assert_not_awaited()
