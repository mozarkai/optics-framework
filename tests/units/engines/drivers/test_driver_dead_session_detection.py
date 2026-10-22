from selenium.common.exceptions import InvalidSessionIdException, WebDriverException
import pytest

from optics_framework.common.driver_interface import DriverInterface
from optics_framework.engines.drivers.appium import Appium
from optics_framework.engines.drivers.selenium import SeleniumDriver

try:
    from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
    from optics_framework.engines.drivers.playwright import Playwright

    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

pytestmark = pytest.mark.white_box


class _BackendWithoutRemoteSession(DriverInterface):
    pass


class TestDefaultIsNoDeadSession:
    def test_default_never_reports_a_dead_session(self):
        assert _BackendWithoutRemoteSession.is_dead_session_error(RuntimeError("boom")) is False
        assert _BackendWithoutRemoteSession.is_dead_session_error(
            InvalidSessionIdException("x")
        ) is False


class TestWebDriverBackends:
    @pytest.mark.parametrize("driver", [Appium, SeleniumDriver])
    def test_invalid_session_id_is_a_dead_session(self, driver):
        assert driver.is_dead_session_error(InvalidSessionIdException("not running")) is True

    @pytest.mark.parametrize("driver", [Appium, SeleniumDriver])
    def test_other_webdriver_failures_are_not_conclusive(self, driver):
        assert driver.is_dead_session_error(WebDriverException("unknown command")) is False
        assert driver.is_dead_session_error(RuntimeError("boom")) is False


@pytest.mark.skipif(not _HAS_PLAYWRIGHT, reason="playwright extra not installed")
class TestPlaywrightBackend:
    @pytest.mark.parametrize(
        "message",
        [
            "Target page, context or browser has been closed",
            "Target closed",
            "Target crashed",
        ],
    )
    def test_closed_target_is_a_dead_session(self, message):
        assert Playwright.is_dead_session_error(PlaywrightError(message)) is True

    def test_timeout_is_not_a_dead_session(self):
        assert Playwright.is_dead_session_error(
            PlaywrightTimeoutError("Timeout 30000ms exceeded")
        ) is False

    def test_non_playwright_error_is_ignored(self):
        assert Playwright.is_dead_session_error(InvalidSessionIdException("x")) is False
