from unittest.mock import MagicMock, call

import pytest
from appium.common.exceptions import NoSuchContextException
from selenium.common.exceptions import UnknownMethodException, WebDriverException

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.drivers.appium import Appium

pytestmark = pytest.mark.white_box

CHROME_PAGES = [
    {
        "webview": "WEBVIEW_Terrace",
        "info": {"Android-Package": "com.sec.android.app.launcher"},
        "pages": [{"url": "https://launcher.example/"}],
    },
    {
        "webview": "WEBVIEW_chrome",
        "info": {"Android-Package": "com.android.chrome"},
        "pages": [{"url": "https://example.com/front"}, {"url": "https://example.com/back"}],
    },
]


def _appium(webdriver, platform="Android") -> Appium:
    instance = Appium.__new__(Appium)
    instance.driver = webdriver
    instance.capabilities = {"platformName": platform}
    instance.event_sdk = MagicMock()
    instance._viewport_rect_supported = True
    instance._browser_package = None
    if webdriver is not None:
        webdriver.capabilities = {"platformName": platform}
    return instance


def _webdriver(package="com.android.chrome", contexts=CHROME_PAGES) -> MagicMock:
    webdriver = MagicMock()
    webdriver.current_package = package
    webdriver.execute_script.side_effect = lambda script, *args: (
        contexts if script == "mobile: getContexts" else None
    )
    webdriver.window_handles = ["only"]
    return webdriver


def _with_tabs(webdriver, urls_by_handle, attached=None, attached_state="hidden"):
    webdriver.window_handles = list(urls_by_handle)
    webdriver.current_window_handle = attached
    webdriver.execute_script.side_effect = lambda script, *args: (
        CHROME_PAGES if script == "mobile: getContexts" else attached_state
    )
    targets = [{"targetId": handle, "type": "page", "url": url} for handle, url in urls_by_handle.items()]
    webdriver.execute.return_value = {"value": {"targetInfos": targets}}


def _enter(context, error=None):
    with context:
        if error is not None:
            raise error


class TestForegroundWebContext:
    def test_picks_the_web_view_of_the_app_in_front(self):
        appium = _appium(_webdriver())
        assert appium._foreground_web_context(appium.driver) == ("WEBVIEW_chrome", "https://example.com/front")

    def test_matches_the_package_named_context_when_the_owner_is_unreported(self):
        contexts = [{"webview": "WEBVIEW_com.shop.app", "pages": [{"url": "https://shop/"}]}]
        appium = _appium(_webdriver(package="com.shop.app", contexts=contexts))
        assert appium._foreground_web_context(appium.driver) == ("WEBVIEW_com.shop.app", "https://shop/")

    def test_skips_a_web_view_without_pages(self):
        contexts = [{"webview": "WEBVIEW_chrome", "info": {"Android-Package": "com.android.chrome"}, "pages": []}]
        appium = _appium(_webdriver(contexts=contexts))
        with pytest.raises(OpticsError) as err:
            appium._foreground_web_context(appium.driver)
        assert err.value.code == Code.E0201

    def test_native_app_in_front_has_no_web_content(self):
        appium = _appium(_webdriver(package="com.android.vending"))
        with pytest.raises(OpticsError) as err:
            appium._foreground_web_context(appium.driver)
        assert err.value.code == Code.E0201
        assert "com.android.vending" in str(err.value)


class TestWebContext:
    def test_enters_the_web_view_and_always_returns_to_native(self):
        webdriver = _webdriver()
        context = _appium(webdriver).web_context()
        error = RuntimeError("lookup failed")

        with pytest.raises(RuntimeError):
            _enter(context, error)

        assert webdriver.switch_to.context.call_args_list == [call("WEBVIEW_chrome"), call("NATIVE_APP")]

    def test_yields_the_webdriver(self):
        webdriver = _webdriver()
        with _appium(webdriver).web_context() as driver:
            assert driver is webdriver

    def test_focuses_the_window_showing_the_front_page_without_visiting_others(self):
        webdriver = _webdriver()
        _with_tabs(webdriver, {"back": "https://example.com/back", "front": "https://example.com/front"})

        with _appium(webdriver).web_context():
            pass

        webdriver.switch_to.window.assert_called_once_with("front")
        webdriver.execute.assert_called_once_with(
            "opticsExecuteCdp", {"cmd": "Target.getTargets", "params": {}}
        )

    def test_single_window_needs_no_focus(self):
        webdriver = _webdriver()

        with _appium(webdriver).web_context():
            pass

        webdriver.execute.assert_not_called()
        webdriver.switch_to.window.assert_not_called()

    @pytest.mark.parametrize("urls", [
        {"a": "https://example.com/back", "b": "https://example.com/other"},
        {"a": "https://example.com/front", "b": "https://example.com/front"},
    ], ids=["no-match", "duplicate"])
    def test_front_page_that_no_single_window_shows_fails(self, urls):
        webdriver = _webdriver()
        _with_tabs(webdriver, urls)
        context = _appium(webdriver).web_context()

        with pytest.raises(OpticsError) as err:
            _enter(context)
        assert err.value.code == Code.E0201
        webdriver.switch_to.window.assert_not_called()
        assert webdriver.switch_to.context.call_args_list[-1] == call("NATIVE_APP")

    def test_hidden_attached_tab_leaves_the_other_same_url_tab(self):
        webdriver = _webdriver()
        _with_tabs(webdriver, {"old": "https://example.com/front", "new": "https://example.com/front"}, attached="old")

        with _appium(webdriver).web_context():
            pass

        webdriver.switch_to.window.assert_called_once_with("new")

    def test_visible_attached_tab_is_the_one_on_screen(self):
        webdriver = _webdriver()
        _with_tabs(
            webdriver, {"a": "https://example.com/front", "b": "https://example.com/front"},
            attached="b", attached_state="visible",
        )

        with _appium(webdriver).web_context():
            pass

        webdriver.switch_to.window.assert_not_called()

    def test_three_same_url_tabs_stay_ambiguous(self):
        webdriver = _webdriver()
        same = "https://example.com/front"
        _with_tabs(webdriver, {"a": same, "b": same, "c": same}, attached="a")
        context = _appium(webdriver).web_context()

        with pytest.raises(OpticsError, match="several tabs show"):
            _enter(context)
        webdriver.switch_to.window.assert_not_called()

    def test_web_content_chromedriver_cannot_attach_to_is_not_found(self):
        webdriver = _webdriver(package="com.sec.android.app.sbrowser", contexts=[{
            "webview": "WEBVIEW_Terrace",
            "info": {"Android-Package": "com.sec.android.app.sbrowser"},
            "pages": [{"url": "https://example.com/"}],
        }])
        webdriver.switch_to.context.side_effect = [
            WebDriverException("session not created\nFailed to get sockets matching: @webview_devtools_remote_.*6541"),
            None,
        ]
        context = _appium(webdriver).web_context()

        with pytest.raises(OpticsError) as err:
            _enter(context)
        assert err.value.code == Code.E0201
        assert "com.sec.android.app.sbrowser" in str(err.value)
        assert "Failed to get sockets" not in str(err.value)
        assert webdriver.switch_to.context.call_args_list[-1] == call("NATIVE_APP")

    def test_browser_session_uses_its_fixed_context_name(self):
        webdriver = _webdriver()
        webdriver.switch_to.context.side_effect = [NoSuchContextException("no such context"), None, None]
        webdriver.contexts = ["NATIVE_APP", "CHROMIUM"]

        with _appium(webdriver).web_context():
            pass

        assert webdriver.switch_to.context.call_args_list[1] == call("CHROMIUM")

    def test_unknown_context_without_a_browser_session_fails(self):
        webdriver = _webdriver()
        webdriver.switch_to.context.side_effect = [NoSuchContextException("no such context"), None]
        webdriver.contexts = ["NATIVE_APP"]
        context = _appium(webdriver).web_context()

        with pytest.raises(NoSuchContextException):
            _enter(context)
        assert webdriver.switch_to.context.call_args_list[-1] == call("NATIVE_APP")

    def test_ios_is_not_supported(self):
        appium = _appium(_webdriver(), platform="iOS")
        with pytest.raises(OpticsError) as err:
            appium.web_context()
        assert err.value.code == Code.E0105


class TestWebElementScreenRect:
    def test_uses_the_driver_translation(self):
        webdriver = MagicMock()
        webdriver.execute_script.return_value = {"x": 30, "y": 544, "width": 852, "height": 132}
        element = MagicMock(id="el-1")

        rect = _appium(webdriver).web_element_screen_rect(element)

        assert rect == {"x": 30, "y": 544, "width": 852, "height": 132}
        webdriver.execute_script.assert_called_once_with("mobile: viewportElementRect", {"elementId": "el-1"})

    def test_older_driver_computes_the_rect_from_page_geometry(self):
        # Values measured on a OnePlus Nord CE; viewportElementRect returned the same rect.
        metrics = [292.557, 581.667, 39.349, 35, 3, 0, 0, 1]
        webdriver = MagicMock()
        webdriver.execute_script.side_effect = [UnknownMethodException("Unsupported execute method"), metrics]
        webdriver.current_context = "WEBVIEW_chrome"
        webdriver.current_window_handle = "front"
        webdriver.find_elements.return_value = [
            MagicMock(rect={"x": 0, "y": 0, "width": 0, "height": 0}),
            MagicMock(rect={"x": 0, "y": 283, "width": 1080, "height": 1985}),
        ]
        appium = _appium(webdriver)

        rect = appium.web_element_screen_rect(MagicMock(id="el-1"))

        assert rect == {"x": 878, "y": 2028, "width": 118, "height": 105}
        assert appium._viewport_rect_supported is False
        assert webdriver.switch_to.context.call_args_list == [call("NATIVE_APP"), call("WEBVIEW_chrome")]
        webdriver.switch_to.window.assert_called_once_with("front")

    def test_remembers_that_the_translation_is_unavailable(self):
        webdriver = MagicMock()
        webdriver.execute_script.return_value = [10, 20, 30, 40, 2, 0, 0, 1]
        webdriver.find_elements.return_value = [MagicMock(rect={"x": 0, "y": 100, "width": 720, "height": 1200})]
        appium = _appium(webdriver)
        appium._viewport_rect_supported = False

        assert appium.web_element_screen_rect(MagicMock()) == {"x": 20, "y": 140, "width": 60, "height": 80}
        assert all(c.args[0] != "mobile: viewportElementRect" for c in webdriver.execute_script.call_args_list)

    def test_fallback_without_a_native_web_view_fails(self):
        webdriver = MagicMock()
        webdriver.execute_script.return_value = [0, 0, 1, 1, 1, 0, 0, 1]
        webdriver.find_elements.return_value = []
        appium = _appium(webdriver)
        appium._viewport_rect_supported = False

        element = MagicMock()
        with pytest.raises(OpticsError) as err:
            appium.web_element_screen_rect(element)
        assert err.value.code == Code.E0201

    def test_fallback_with_several_native_web_views_fails(self):
        webdriver = MagicMock()
        webdriver.execute_script.return_value = [0, 0, 1, 1, 1, 0, 0, 1]
        webdriver.find_elements.return_value = [
            MagicMock(rect={"x": 0, "y": 200, "width": 1080, "height": 600}),
            MagicMock(rect={"x": 0, "y": 900, "width": 1080, "height": 1200}),
        ]
        appium = _appium(webdriver)
        appium._viewport_rect_supported = False

        element = MagicMock()
        with pytest.raises(OpticsError, match="2 native WebViews") as err:
            appium.web_element_screen_rect(element)
        assert err.value.code == Code.E0201


class TestParkInNativeContext:
    def test_browser_session_moves_to_native(self):
        webdriver = MagicMock(current_context="CHROMIUM")
        _appium(webdriver)._park_in_native_context({"browserName": "Chrome"})
        webdriver.switch_to.context.assert_called_once_with("NATIVE_APP")

    def test_app_session_is_not_queried(self):
        webdriver = MagicMock()
        _appium(webdriver)._park_in_native_context({"platformName": "Android", "appium:appPackage": "a.b"})
        webdriver.switch_to.context.assert_not_called()

    def test_ios_session_keeps_its_web_context(self):
        webdriver = MagicMock(current_context="WEBVIEW_1")
        _appium(webdriver, platform="iOS")._park_in_native_context({"browserName": "Safari"})
        webdriver.switch_to.context.assert_not_called()

    def test_session_already_native_stays(self):
        webdriver = MagicMock(current_context="NATIVE_APP")
        _appium(webdriver)._park_in_native_context({"appium:autoWebview": True})
        webdriver.switch_to.context.assert_not_called()


class TestLaunchAppWithUrl:
    def test_opens_the_url_after_starting_a_session(self):
        appium = _appium(None)

        def start_session(**kwargs):
            appium.driver = MagicMock(session_id="new")
            return "new"

        appium.start_session = MagicMock(side_effect=start_session)
        appium._open_deeplink = MagicMock()

        assert appium.launch_app("https://example.com/") == "new"
        appium.start_session.assert_called_once_with(event_name=None)
        appium._open_deeplink.assert_called_once_with(appium.driver, "https://example.com/")

    def test_live_session_opens_the_url_without_a_new_session(self):
        webdriver = MagicMock(session_id="live")
        appium = _appium(webdriver)
        appium.start_session = MagicMock()
        appium._open_deeplink = MagicMock()

        assert appium.launch_app("myapp://orders/42") == "live"
        appium.start_session.assert_not_called()
        appium._open_deeplink.assert_called_once_with(webdriver, "myapp://orders/42")


class TestTapAtCoordinates:
    def test_failed_tap_raises(self):
        webdriver = MagicMock()
        webdriver.tap.side_effect = WebDriverException("tap failed")
        appium = _appium(webdriver)
        with pytest.raises(OpticsError) as err:
            appium.tap_at_coordinates(10, 20)
        assert err.value.code == Code.E0401

    def test_tap_records_the_event(self):
        webdriver = MagicMock()
        appium = _appium(webdriver)
        appium.tap_at_coordinates(10, 20, event_name="tap")
        webdriver.tap.assert_called_once_with([(10, 20)], 100)
        appium.event_sdk.capture_event_with_time_input.assert_called_once()


class TestBrowserSessionOpensLinksInItsBrowser:
    def test_web_link_targets_the_session_browser(self):
        webdriver = MagicMock()
        appium = _appium(webdriver)
        appium._browser_package = "com.android.chrome"
        appium._open_android_deeplink(webdriver, "https://example.com/")
        webdriver.execute_script.assert_called_once_with(
            "mobile: deepLink", {"url": "https://example.com/", "package": "com.android.chrome"}
        )

    def test_app_link_keeps_the_default_handler(self):
        webdriver = MagicMock()
        appium = _appium(webdriver)
        appium._browser_package = "com.android.chrome"
        appium._open_android_deeplink(webdriver, "myapp://orders/42")
        webdriver.execute_script.assert_called_once_with("mobile: deepLink", {"url": "myapp://orders/42"})

    def test_failed_deep_link_raises_without_a_local_fallback(self, monkeypatch):
        webdriver = MagicMock()
        webdriver.execute_script.side_effect = WebDriverException("am start timed out")
        run = MagicMock()
        monkeypatch.setattr("optics_framework.engines.drivers.appium.subprocess.run", run)
        appium = _appium(webdriver)
        with pytest.raises(OpticsError) as err:
            appium._open_android_deeplink(webdriver, "https://example.com/")
        assert err.value.code == Code.E0401
        run.assert_not_called()

    def test_app_session_uses_the_default_handler(self):
        webdriver = MagicMock()
        _appium(webdriver)._open_android_deeplink(webdriver, "https://example.com/")
        webdriver.execute_script.assert_called_once_with("mobile: deepLink", {"url": "https://example.com/"})
