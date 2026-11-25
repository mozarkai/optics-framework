"""Enter Text types where the keyboard focus is on iOS, and through 'mobile: type' on Android."""
import pytest
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import NoSuchElementException

from optics_framework.common.error import Code, OpticsError
from tests.units.fake_appium import AppiumServer, appium_driver

IOS = {"platformName": "iOS", "appium:automationName": "XCUITest", "appium:bundleId": "in.app"}
FORM = '<hierarchy><f class="XCUIElementTypeTextField" bounds="[0,0][100,40]"/></hierarchy>'
TYPING = ("send_keys", "mobile: type", "mobile: keys", "press_keycode")


def started(monkeypatch, server, capabilities):
    driver = appium_driver(monkeypatch, server, capabilities)
    driver.start_session()
    return driver


def test_ios_types_into_the_focused_field(monkeypatch):
    server = AppiumServer(page=FORM)
    server.answers["active_element"] = server.nodes(AppiumBy.XPATH, "//f")[0]
    started(monkeypatch, server, IOS).enter_text("hello")
    assert server.commands(*TYPING) == [("send_keys", "[0,0][100,40]", "hello")]


def test_ios_keyboard_typing_fails_when_nothing_has_focus(monkeypatch):
    server = AppiumServer({"active_element": NoSuchElementException("No element is currently focused")}, page=FORM)
    with pytest.raises(OpticsError) as caught:
        started(monkeypatch, server, IOS).enter_text_using_keyboard("hello")
    assert caught.value.code == Code.E0401
    assert server.commands(*TYPING) == []


def test_android_types_through_mobile_type(monkeypatch):
    server = AppiumServer()
    started(monkeypatch, server, {"platformName": "Android", "appium:automationName": "UiAutomator2",
                                  "appium:appPackage": "in.app"}).enter_text("hello")
    assert server.commands(*TYPING) == [("mobile: type", {"text": "hello"})]
