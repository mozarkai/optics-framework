from unittest.mock import MagicMock

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.drivers.appium import Appium

pytestmark = pytest.mark.white_box


def _driver_instance(platform: str, nav_buttons: list) -> Appium:
    webdriver = MagicMock()
    webdriver.capabilities = {"platformName": platform}
    webdriver.find_elements.return_value = nav_buttons
    instance = Appium.__new__(Appium)
    instance.driver = webdriver
    instance.capabilities = {}
    instance.event_sdk = MagicMock()
    return instance


def test_android_presses_back():
    instance = _driver_instance("Android", nav_buttons=[])

    instance.go_back()

    instance.driver.back.assert_called_once()
    instance.driver.find_elements.assert_not_called()


def test_ios_taps_the_navigation_bar_back_button():
    instance = _driver_instance("iOS", nav_buttons=[MagicMock()])

    instance.go_back()

    instance.driver.back.assert_called_once()


def test_ios_without_a_navigation_bar_button_fails_instead_of_passing():
    instance = _driver_instance("iOS", nav_buttons=[])

    with pytest.raises(OpticsError) as excinfo:
        instance.go_back(event_name="back")

    assert excinfo.value.code == Code.E0401
    instance.driver.back.assert_not_called()
    instance.event_sdk.capture_event.assert_not_called()
