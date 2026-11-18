from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from playwright.async_api import Error as PlaywrightError

from optics_framework.engines.drivers import playwright_locator

pytestmark = pytest.mark.white_box


def _driver(page):
    return SimpleNamespace(page=page, run=lambda coro: coro)


def test_a_closed_browser_is_not_mistaken_for_a_missing_element():
    page = MagicMock()
    page.locator.return_value.evaluate_all.side_effect = PlaywrightError("Target closed")
    driver = _driver(page)
    with pytest.raises(PlaywrightError):
        playwright_locator.find(driver, "#go")
