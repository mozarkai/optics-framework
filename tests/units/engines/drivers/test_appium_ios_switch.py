from unittest.mock import MagicMock

import pytest

from optics_framework.engines.drivers.appium import Appium

pytestmark = pytest.mark.white_box


def _driver_instance(platform: str) -> Appium:
    instance = Appium.__new__(Appium)
    instance.driver = MagicMock()
    instance.driver.capabilities = {"platformName": platform}
    instance.capabilities = {}
    instance.event_sdk = MagicMock()
    return instance


def _switch_row(inner: list) -> MagicMock:
    row = MagicMock()
    row.tag_name = "XCUIElementTypeSwitch"
    row.find_elements.return_value = inner
    return row


def test_ios_presses_the_switch_inside_a_switch_row():
    knob = MagicMock()
    row = _switch_row([knob])

    _driver_instance("iOS").press_element(row, 1)

    knob.click.assert_called_once()
    row.click.assert_not_called()


def test_ios_presses_a_plain_switch_itself():
    switch = _switch_row([])

    _driver_instance("iOS").press_element(switch, 1)

    switch.click.assert_called_once()


def test_android_presses_the_element_it_was_given():
    row = _switch_row([MagicMock()])

    _driver_instance("Android").press_element(row, 1)

    row.click.assert_called_once()
