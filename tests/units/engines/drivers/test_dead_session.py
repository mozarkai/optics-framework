import pytest
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException

from optics_framework.common.driver_interface import DriverInterface
from optics_framework.engines.drivers.appium import Appium
from optics_framework.engines.drivers.selenium import SeleniumDriver


@pytest.mark.parametrize("driver", [Appium, SeleniumDriver])
@pytest.mark.parametrize(("error", "dead"), [
    (InvalidSessionIdException("not running"), True),
    (WebDriverException("unknown command"), False),
    (RuntimeError("boom"), False),
])
def test_only_a_lost_webdriver_session_is_dead(driver, error, dead):
    assert driver.is_dead_session_error(error) is dead
    assert DriverInterface.is_dead_session_error(error) is False


@pytest.mark.parametrize(("message", "dead"), [
    ("Target page, context or browser has been closed", True),
    ("Target crashed", True),
    ("Timeout 30000ms exceeded", False),
])
def test_only_a_closed_browser_is_a_dead_playwright_session(message, dead):
    pytest.importorskip("playwright")
    from playwright.async_api import Error

    from optics_framework.engines.drivers.playwright import Playwright

    assert Playwright.is_dead_session_error(Error(message)) is dead
    assert Playwright.is_dead_session_error(InvalidSessionIdException("x")) is False
