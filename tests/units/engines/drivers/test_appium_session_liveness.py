from unittest.mock import MagicMock

from selenium.common.exceptions import WebDriverException

from optics_framework.engines.drivers.appium import Appium


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


class TestLaunchAppRecreatesDeadSession:

    def test_unsupported_probe_leaves_session_presumed_alive(self):
        instance = _driver_instance(
            _webdriver(session_id="live-session-id", probe_error=WebDriverException("unknown command: window rect"))
        )
        instance.start_session = MagicMock()

        assert instance.launch_app(app_identifier="com.example.app") == "live-session-id"
        instance.start_session.assert_not_called()

    def test_absent_driver_starts_a_session_without_probing(self):
        instance = _driver_instance(None)
        instance.start_session = MagicMock(return_value="new-session-id")

        assert instance.launch_app() == "new-session-id"
        instance.start_session.assert_called_once()
