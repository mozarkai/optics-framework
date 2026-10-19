from unittest.mock import MagicMock

import pytest

from optics_framework.engines.drivers.appium import Appium


def _driver_instance(capabilities: dict) -> Appium:
    webdriver = MagicMock()
    webdriver.capabilities = capabilities
    instance = Appium.__new__(Appium)
    instance.driver = webdriver
    instance.capabilities = {}
    instance.event_sdk = MagicMock()
    return instance


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        ({"platformName": "Android"}, "android"),
        ({"platformName": "iOS"}, "ios"),
        ({"platformName": "Android", "browserName": ""}, "android"),
        ({"platformName": "TizenTV"}, None),
        ({"platformName": "LGTV"}, None),
        ({"platformName": "Android", "browserName": "chrome"}, None),
        ({"platformName": "Android", "appium:autoWebview": True}, None),
    ],
)
def test_native_ui_platform(capabilities, expected):
    assert _driver_instance(capabilities).native_ui_platform() == expected
