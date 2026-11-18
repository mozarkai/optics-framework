from unittest.mock import MagicMock, patch

import pytest
from appium.common.exceptions import NoSuchContextException
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


def test_ios_web_context_tries_a_safari_sheet_first():
    instance = _ios_instance(
        [{"id": "WEBVIEW_100.1", "bundleId": "process-App"},
         {"id": "WEBVIEW_300.1", "bundleId": "com.apple.SafariViewService"}],
        visible={"WEBVIEW_100.1", "WEBVIEW_300.1"},
    )

    instance._enter_ios_web_context(instance.driver)

    assert instance.entered == ["WEBVIEW_300.1"]


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


def test_a_web_context_that_is_gone_reports_the_page_as_unreadable():
    instance = _driver_instance({"platformName": "Android"})
    webdriver = instance.driver
    webdriver.current_package = "com.example.app"
    webdriver.contexts = [Appium.NATIVE_CONTEXT]
    instance._known_web_contexts = {}

    def switch(context):
        if context == "WEBVIEW_com.example.app":
            raise NoSuchContextException("no such context")

    webdriver.switch_to.context.side_effect = switch

    with patch.object(instance, "_foreground_web_context", return_value=("WEBVIEW_com.example.app", None, 0)), \
            pytest.raises(OpticsError) as err, instance.web_context():
        pass

    assert err.value.code == Code.E0201
