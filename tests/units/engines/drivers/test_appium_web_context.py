from unittest.mock import MagicMock

import pytest
from selenium.common.exceptions import WebDriverException

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
        ({"platformName": "LGTV"}, None),
        ({"platformName": "Android", "browserName": "chrome"}, None),
        ({"platformName": "Android", "appium:autoWebview": True}, None),
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
    instance._app_webviews_in_front = 0
    webdriver.window_handles = ["tab"]

    def switch(context):
        if context == Appium.NATIVE_CONTEXT:
            raise WebDriverException("context switch failed")

    webdriver.switch_to.context.side_effect = switch

    with pytest.raises(OpticsError) as excinfo, instance.web_context():
        pass

    assert excinfo.value.code == Code.E0106
