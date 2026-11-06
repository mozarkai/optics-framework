from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from selenium.common.exceptions import WebDriverException

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.drivers.appium import Appium

pytestmark = pytest.mark.white_box


def _driver_instance(platform: str) -> Appium:
    webdriver = MagicMock()
    webdriver.capabilities = {"platformName": platform}
    instance = Appium.__new__(Appium)
    instance.driver = webdriver
    instance.capabilities = {"platformName": platform}
    instance.event_sdk = MagicMock()
    return instance


def _android(*packages: str) -> Appium:
    instance = _driver_instance("Android")
    type(instance.driver).current_package = PropertyMock(side_effect=list(packages))
    return instance


def _ios(front: str, alert: bool = False) -> Appium:
    instance = _driver_instance("iOS")

    def execute_script(script, *args):
        if script == "mobile: activeAppInfo":
            return {"bundleId": front}
        if script == "mobile: alert" and alert:
            return ["Allow", "Don't Allow"]
        raise WebDriverException("no alert")

    instance.driver.execute_script.side_effect = execute_script
    return instance


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("optics_framework.engines.drivers.appium.time.sleep"):
        yield


def test_launch_waits_for_the_app_to_come_to_the_front():
    instance = _android("com.google.android.apps.nexuslauncher", "com.example.app")

    instance.launch_other_app("com.example.app")

    instance.driver.activate_app.assert_called_once_with("com.example.app")


def test_launch_fails_naming_the_app_in_front_when_the_app_closes():
    instance = _ios("com.apple.springboard")
    instance.LAUNCH_SETTLE_TIMEOUT_S = 0

    with pytest.raises(OpticsError) as excinfo:
        instance.launch_other_app("com.apple.Preferences")

    assert excinfo.value.code == Code.E0401
    assert "com.apple.Preferences" in str(excinfo.value)
    assert "com.apple.springboard" in str(excinfo.value)


def test_launch_app_in_a_running_session_checks_the_front_app():
    instance = _android("com.google.android.apps.nexuslauncher")
    instance.LAUNCH_SETTLE_TIMEOUT_S = 0

    with pytest.raises(OpticsError) as excinfo:
        instance.launch_app("com.example.app")

    assert excinfo.value.code == Code.E0401


def test_a_system_alert_over_the_app_counts_as_launched():
    instance = _ios("com.apple.springboard", alert=True)
    instance.LAUNCH_SETTLE_TIMEOUT_S = 0

    instance.launch_other_app("com.example.app")


def test_a_permission_prompt_over_the_app_counts_as_launched():
    instance = _android("com.google.android.permissioncontroller")
    instance.LAUNCH_SETTLE_TIMEOUT_S = 0

    instance.launch_other_app("com.example.app")


def test_launch_passes_when_the_front_app_cannot_be_read():
    instance = _driver_instance("iOS")
    instance.driver.execute_script.side_effect = WebDriverException("Unknown mobile command")

    instance.launch_other_app("com.example.app")


def test_tv_launch_skips_the_front_app_check():
    instance = _driver_instance("TizenTV")

    instance.launch_other_app("com.example.tvapp")

    instance.driver.execute_script.assert_not_called()


def test_launch_app_without_an_app_in_a_running_session_checks_nothing():
    instance = _driver_instance("Android")
    front = PropertyMock()
    type(instance.driver).current_package = front

    instance.launch_app()

    instance.driver.activate_app.assert_not_called()
    front.assert_not_called()


def test_launch_app_without_an_app_only_starts_the_session():
    instance = _driver_instance("iOS")
    instance.driver = None
    instance.start_session = MagicMock(return_value="session-1")

    assert instance.launch_app() == "session-1"

    instance.start_session.assert_called_once_with(app_package=None, app_activity=None, event_name=None)
