import pytest

from optics_framework.common.error import Code, OpticsError
from tests.units.fake_appium import AppiumServer, appium_driver


def test_a_stop_that_the_desktop_host_refuses_fails_the_keyword(monkeypatch):
    server = AppiumServer({"optics_terminate_app": RuntimeError("connection lost")})
    driver = appium_driver(monkeypatch, server, {"platformName": "Linux", "appium:automationName": "Xa11y",
                                                 "appium:app": "mousepad"})
    driver.start_session()
    with pytest.raises(OpticsError) as caught:
        driver.force_terminate_app("mousepad")
    assert caught.value.code == Code.E0401
    assert server.commands("optics_terminate_app") == [("optics_terminate_app", {"appId": "mousepad"})]
