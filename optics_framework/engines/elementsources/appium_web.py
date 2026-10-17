import time
from collections.abc import Callable
from typing import Any
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
from optics_framework.common import utils
from optics_framework.common.elementsource_interface import ElementSourceInterface, ScreenPoint
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.session_liveness import raise_if_session_dead

_SCROLL_INTO_VIEW_JS = "arguments[0].scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});"
_IN_VIEWPORT_JS = (
    "const r = arguments[0].getBoundingClientRect(); const v = window.visualViewport;"
    "const left = v ? v.offsetLeft : 0; const top = v ? v.offsetTop : 0;"
    "const right = left + (v ? v.width : window.innerWidth);"
    "const bottom = top + (v ? v.height : window.innerHeight);"
    "return r.width > 0 && r.height > 0 && r.bottom > top && r.right > left"
    " && r.top < bottom && r.left < right;"
)
_TEXT_JS = (
    "const e = arguments[0];"
    "return ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName) ? e.value : e.innerText;"
)
_POLL_INTERVAL_S = 0.5
_SETTLE_TIMEOUT_S = 2.0
_SETTLE_INTERVAL_S = 0.15
_NATIVE_XPATH_MARKERS = (
    "android.", "XCUIElementType", "@resource-id", "@content-desc", "@text", "@package", "@bounds",
    "@clickable", "@long-clickable", "@checkable", "@focusable", "@focused", "@scrollable",
    "@displayed", "@enabled", "@password", "@hint", "@visible", "@accessible",
)


class AppiumWeb(ElementSourceInterface):
    """Finds CSS and DOM XPath locators in the web content of the Android app in front.

    Each lookup enters the page served by the foreground app (Chrome, or a debuggable
    WebView), finds the element in the DOM, and returns its centre in device-screen
    coordinates. The session stays in the native context, so the press, typing and
    screenshots that follow are native actions, and the native locators keep working
    between web lookups.
    """

    REQUIRED_DRIVER_TYPE = "appium"

    def __init__(self, driver: Any = None):
        self.driver = driver

    def capture(self):
        raise NotImplementedError("AppiumWeb does not capture screenshots.")

    def get_interactive_elements(self, filter_config: list[str] | None = None, compact: bool = False) -> list:
        raise NotImplementedError("AppiumWeb does not list interactive elements.")

    def can_locate(self, element: str) -> bool:
        return self._dom_query(element) is not None

    def locate(self, element: str, index: int | None = None) -> ScreenPoint | None:
        query = self._dom_query(element)
        if query is None:
            return None
        try:
            with self._web_context() as driver:
                matches = driver.find_elements(*query)
                position = index or 0
                if position >= len(matches):
                    raise OpticsError(
                        Code.E0201,
                        message=f"'{element}' matched {len(matches)} web element(s); index {position} is out of range.",
                    )
                target = matches[position]
                if not target.is_displayed():
                    raise OpticsError(Code.E0201, message=f"'{element}' is in the page but not displayed.")
                driver.execute_script(_SCROLL_INTO_VIEW_JS, target)
                rect = self._settled_rect(target)
                text = driver.execute_script(_TEXT_JS, target)
        except _Unsupported:
            return None
        return ScreenPoint(rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2, text)

    def _settled_rect(self, target: Any) -> dict:
        """Screen rect once it holds still.

        Scrolling makes Chrome slide its toolbar away, which moves the page after the
        scroll itself has finished; a rect read too early sends the tap somewhere else.
        """
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
        return self._poll(elements, timeout, rule, self._is_present)

    def assert_elements_visible(self, elements: Any, timeout: int = 30, rule: str = "any") -> tuple[bool, str]:
        return self._poll(elements, timeout, rule, self._is_visible)

    def _poll(
        self, elements: Any, timeout: int, rule: str, check: Callable[[Any, tuple[str, str]], bool]
    ) -> tuple[bool, str]:
        if rule not in ("any", "all"):
            raise ValueError("Invalid rule. Use 'any' or 'all'.")
        queries = [self._dom_query(element) for element in elements]
        deadline = time.monotonic() + float(timeout)
        while True:
            try:
                if self._check_once(queries, rule, check):
                    return True, utils.get_timestamp()
            except _Unsupported:
                raise TimeoutError(f"Web elements cannot be checked on this platform: {elements}") from None
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Web elements not found based on rule '{rule}': {elements}")
            time.sleep(_POLL_INTERVAL_S)

    def _check_once(
        self, queries: list[tuple[str, str] | None], rule: str, check: Callable[[Any, tuple[str, str]], bool]
    ) -> bool:
        """One look at the page in front; a page that cannot be read yet counts as no match."""
        try:
            with self._web_context() as driver:
                found = [query is not None and check(driver, query) for query in queries]
        except StaleElementReferenceException:
            internal_logger.debug("AppiumWeb: the page changed while checking; checking again.")
            return False
        except OpticsError as e:
            internal_logger.debug(f"AppiumWeb: no web content to check yet: {e}")
            return False
        except WebDriverException as e:
            # A browser still opening a tab or registering its web view fails this way.
            raise_if_session_dead(e)
            internal_logger.debug(f"AppiumWeb: the page could not be read yet: {e.msg}")
            return False
        return all(found) if rule == "all" else any(found)

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
            selector = element[len("css="):] if element.lower().startswith("css=") else element
            return AppiumBy.CSS_SELECTOR, selector
        if element_type == "XPath":
            xpath = element[len("xpath="):] if element.lower().startswith("xpath=") else element
            if any(marker in xpath for marker in _NATIVE_XPATH_MARKERS):
                return None
            return AppiumBy.XPATH, xpath
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


class _Unsupported(Exception):
    """The device platform has no web context support; the lookup is skipped, not failed."""
