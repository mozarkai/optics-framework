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


def test_ios_presses_a_plain_switch_itself():
    switch = _switch_row([])

    _driver_instance("iOS").press_element(switch, 1)

    switch.click.assert_called_once()
