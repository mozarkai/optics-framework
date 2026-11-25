"""Web pages on a phone: the driver enters the page in front for the body, then goes back to the native screen."""
import pytest
from appium.common.exceptions import NoSuchContextException
from selenium.common.exceptions import WebDriverException

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.elementsources.appium_web import AppiumWeb
from tests.units.fake_appium import AppiumServer, started_driver

NATIVE = "NATIVE_APP"
ANDROID = {"platformName": "Android", "appium:automationName": "UiAutomator2", "appium:appPackage": "in.app"}
IOS = {"platformName": "iOS", "appium:automationName": "XCUITest", "appium:bundleId": "in.app"}


def refusing(name, error):
    def context(requested):
        if requested == name:
            raise error
    return context


def chrome(package, webview, context):
    return {"current_package": package, "window_handles": ["tab"], "current_window_handle": "tab", "contexts": [NATIVE],
            "mobile: getContexts": [{"webviewName": webview, "pages": [{"url": "https://shop.test"}],
                                     "info": {"Android-Package": package}}], "context": context}


def test_a_session_that_cannot_go_back_to_the_native_screen_is_lost(monkeypatch):
    server = AppiumServer(chrome("com.android.chrome", "CHROMIUM",
                                 refusing(NATIVE, WebDriverException("context switch failed"))))
    with pytest.raises(OpticsError) as caught, started_driver(monkeypatch, server, ANDROID).web_context():
        pass
    assert caught.value.code == Code.E0106


def test_a_web_view_that_is_gone_makes_the_page_unreadable(monkeypatch):
    webview = "WEBVIEW_com.example.app"
    server = AppiumServer(chrome("com.example.app", webview, refusing(webview, NoSuchContextException("no such context"))))
    with pytest.raises(OpticsError) as caught, started_driver(monkeypatch, server, ANDROID).web_context():
        pass
    assert caught.value.code == Code.E0201
    assert server.commands("context") == [("context", webview), ("context", NATIVE)]


def test_ios_enters_a_safari_sheet_before_the_app_s_own_page(monkeypatch):
    server = AppiumServer({
        "mobile: activeAppInfo": {"pid": 100, "bundleId": "in.app"},
        "mobile: getContexts": [{"id": NATIVE}, {"id": "WEBVIEW_100.1", "bundleId": "process-App"},
                                {"id": "WEBVIEW_300.1", "bundleId": "com.apple.SafariViewService"}],
        "page script": True,
    })
    with started_driver(monkeypatch, server, IOS).web_context():
        pass
    assert server.commands("context") == [("context", "WEBVIEW_300.1"), ("context", NATIVE)]


def test_page_boxes_are_placed_on_the_screen_through_a_probe(monkeypatch):
    """Safari draws the page under a 62 pt toolbar: the probe over its 402 x 714 viewport shows at y 62 natively."""
    def page(script, label=None):
        if label and f"name='{label}'" not in server.page:
            server.page = server.page.replace("</hierarchy>", f'<p name="{label}" bounds="[0,62][402,776]"/></hierarchy>')
        return [0, 0, 402, 714]

    server = AppiumServer({"current_context": "WEBVIEW_100.1", "page script": page},
                          page='<hierarchy><w class="XCUIElementTypeWebView" bounds="[0,62][402,776]"/></hierarchy>')
    rects = started_driver(monkeypatch, server, IOS).web_rects_to_screen([{"x": 24, "y": 24, "width": 354, "height": 38}, None])
    assert rects == [{"x": 24, "y": 86, "width": 354, "height": 38}, None]
    assert server.commands("context") == [("context", NATIVE), ("context", "WEBVIEW_100.1")]


def test_a_browser_session_is_read_natively_not_through_its_web_context(monkeypatch):
    server = AppiumServer()
    web = AppiumWeb(driver=started_driver(monkeypatch, server, {**IOS, "browserName": "Safari"}))
    assert web.locate("css=#q") is None
    assert web.get_interactive_elements() == []
    assert server.commands("context", "mobile: getContexts") == []


def test_one_look_answers_each_element_in_one_visit_to_the_page(monkeypatch):
    server = AppiumServer(chrome("com.android.chrome", "CHROMIUM", None), page='<html><input id="q"/></html>')
    web = AppiumWeb(driver=started_driver(monkeypatch, server, ANDROID))
    assert web.check_elements(["css=#q", "css=#missing"]) == [True, False]
    assert server.commands("context") == [("context", "CHROMIUM"), ("context", NATIVE)]
