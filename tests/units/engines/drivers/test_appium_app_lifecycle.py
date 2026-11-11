from unittest.mock import MagicMock

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.drivers.appium import Appium

pytestmark = pytest.mark.white_box


def _driver_instance(platform: str) -> Appium:
    webdriver = MagicMock()
    webdriver.capabilities = {"platformName": platform}
    instance = Appium.__new__(Appium)
    instance.driver = webdriver
    instance.capabilities = {}
    instance.event_sdk = MagicMock()
    return instance


def test_a_stop_that_the_phone_refuses_fails_the_keyword():
    instance = _driver_instance("Android")
    instance.driver.terminate_app.side_effect = RuntimeError("connection lost")

    with pytest.raises(OpticsError) as excinfo:
        instance.force_terminate_app("com.example")

    assert excinfo.value.code == Code.E0401


def test_a_stop_that_the_desktop_host_refuses_fails_the_keyword():
    instance = _driver_instance("Linux")
    instance.driver.execute.side_effect = RuntimeError("connection lost")

    with pytest.raises(OpticsError) as excinfo:
        instance.force_terminate_app("mousepad")

    assert excinfo.value.code == Code.E0401


def test_launch_app_with_no_app_leaves_a_tv_session_alone():
    instance = _driver_instance("TizenTV")
    instance.driver.capabilities = {"platformName": "TizenTV", "appium:appPackage": "abc.Player"}
    instance._is_session_alive = lambda: True

    instance.launch_app()

    instance.driver.activate_app.assert_not_called()
