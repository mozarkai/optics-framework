"""Appium.launch_app must not trust a client-side cached session id.

A remote hub can drop a session while it sits idle; the client keeps its cached
``driver.session_id`` regardless. ``launch_app`` used to gate purely on
``self.driver is None``, so re-launching against a dead session returned the stale
id in ~0s and reported success while nothing had been launched. It now probes the
server before deciding there is nothing to do.
"""
from unittest.mock import MagicMock

import pytest
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException

from optics_framework.engines.drivers.appium import Appium

pytestmark = pytest.mark.white_box


def _driver_instance(webdriver) -> Appium:
    """An Appium driver wrapping ``webdriver``, bypassing config-driven __init__."""
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
    """Only an ``invalid session id`` response is treated as conclusive."""

    def test_live_session_is_alive(self):
        assert _driver_instance(_webdriver())._is_session_alive() is True

    def test_dropped_session_is_not_alive(self):
        instance = _driver_instance(
            _webdriver(probe_error=InvalidSessionIdException("the session is not running"))
        )
        assert instance._is_session_alive() is False

    def test_unsupported_probe_leaves_session_presumed_alive(self):
        """A TV profile may reject the probe command; that is not a dead session."""
        instance = _driver_instance(
            _webdriver(probe_error=WebDriverException("unknown command: window rect"))
        )
        assert instance._is_session_alive() is True

    def test_no_driver_is_not_alive(self):
        assert _driver_instance(None)._is_session_alive() is False


class TestLaunchAppRecreatesDeadSession:
    """The false-positive ``launch_app`` success is the symptom being fixed."""

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
