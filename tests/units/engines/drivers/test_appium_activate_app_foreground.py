from unittest.mock import MagicMock

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.drivers import appium as appium_module
from optics_framework.engines.drivers.appium import Appium

BACKGROUND, FOREGROUND = 3, 4


def _instance(states) -> Appium:
    instance = Appium.__new__(Appium)
    instance.driver = MagicMock()
    instance.driver.query_app_state.side_effect = states
    instance._is_desktop = lambda: False
    return instance


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(appium_module, "_FOREGROUND_WAIT_S", 0)
    monkeypatch.setattr(appium_module.time, "sleep", lambda _: None)


def test_an_app_that_never_reaches_the_foreground_fails_the_launch():
    instance = _instance(lambda _: BACKGROUND)

    with pytest.raises(OpticsError) as excinfo:
        instance._activate_app(instance.driver, "com.android.settings")

    assert excinfo.value.code == Code.E0401
    assert "com.android.settings" in str(excinfo.value)


def test_an_app_that_arrives_a_moment_late_passes(monkeypatch):
    monkeypatch.setattr(appium_module, "_FOREGROUND_WAIT_S", 5)
    instance = _instance([BACKGROUND, BACKGROUND, FOREGROUND])

    instance._activate_app(instance.driver, "com.android.settings")

    instance.driver.activate_app.assert_called_once_with("com.android.settings")


def test_a_driver_that_cannot_report_the_app_state_leaves_the_launch_unchecked():
    instance = _instance(RuntimeError("not supported"))

    instance._activate_app(instance.driver, "com.android.settings")

    instance.driver.activate_app.assert_called_once_with("com.android.settings")
