from contextlib import contextmanager
from unittest.mock import DEFAULT, MagicMock

import pytest
from appium.common.exceptions import NoSuchContextException
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.elementsources import appium_web
from optics_framework.engines.elementsources.appium_web import AppiumWeb

pytestmark = pytest.mark.white_box


class _Driver:
    """Stands in for the Appium driver wrapper: a web context over a fake page."""

    def __init__(self, page: dict, rects=None, context_error=None, text="", reachable=True):
        self.page = page
        self.rects = list(rects or [{"x": 100, "y": 200, "width": 50, "height": 20}])
        self.context_error = context_error
        self.webdriver = MagicMock()
        self.webdriver.find_elements.side_effect = lambda by, query: list(self.page.get((by, query), []))
        self.webdriver.execute_script.side_effect = lambda script, *_args: (
            [text, reachable] if script == appium_web._TEXT_AND_REACH_JS else DEFAULT
        )
        self.entered = 0
        self.opening_errors = []

    def web_context(self):
        if self.context_error is not None:
            raise self.context_error
        if self.opening_errors:
            raise self.opening_errors.pop(0)

        @contextmanager
        def entered():
            self.entered += 1
            yield self.webdriver

        return entered()

    def web_element_screen_rect(self, element):
        return self.rects.pop(0) if len(self.rects) > 1 else self.rects[0]


def _element(displayed=True):
    element = MagicMock()
    element.is_displayed.return_value = displayed
    return element


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(appium_web.time, "sleep", lambda _s: None)


class TestLocate:
    def test_css_returns_the_element_centre(self):
        driver = _Driver({("css selector", "#search"): [_element()]})
        assert AppiumWeb(driver).locate("css=#search") == (125, 210)

    def test_unprefixed_css_and_dom_xpath_are_looked_up(self):
        driver = _Driver({
            ("css selector", "input[name='q']"): [_element()],
            ("xpath", "//button[@type='submit']"): [_element()],
        })
        source = AppiumWeb(driver)
        assert source.locate("input[name='q']") == (125, 210)
        assert source.locate("//button[@type='submit']") == (125, 210)

    def test_index_picks_among_matches(self):
        first, second = _element(), _element()
        driver = _Driver({("css selector", "a"): [first, second]})
        AppiumWeb(driver).locate("css=a", index=1)
        driver.webdriver.execute_script.assert_any_call(appium_web._SCROLL_INTO_VIEW_JS, second)

    def test_index_beyond_the_matches_is_not_found(self):
        source = AppiumWeb(_Driver({("css selector", "a"): [_element()]}))
        with pytest.raises(OpticsError) as err:
            source.locate("css=a", index=3)
        assert err.value.code == Code.E0201

    def test_hidden_element_is_not_acted_on(self):
        driver = _Driver({("css selector", "#hidden"): [_element(displayed=False)]})
        source = AppiumWeb(driver)
        with pytest.raises(OpticsError, match="not displayed"):
            source.locate("css=#hidden")
        driver.webdriver.execute_script.assert_not_called()

    def test_point_carries_the_element_text(self):
        target = _element()
        driver = _Driver({("css selector", "h1"): [target]}, text="Appium")
        point = AppiumWeb(driver).locate("css=h1")
        assert point.text == "Appium"
        driver.webdriver.execute_script.assert_any_call(appium_web._TEXT_AND_REACH_JS, target)

    @pytest.mark.parametrize("reachable", [True, False])
    def test_point_tells_whether_the_element_is_covered(self, reachable):
        driver = _Driver({("css selector", "#buy"): [_element()]}, reachable=reachable)
        assert AppiumWeb(driver).locate("css=#buy").covered is not reachable

    def test_waits_for_the_rect_to_settle(self):
        moving = [{"x": 0, "y": y, "width": 10, "height": 10} for y in (900, 700, 600, 600)]
        driver = _Driver({("css selector", "#footer"): [_element()]}, rects=moving)
        assert AppiumWeb(driver).locate("css=#footer") == (5, 605)

    @pytest.mark.parametrize("locator", [
        "Sign in",
        "logo.png",
        "//android.widget.Button[@text='OK']",
        "//*[@resource-id='com.app:id/ok']",
        "//XCUIElementTypeButton",
        "//*[@clickable='true']",
        "//*[@focused='true']",
    ])
    def test_native_locators_are_left_to_native_sources(self, locator):
        driver = _Driver({})
        assert AppiumWeb(driver).locate(locator) is None
        assert driver.entered == 0

    def test_platform_without_web_contexts_is_skipped(self):
        driver = _Driver({}, context_error=OpticsError(Code.E0105, message="not on iOS"))
        assert AppiumWeb(driver).locate("css=#search") is None

    def test_missing_web_content_is_a_failure(self):
        source = AppiumWeb(_Driver({}, context_error=OpticsError(Code.E0201, message="no web content")))
        with pytest.raises(OpticsError) as err:
            source.locate("css=#search")
        assert err.value.code == Code.E0201


class TestAssertElements:
    def test_any_rule_passes_on_one_match(self):
        driver = _Driver({("css selector", "#a"): [_element()]})
        found, _ = AppiumWeb(driver).assert_elements(["css=#a", "css=#b"], timeout=1, rule="any")
        assert found is True

    def test_all_rule_needs_every_match(self):
        source = AppiumWeb(_Driver({("css selector", "#a"): [_element()]}))
        with pytest.raises(TimeoutError):
            source.assert_elements(["css=#a", "css=#b"], timeout=0, rule="all")

    def test_polls_until_the_element_appears(self):
        driver = _Driver({})
        results = iter([[], [], [_element()]])
        driver.webdriver.find_elements.side_effect = lambda by, query: next(results)
        found, _ = AppiumWeb(driver).assert_elements(["css=#late"], timeout=30)
        assert found is True
        assert driver.entered == 3

    def test_hidden_match_is_present_but_not_visible(self):
        driver = _Driver({("css selector", "#modal"): [_element(displayed=False)]})
        source = AppiumWeb(driver)
        assert source.assert_elements(["css=#modal"], timeout=0)[0] is True
        with pytest.raises(TimeoutError):
            source.assert_elements_visible(["css=#modal"], timeout=0)

    def test_displayed_match_outside_the_viewport_is_not_visible(self):
        driver = _Driver({("css selector", "#footer"): [_element()]})
        driver.webdriver.execute_script.return_value = False
        source = AppiumWeb(driver)
        with pytest.raises(TimeoutError):
            source.assert_elements_visible(["css=#footer"], timeout=0)

    def test_displayed_match_in_the_viewport_is_visible(self):
        driver = _Driver({("css selector", "#search"): [_element()]})
        driver.webdriver.execute_script.return_value = True
        assert AppiumWeb(driver).assert_elements_visible(["css=#search"], timeout=0)[0] is True

    def test_stale_match_is_checked_again(self):
        stale = _element()
        stale.is_displayed.side_effect = StaleElementReferenceException("gone")
        results = iter([[stale], [_element()]])
        driver = _Driver({})
        driver.webdriver.find_elements.side_effect = lambda by, query: next(results)
        driver.webdriver.execute_script.return_value = True
        found, _ = AppiumWeb(driver).assert_elements_visible(["css=#late"], timeout=30)
        assert found is True

    def test_platform_without_web_contexts_fails_without_waiting(self):
        source = AppiumWeb(_Driver({}, context_error=OpticsError(Code.E0105, message="not on iOS")))
        with pytest.raises(TimeoutError):
            source.assert_elements(["css=#a"], timeout=30)

    def test_retries_while_no_web_content_is_on_screen(self):
        source = AppiumWeb(_Driver({}, context_error=OpticsError(Code.E0201, message="no web content")))
        with pytest.raises(TimeoutError):
            source.assert_elements(["css=#a"], timeout=0)

    def test_retries_while_the_browser_is_still_opening(self):
        driver = _Driver({("css selector", "#late"): [_element()]})
        driver.opening_errors = [NoSuchContextException("WEBVIEW_chrome is not there yet")]
        assert AppiumWeb(driver).assert_elements(["css=#late"], timeout=30)[0] is True
        assert driver.entered == 1

    def test_dead_session_is_not_retried(self, monkeypatch):
        def raise_dead(error):
            raise error

        monkeypatch.setattr(appium_web, "raise_if_session_dead", raise_dead)
        source = AppiumWeb(_Driver({}, context_error=WebDriverException("invalid session id")))
        with pytest.raises(WebDriverException, match="invalid session id"):
            source.assert_elements(["css=#a"], timeout=30)

    def test_invalid_rule(self):
        source = AppiumWeb(_Driver({}))
        with pytest.raises(ValueError):
            source.assert_elements(["css=#a"], rule="most")


@pytest.mark.parametrize("locator, expected", [
    ("css=#search", True),
    ("input[name='q']", True),
    ("//button[@type='submit']", True),
    ("Sign in", False),
    ("logo.png", False),
    ("//*[@resource-id='com.app:id/ok']", False),
])
def test_can_locate_only_web_locators(locator, expected):
    assert AppiumWeb(_Driver({})).can_locate(locator) is expected


def test_non_lookup_capabilities_are_declared_unimplemented():
    from optics_framework.common.strategies import LocatorStrategy

    source = AppiumWeb(_Driver({}))
    assert LocatorStrategy._is_method_implemented(source, "locate")
    assert not LocatorStrategy._is_method_implemented(source, "capture")
    assert not LocatorStrategy._is_method_implemented(source, "get_interactive_elements")
    assert not LocatorStrategy._is_method_implemented(source, "get_page_source")
