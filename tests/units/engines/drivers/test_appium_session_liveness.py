from unittest.mock import MagicMock

import pytest
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException

from optics_framework.engines.drivers.appium import Appium

pytestmark = pytest.mark.white_box


def _driver_instance(webdriver) -> Appium:
    instance = Appium.__new__(Appium)
    instance.driver = webdriver
    return instance


def _webdriver(session_id: str = "stale-session-id", probe_error=None) -> MagicMock:
    webdriver = MagicMock()
    webdriver.session_id = session_id
    if probe_error is not None:
        webdriver.get_window_size.side_effect = probe_error
    return webdriver


class TestIsSessionAlive:
    def test_live_session_is_alive(self):
        assert _driver_instance(_webdriver())._is_session_alive() is True

    def test_dropped_session_is_not_alive(self):
        instance = _driver_instance(
            _webdriver(probe_error=InvalidSessionIdException("the session is not running"))
        )
        assert instance._is_session_alive() is False

    def test_unsupported_probe_leaves_session_presumed_alive(self):
        instance = _driver_instance(
            _webdriver(probe_error=WebDriverException("unknown command: window rect"))
        )
        assert instance._is_session_alive() is True

    def test_no_driver_is_not_alive(self):
        assert _driver_instance(None)._is_session_alive() is False


class TestLaunchAppRecreatesDeadSession:
    def test_stale_session_is_restarted(self):
        instance = _driver_instance(
            _webdriver(probe_error=InvalidSessionIdException("the session is not running"))
        )
        instance.start_session = MagicMock(return_value="fresh-session-id")

        result = instance.launch_app(app_identifier="com.example.app")

        instance.start_session.assert_called_once_with(
            app_package="com.example.app", app_activity=None, event_name=None
        )
        assert result == "fresh-session-id"

    def test_live_session_is_left_alone(self):
        webdriver = _webdriver(session_id="live-session-id")
        instance = _driver_instance(webdriver)
        instance.start_session = MagicMock()

        result = instance.launch_app(app_identifier="com.example.app")

        instance.start_session.assert_not_called()
        assert result == "live-session-id"

    def test_absent_driver_starts_a_session_without_probing(self):
        instance = _driver_instance(None)
        instance.start_session = MagicMock(return_value="new-session-id")

        assert instance.launch_app() == "new-session-id"
        instance.start_session.assert_called_once()
