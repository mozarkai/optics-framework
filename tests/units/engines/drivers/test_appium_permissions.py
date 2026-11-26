"""Permissions on the sessions the Appium driver starts: set before the app opens, on Android only."""
import pytest
from selenium.common.exceptions import WebDriverException

from tests.units.fake_appium import AppiumServer, appium_driver

pytestmark = pytest.mark.white_box

CAMERA, FINE, COARSE = ("android.permission.CAMERA", "android.permission.ACCESS_FINE_LOCATION",
                        "android.permission.ACCESS_COARSE_LOCATION")
ANDROID = {"platformName": "Android", "appium:automationName": "UiAutomator2", "appium:appPackage": "in.app",
           "appium:appActivity": ".Main"}


def change(action, permission):
    return "mobile: changePermissions", {"action": action, "appPackage": "in.app", "permissions": [permission]}


def test_a_new_android_session_sets_the_permissions_before_it_opens_the_app(monkeypatch):
    server = AppiumServer()
    appium_driver(monkeypatch, server, ANDROID, permissions={"camera": "allow", "notifications": "deny"}).start_session()
    assert server.log[0][1]["appium:autoLaunch"] is False
    assert server.commands("mobile: changePermissions", "mobile: startActivity") == [
        change("grant", CAMERA), change("revoke", "android.permission.POST_NOTIFICATIONS"),
        ("mobile: startActivity", {"intent": "in.app/.Main"}),
    ]


def test_a_session_without_permissions_opens_the_app_itself(monkeypatch):
    server = AppiumServer()
    appium_driver(monkeypatch, server, ANDROID).start_session()
    assert "appium:autoLaunch" not in server.log[0][1]
    assert server.commands("mobile: changePermissions", "mobile: startActivity") == []


def test_attaching_to_a_running_session_sets_the_permissions_too(monkeypatch):
    server = AppiumServer(device={"platformName": "Android", "appPackage": "in.app"})
    driver = appium_driver(monkeypatch, server, {**ANDROID, "appium:sessionId": "running"}, permissions={"camera": "allow"})
    assert driver.start_session() == "running"
    assert server.commands("mobile: changePermissions") == [change("grant", CAMERA)]


def test_a_permission_the_app_does_not_declare_does_not_stop_the_others(monkeypatch):
    def change_permissions(args):
        if args["permissions"] == [FINE]:
            raise WebDriverException("not a changeable permission type")

    server = AppiumServer({"mobile: changePermissions": change_permissions})
    appium_driver(monkeypatch, server, ANDROID, permissions={"location": "allow", "camera": "allow"}).start_session()
    assert server.commands("mobile: changePermissions") == [
        change("grant", FINE), change("grant", COARSE), change("grant", CAMERA)]


def test_on_ios_permissions_set_nothing_and_say_where_the_prompt_rule_goes(monkeypatch, internal_log):
    server = AppiumServer()
    ios = {"platformName": "iOS", "appium:automationName": "XCUITest", "appium:bundleId": "in.app"}
    appium_driver(monkeypatch, server, ios, permissions={"camera": "allow"}).start_session()
    assert [entry for entry in server.log if entry[0] not in ("new_session",)] == []
    assert ("WARNING", "'permissions' sets Android permissions only. On iOS answer system prompts with the "
                       "appium:settings[autoClickAlertSelector] capability.") in internal_log


@pytest.mark.parametrize("configured, running", [
    pytest.param({}, {"platformName": "Android", "appPackage": "in.app"}, id="only a session id"),
    pytest.param({"appium:appPackage": "in.app"}, {"platformName": "Android"}, id="a package the session does not report"),
    pytest.param({"appPackage": "old.app"}, {"platformName": "Android", "appium:appPackage": "in.app"},
                 id="a package the session reports under its other name"),
])
def test_attaching_sets_the_permissions_of_the_running_app(monkeypatch, configured, running):
    server = AppiumServer({"getSession": {"value": {"capabilities": running}}}, device=running)
    appium_driver(monkeypatch, server, {"appium:sessionId": "running", **configured}, permissions={"camera": "allow"}).start_session()
    assert server.commands("mobile: changePermissions") == [change("grant", CAMERA)]
