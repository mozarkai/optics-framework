from unittest.mock import MagicMock, PropertyMock

import pytest
from selenium.common.exceptions import NoSuchElementException

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.drivers.appium import Appium


def _driver_instance(platform: str, focused=None, focus_error=None) -> Appium:
    webdriver = MagicMock()
    webdriver.capabilities = {"platformName": platform}
    webdriver.find_elements.return_value = []
    active = PropertyMock(side_effect=focus_error) if focus_error else PropertyMock(return_value=focused)
    type(webdriver.switch_to).active_element = active
    instance = Appium.__new__(Appium)
    instance.driver = webdriver
    instance.capabilities = {}
    instance.event_sdk = MagicMock()
    return instance


class TestIosEnterText:
    def test_types_into_the_focused_element(self):
        focused = MagicMock()
        instance = _driver_instance("iOS", focused=focused)

        instance.enter_text("hello")

        focused.send_keys.assert_called_once_with("hello")
        instance.driver.execute_script.assert_not_called()

    def test_keyboard_keyword_fails_too_when_nothing_is_focused(self):
        instance = _driver_instance("iOS", focus_error=NoSuchElementException("No element is currently focused"))

        with pytest.raises(OpticsError) as raised:
            instance.enter_text_using_keyboard("hello")

        assert raised.value.code == Code.E0401
        instance.driver.execute_script.assert_not_called()


class TestAndroidEnterTextUnchanged:
    def test_android_still_uses_mobile_type(self):
        instance = _driver_instance("Android")

        instance.enter_text("hello")

        instance.driver.execute_script.assert_called_once_with("mobile: type", {"text": "hello"})
