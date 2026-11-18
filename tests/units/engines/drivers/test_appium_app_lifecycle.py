from unittest.mock import MagicMock

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.drivers.appium import Appium


def _driver_instance(platform: str) -> Appium:
    webdriver = MagicMock()
    webdriver.capabilities = {"platformName": platform}
    instance = Appium.__new__(Appium)
    instance.driver = webdriver
    instance.capabilities = {}
    instance.event_sdk = MagicMock()
    return instance


def test_a_stop_that_the_desktop_host_refuses_fails_the_keyword():
    instance = _driver_instance("Linux")
    instance.driver.execute.side_effect = RuntimeError("connection lost")

    with pytest.raises(OpticsError) as excinfo:
        instance.force_terminate_app("mousepad")

    assert excinfo.value.code == Code.E0401
