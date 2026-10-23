import time
from typing import Any
from appium.webdriver.common.appiumby import AppiumBy
from lxml import etree  # type: ignore
from selenium.common.exceptions import WebDriverException
from optics_framework.common import utils
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.session_liveness import raise_if_session_dead
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
_CENTER_IN_VIEWPORT_JS = (
    "const r = arguments[0].getBoundingClientRect(); const v = window.visualViewport;"
    "if (!v) return true; const x = r.left + r.width / 2; const y = r.top + r.height / 2;"
    "return x >= v.offsetLeft && x <= v.offsetLeft + v.width && y >= v.offsetTop && y <= v.offsetTop + v.height;"
)
_CLOSE_KEYBOARD_JS = "document.activeElement && document.activeElement.blur();"
# iOS can raise the keyboard a second after typing ends, scrolling the page; until then the viewport is near full height.
_KEYBOARD_PENDING_JS = (
    "const a = document.activeElement, v = window.visualViewport;"
    "return !!v && !!a && a !== arguments[0] && v.height > innerHeight * 0.8 && a.matches('textarea,"
    " [contenteditable]:not([contenteditable=false]), input:not([type=checkbox], [type=radio], [type=button],"
    " [type=submit], [type=reset], [type=file], [type=range], [type=color], [type=image], [type=hidden])');"
)
# One round trip: the rects and the locators.
_INSPECT_JS = (
    f"return [({BOUNDS_JS})(arguments[0]),"
    f" arguments[1] ? ({SUGGEST_LOCATORS_JS})(arguments[1]) : null];"
)
_SETTLE_TIMEOUT_S = 2.0
_SETTLE_INTERVAL_S = 0.15


class AppiumWeb(ElementSourceInterface, WebDomInspector):
    """Finds CSS and DOM XPath locators in the foreground browser or WebView and returns screen coordinates, so actions stay native."""

    REQUIRED_DRIVER_TYPE = "appium"
    SUPPLEMENTS_SCREEN_ELEMENTS = True

    def __init__(self, driver: Any = None):
        self.driver = driver

    def capture(self):
        raise NotImplementedError("AppiumWeb does not capture screenshots.")

    def get_interactive_elements(self, filter_config: list[str] | None = None, compact: bool = False) -> list:
        """The whole web page the app in front shows, in screen coordinates; out-of-view elements are ``offscreen``."""
        if not self._reads_native_screen():
            return []
        with self.driver.web_context() as driver:
            self.tree = etree.HTML(driver.page_source)
            return self._web_interactive_elements(filter_config, compact)

    def _inspect_nodes(
        self, xpaths: list[str | None], locate: list[str | None]
    ) -> tuple[list[dict[str, Any] | None] | None, list[str | None]]:
        if not xpaths:
            return [], []
        try:
            rects, found = self.driver.driver.execute_script(
                _INSPECT_JS, xpaths, locate if any(locate) else None
            )
        except WebDriverException as e:
            raise_if_session_dead(e)
            internal_logger.debug(f"AppiumWeb: the page could not be measured: {e.msg}")
            return None, [None] * len(xpaths)
        if not isinstance(rects, list) or len(rects) != len(xpaths):
            internal_logger.debug("AppiumWeb: measuring the page returned a malformed payload.")
            return None, [None] * len(xpaths)
        return self.driver.web_rects_to_screen(rects), found or [None] * len(xpaths)

    def can_locate(self, element: str) -> bool:
        return self._dom_query(element) is not None and self._reads_native_screen()

    def _reads_native_screen(self) -> bool:
        # A browser or autoWebview session already reads the page through the native sources; leaving it
        # through web_context would drop it to NATIVE_APP.
        return self.driver is not None and self.driver.native_ui_platform() is not None

    def locate(self, element: str, index: int | None = None) -> tuple[int, int] | None:
        if not self.can_locate(element):
            return None
        query = self._dom_query(element)
        with self.driver.web_context([query]) as driver:
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
            if self.driver.native_ui_platform() == "ios":
                self._await_keyboard(driver, target)
            driver.execute_script(_SCROLL_INTO_VIEW_JS, target)
            self._uncover(driver, target)
            rect = self._settled_rect(target)
        return rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2

    @staticmethod
    def _await_keyboard(driver: Any, target: Any) -> None:
        deadline = time.monotonic() + _SETTLE_TIMEOUT_S
        while time.monotonic() < deadline and driver.execute_script(_KEYBOARD_PENDING_JS, target):
            time.sleep(_SETTLE_INTERVAL_S)

    @staticmethod
    def _uncover(driver: Any, target: Any) -> None:
        """Close the on-screen keyboard when it covers ``target`` at the end of the page, where scrolling cannot."""
        if driver.execute_script(_CENTER_IN_VIEWPORT_JS, target):
            return
        driver.execute_script(_CLOSE_KEYBOARD_JS)
        deadline = time.monotonic() + _SETTLE_TIMEOUT_S
        while time.monotonic() < deadline and not driver.execute_script(_CENTER_IN_VIEWPORT_JS, target):
            time.sleep(_SETTLE_INTERVAL_S)
            driver.execute_script(_SCROLL_INTO_VIEW_JS, target)

    def _settled_rect(self, target: Any) -> dict:
        """Screen rect once it holds still: Chrome slides its toolbar away after a scroll ends."""
        previous, rect = None, self.driver.web_element_screen_rect(target)
        deadline = time.monotonic() + _SETTLE_TIMEOUT_S
        while rect != previous and time.monotonic() < deadline:
            time.sleep(_SETTLE_INTERVAL_S)
            previous, rect = rect, self.driver.web_element_screen_rect(target)
        return rect

    def assert_elements(self, elements: Any, timeout: int = 30, rule: str = "any") -> tuple[bool, str]:
        return self._assert_once(elements, rule, visible=False)

    def assert_elements_visible(self, elements: Any, timeout: int = 30, rule: str = "any") -> tuple[bool, str]:
        return self._assert_once(elements, rule, visible=True)

    def _assert_once(self, elements: Any, rule: str, visible: bool) -> tuple[bool, str]:
        """One look; the strategy layer owns waiting."""
        if rule not in ("any", "all"):
            raise ValueError("Invalid rule. Use 'any' or 'all'.")
        if not (all if rule == "all" else any)(self.check_elements(elements, visible)):
            raise TimeoutError(f"Web elements not found based on rule '{rule}': {elements}")
        return True, utils.get_timestamp()

    def check_elements(self, elements: list[str], visible: bool = False) -> list[bool]:
        """One look at the page in front; a page that cannot be read yet counts as no match."""
        queries = [self._dom_query(element) for element in elements]
        if not self._reads_native_screen():
            return [False] * len(queries)
        check = self._is_visible if visible else self._is_present
        try:
            with self.driver.web_context([query for query in queries if query is not None]) as driver:
                return [query is not None and check(driver, query) for query in queries]
        except OpticsError as e:
            raise_if_session_dead(e)
            internal_logger.debug(f"AppiumWeb: no web content to check yet: {e}")
        except WebDriverException as e:
            # A browser still opening a tab, registering its web view or changing the page mid-check fails this way.
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


def _without_prefix(element: str, prefix: str) -> str:
    return element[len(prefix):] if element.lower().startswith(prefix) else element
