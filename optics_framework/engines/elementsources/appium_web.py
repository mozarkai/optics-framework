import time
from collections.abc import Callable
from typing import Any
from appium.webdriver.common.appiumby import AppiumBy
from lxml import etree  # type: ignore
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
from optics_framework.common import utils
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.session_liveness import raise_if_session_dead
from optics_framework.engines.drivers.appium_platforms import ANDROID
from optics_framework.engines.elementsources.playwright_page_source import BOUNDS_JS, SUGGEST_LOCATORS_JS, WebDomInspector

_SCROLL_INTO_VIEW_JS = "arguments[0].scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});"
_IN_VIEWPORT_JS = (
    "const r = arguments[0].getBoundingClientRect(); const v = window.visualViewport;"
    "const left = v ? v.offsetLeft : 0; const top = v ? v.offsetTop : 0;"
    "const right = left + (v ? v.width : window.innerWidth);"
    "const bottom = top + (v ? v.height : window.innerHeight);"
    "return r.width > 0 && r.height > 0 && r.bottom > top && r.right > left"
    " && r.top < bottom && r.left < right;"
)
# One round trip: the page geometry web_rects_to_screen needs, the rects, and the locators.
_INSPECT_JS = (
    "const v = window.visualViewport;"
    "return [[window.devicePixelRatio, v ? v.offsetLeft : 0, v ? v.offsetTop : 0, v ? v.scale : 1],"
    f" ({BOUNDS_JS})(arguments[0]),"
    f" arguments[1] ? ({SUGGEST_LOCATORS_JS})(arguments[1]) : null];"
)
_SETTLE_TIMEOUT_S = 2.0
_SETTLE_INTERVAL_S = 0.15


class AppiumWeb(ElementSourceInterface, WebDomInspector):
    """Finds CSS and DOM XPath locators in the foreground Android app's web content (Chrome or a debuggable WebView).

    It returns screen coordinates, so the session and the actions that follow stay native.
    """

    REQUIRED_DRIVER_TYPE = "appium"
    SUPPLEMENTS_SCREEN_ELEMENTS = True

    def __init__(self, driver: Any = None):
        self.driver = driver
        self._page_driver: Any = None

    def capture(self):
        raise NotImplementedError("AppiumWeb does not capture screenshots.")

    def get_interactive_elements(self, filter_config: list[str] | None = None, compact: bool = False) -> list:
        """The whole web page the app in front shows, in device-screen pixels; out-of-view elements are ``offscreen``."""
        if not self._reads_native_android():
            return []
        try:
            with self._web_context() as driver:
                self._page_driver = driver
                try:
                    self.tree = etree.HTML(driver.page_source)
                    return self._web_interactive_elements(filter_config, utils.to_bool(compact))
                finally:
                    self._page_driver = None
        except _Unsupported:
            return []

    def _inspect_nodes(
        self, xpaths: list[str | None], locate: list[str | None]
    ) -> tuple[list[dict[str, Any] | None] | None, list[str | None]]:
        if not xpaths:
            return [], []
        try:
            metrics, rects, found = self._page_driver.execute_script(
                _INSPECT_JS, xpaths, locate if any(locate) else None
            )
        except WebDriverException as e:
            raise_if_session_dead(e)
            internal_logger.debug(f"AppiumWeb: the page could not be measured: {e.msg}")
            return None, [None] * len(xpaths)
        if not isinstance(rects, list) or len(rects) != len(xpaths):
            internal_logger.debug("AppiumWeb: measuring the page returned a malformed payload.")
            return None, [None] * len(xpaths)
        return self.driver.web_rects_to_screen(rects, metrics), found or [None] * len(xpaths)

    def can_locate(self, element: str) -> bool:
        return self._dom_query(element) is not None and self._reads_native_android()

    def _reads_native_android(self) -> bool:
        # A browser or autoWebview session already reads the page through the native sources; leaving it
        # through web_context would drop it to NATIVE_APP.
        return self.driver is not None and self.driver.native_ui_platform() == ANDROID

    def locate(self, element: str, index: int | None = None) -> tuple[int, int] | None:
        if not self.can_locate(element):
            return None
        query = self._dom_query(element)
        try:
            with self._web_context() as driver:
                matches = driver.find_elements(*query)
                position = index or 0
                if not 0 <= position < len(matches):
                    raise OpticsError(
                        Code.E0201,
                        message=f"'{element}' matched {len(matches)} web element(s); index {position} is out of range.",
                    )
                target = matches[position]
                if not target.is_displayed():
                    raise OpticsError(Code.E0201, message=f"'{element}' is in the page but not displayed.")
                driver.execute_script(_SCROLL_INTO_VIEW_JS, target)
                rect = self._settled_rect(target)
        except _Unsupported:
            return None
        return rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2

    def _settled_rect(self, target: Any) -> dict:
        """Screen rect once it holds still: Chrome slides its toolbar away after a scroll ends."""
        rect = self.driver.web_element_screen_rect(target)
        deadline = time.monotonic() + _SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            time.sleep(_SETTLE_INTERVAL_S)
            current = self.driver.web_element_screen_rect(target)
            if current == rect:
                return current
            rect = current
        return rect

    def assert_elements(self, elements: Any, timeout: int = 30, rule: str = "any") -> tuple[bool, str]:
        return self._assert_once(elements, rule, visible=False)

    def assert_elements_visible(self, elements: Any, timeout: int = 30, rule: str = "any") -> tuple[bool, str]:
        return self._assert_once(elements, rule, visible=True)

    def _assert_once(self, elements: Any, rule: str, visible: bool) -> tuple[bool, str]:
        """One look; the strategy layer owns waiting."""
        if rule not in ("any", "all"):
            raise ValueError("Invalid rule. Use 'any' or 'all'.")
        try:
            found = self.check_elements(elements, visible)
        except _Unsupported:
            raise TimeoutError(f"Web elements cannot be checked on this platform: {elements}") from None
        if not (all if rule == "all" else any)(found):
            raise TimeoutError(f"Web elements not found based on rule '{rule}': {elements}")
        return True, utils.get_timestamp()

    def check_elements(self, elements: list[str], visible: bool = False) -> list[bool]:
        queries = [self._dom_query(element) for element in elements]
        return self._check_once(queries, self._is_visible if visible else self._is_present)

    def _check_once(
        self, queries: list[tuple[str, str] | None], check: Callable[[Any, tuple[str, str]], bool]
    ) -> list[bool]:
        """One look at the page in front; a page that cannot be read yet counts as no match."""
        try:
            with self._web_context() as driver:
                return [query is not None and check(driver, query) for query in queries]
        except StaleElementReferenceException:
            internal_logger.debug("AppiumWeb: the page changed while checking; checking again.")
        except OpticsError as e:
            raise_if_session_dead(e)
            internal_logger.debug(f"AppiumWeb: no web content to check yet: {e}")
        except WebDriverException as e:
            # A browser still opening a tab or registering its web view fails this way.
            raise_if_session_dead(e)
            internal_logger.debug(f"AppiumWeb: the page could not be read yet: {e.msg}")
        return [False] * len(queries)

    @staticmethod
    def _is_present(driver: Any, query: tuple[str, str]) -> bool:
        return bool(driver.find_elements(*query))

    @staticmethod
    def _is_visible(driver: Any, query: tuple[str, str]) -> bool:
        return any(
            match.is_displayed() and driver.execute_script(_IN_VIEWPORT_JS, match)
            for match in driver.find_elements(*query)
        )

    @staticmethod
    def _dom_query(element: str) -> tuple[str, str] | None:
        element_type = utils.determine_element_type(element)
        if element_type == "CSS":
            return AppiumBy.CSS_SELECTOR, _without_prefix(element, "css=")
        if element_type == "XPath":
            xpath = _without_prefix(element, "xpath=")
            return None if utils.is_native_xpath(xpath) else (AppiumBy.XPATH, xpath)
        return None

    def _web_context(self):
        if self.driver is None:
            raise OpticsError(Code.E0101, message="Appium driver is not initialized for AppiumWeb.")
        try:
            return self.driver.web_context()
        except OpticsError as e:
            if e.code == Code.E0105:
                internal_logger.debug(f"AppiumWeb: {e}")
                raise _Unsupported from e
            raise


def _without_prefix(element: str, prefix: str) -> str:
    return element[len(prefix):] if element.lower().startswith(prefix) else element


class _Unsupported(Exception):
    """The device platform has no web context support; the lookup is skipped, not failed."""
