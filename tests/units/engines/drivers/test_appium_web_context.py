from unittest.mock import MagicMock

import pytest
from selenium.common.exceptions import TimeoutException, WebDriverException

from optics_framework.common.error import Code, OpticsError
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
        ({"platformName": "Android", "browserName": "chrome"}, None),
        ({"platformName": "Android", "appium:autoWebview": True}, None),
        ({"platformName": "iOS", "browserName": "Safari"}, None),
    ],
)
def test_native_ui_platform(capabilities, expected):
    assert _driver_instance(capabilities).native_ui_platform() == expected


def test_android_sessions_keep_webview_version_details():
    capabilities = {"platformName": "Android"}
    instance = _driver_instance(capabilities)
    instance.appium_server_url = "http://127.0.0.1:4723"
    _, defaults = instance._get_platform_and_options(capabilities)

    # Without them chromedriver is matched to the system WebView's version, not Chrome's.
    assert defaults.get("enableWebviewDetailsCollection", True) is True
    assert defaults["ensureWebviewsHavePages"] is False


def test_web_context_raises_e0106_when_native_context_cannot_be_restored():
    instance = _driver_instance({"platformName": "Android"})
    webdriver = instance.driver
    webdriver.current_package = "com.android.chrome"
    instance._known_web_contexts = {(webdriver.session_id, "com.android.chrome"): ("CHROMIUM", 0)}
    webdriver.window_handles = ["tab"]

    def switch(context):
        if context == Appium.NATIVE_CONTEXT:
            raise WebDriverException("context switch failed")

    webdriver.switch_to.context.side_effect = switch

    with pytest.raises(OpticsError) as excinfo, instance.web_context():
        pass

    assert excinfo.value.code == Code.E0106


def _ios_instance(contexts: list[dict], visible: set[str], frozen: frozenset[str] = frozenset()) -> Appium:
    instance = _driver_instance({"platformName": "iOS"})
    instance._frozen_web_contexts = set()
    instance._ios_web_context = None
    webdriver = instance.driver
    current = {"context": Appium.NATIVE_CONTEXT}
    instance.entered = []

    def switch(name):
        current["context"] = name
        instance.entered.append(name)

    webdriver.switch_to.context.side_effect = switch

    def execute_script(script, *args):
        if script == "mobile: activeAppInfo":
            return {"pid": 100, "bundleId": "org.example.app"}
        if script == "mobile: getContexts":
            return [{"id": Appium.NATIVE_CONTEXT}, *contexts]
        if current["context"] in frozen:
            raise TimeoutException("JavaScript execution is blocked")
        return current["context"] in visible

    webdriver.execute_script.side_effect = execute_script
    return instance


def test_ios_web_context_enters_the_visible_page_of_the_app_in_front():
    instance = _ios_instance(
        [
            {"id": "WEBVIEW_200.1", "bundleId": "com.apple.mobilesafari"},
            {"id": "WEBVIEW_100.1", "bundleId": "process-App"},
            {"id": "WEBVIEW_100.2", "bundleId": "process-App"},
        ],
        visible={"WEBVIEW_200.1", "WEBVIEW_100.2"},
    )

    instance._enter_ios_web_context(instance.driver)

    # Safari's visible page belongs to another process; the app's own hidden page is passed over.
    assert "WEBVIEW_200.1" not in instance.entered
    assert instance.entered[-1] == "WEBVIEW_100.2"


def test_ios_web_context_tries_a_safari_sheet_first():
    instance = _ios_instance(
        [{"id": "WEBVIEW_100.1", "bundleId": "process-App"},
         {"id": "WEBVIEW_300.1", "bundleId": "com.apple.SafariViewService"}],
        visible={"WEBVIEW_100.1", "WEBVIEW_300.1"},
    )

    instance._enter_ios_web_context(instance.driver)

    assert instance.entered == ["WEBVIEW_300.1"]


def test_ios_web_context_skips_a_frozen_page_until_it_changes():
    busy = {"id": "WEBVIEW_100.1", "bundleId": "process-App", "title": ""}
    visible = {"WEBVIEW_100.2"}
    instance = _ios_instance(
        [busy, {"id": "WEBVIEW_100.2", "bundleId": "process-App"}], visible=visible, frozen=frozenset({"WEBVIEW_100.1"})
    )
    instance._enter_ios_web_context(instance.driver)
    visible.clear()
    instance.entered.clear()

    with pytest.raises(OpticsError):
        instance._enter_ios_web_context(instance.driver)
    assert "WEBVIEW_100.1" not in instance.entered

    busy["title"] = "Loaded"
    instance.entered.clear()
    with pytest.raises(OpticsError):
        instance._enter_ios_web_context(instance.driver)
    assert "WEBVIEW_100.1" in instance.entered


def test_ios_web_context_enters_the_visible_app_page_that_holds_the_element():
    pages = [{"id": "WEBVIEW_100.1", "bundleId": "process-App"}, {"id": "WEBVIEW_100.2", "bundleId": "process-App"}]
    instance = _ios_instance(pages, visible={"WEBVIEW_100.1", "WEBVIEW_100.2"})
    instance.driver.find_elements.side_effect = (
        lambda by, value: [MagicMock()] if (instance.entered[-1], value) == ("WEBVIEW_100.2", "#email") else []
    )

    instance._enter_ios_web_context(instance.driver, [("css selector", "#email")])
    assert instance.entered[-1] == "WEBVIEW_100.2"

    instance._ios_web_context = None
    instance._enter_ios_web_context(instance.driver, [("css selector", "#nowhere")])
    # No page holds it: the first visible page answers, so the lookup reports the miss.
    assert instance.entered[-1] == "WEBVIEW_100.1"


def test_web_rects_map_css_pixels_through_the_probe():
    instance = _driver_instance({"platformName": "iOS"})
    webdriver = instance.driver
    webdriver.current_context = "WEBVIEW_100.1"
    # Safari draws the page under a 62 pt toolbar; the probe covers the 402 x 714 visual viewport.
    webdriver.execute_script.return_value = [0, 0, 402, 714]
    probe = MagicMock()
    probe.rect = {"x": 0, "y": 62, "width": 402, "height": 714}
    webdriver.find_elements.return_value = [probe]

    rects = instance.web_rects_to_screen([{"x": 24, "y": 24, "width": 354, "height": 38}, None])

    assert rects == [{"x": 24, "y": 86, "width": 354, "height": 38}, None]
    assert webdriver.switch_to.context.call_args_list[-1].args == ("WEBVIEW_100.1",)


@pytest.mark.parametrize(
    ("capabilities", "fast_contexts"),
    [
        ({"platformName": "iOS"}, True),
        ({"platformName": "iOS", "browserName": "Safari"}, False),
        ({"platformName": "Android"}, False),
    ],
)
def test_ios_context_options_apply_to_native_sessions_only(capabilities, fast_contexts):
    instance = _driver_instance(capabilities)
    instance.appium_server_url = "http://127.0.0.1:4723"
    _, defaults = instance._get_platform_and_options(capabilities)

    assert (defaults.get("webviewAtomWaitTimeout") == 5000) is fast_contexts
