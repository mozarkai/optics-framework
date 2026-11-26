"""Launch App keeps a session the server still holds, starts a new one when the server has dropped it, and opens the app or deep link it names."""
import pytest
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException

from tests.units.fake_appium import AppiumServer, appium_driver

ANDROID = {"platformName": "Android", "appium:automationName": "UiAutomator2", "appium:appPackage": "in.app"}


def launched(monkeypatch, probe):
    server = AppiumServer({"get_window_size": probe})
    driver = appium_driver(monkeypatch, server, ANDROID)
    driver.start_session()
    return driver.launch_app(), server


def test_a_session_that_cannot_answer_the_probe_is_kept(monkeypatch):
    session, server = launched(monkeypatch, WebDriverException("unknown command: window rect"))
    assert (session, server.sessions) == ("session-1", 1)
    assert server.commands("activate_app") == [("activate_app", "in.app")]


def test_a_session_the_server_dropped_is_started_again(monkeypatch):
    session, server = launched(monkeypatch, InvalidSessionIdException("A session is either terminated or not started"))
    assert (session, server.sessions) == ("session-2", 2)


def test_with_no_session_launch_starts_one_without_a_probe(monkeypatch):
    server = AppiumServer()
    assert appium_driver(monkeypatch, server, ANDROID).launch_app() == "session-1"
    assert server.commands("get_window_size") == []


@pytest.mark.parametrize(("target", "sent"), [
    pytest.param("in.other", [("activate_app", "in.other")], id="an app id"),
    pytest.param("myapp://orders/7", [("mobile: deepLink", {"url": "myapp://orders/7"})], id="a deep link"),
])
def test_launch_app_on_a_live_session_opens_what_it_names(monkeypatch, target, sent):
    server = AppiumServer()
    driver = appium_driver(monkeypatch, server, ANDROID)
    driver.start_session()
    driver.launch_app(target)
    assert server.commands("activate_app", "mobile: deepLink") == sent
