from typing import Optional, Any, Tuple, List, Dict
from lxml import etree  # type: ignore
from selenium.common.exceptions import WebDriverException
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.session_liveness import raise_if_session_dead
from optics_framework.common import utils
from optics_framework.engines.drivers import selenium_locator
from optics_framework.engines.elementsources.playwright_page_source import (
    BOUNDS_JS,
    SUGGEST_LOCATORS_JS,
    WebDomInspector,
)

_INNER_TEXT_JS = (
    "const el = document.evaluate(arguments[0], document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null)"
    ".singleNodeValue; return el ? el.innerText : null;"
)


class SeleniumPageSource(ElementSourceInterface, WebDomInspector):
    """
    Selenium Page Source Handler Class

    This class is responsible for retrieving and interacting with the page source
    using Selenium WebDriver.
    """

    REQUIRED_DRIVER_TYPE = "selenium"

    driver: Optional[Any]
    tree: Optional[Any]
    root: Optional[Any]

    def __init__(self, driver: Optional[Any] = None):
        """
        Initialize the Selenium Page Source Handler Class.
        Args:
            driver: The Selenium driver instance (should be passed explicitly).
        """
        self.driver = driver
        self.tree = None
        self.root = None

    def _require_webdriver(self) -> Any:
        if self.driver is None:
            internal_logger.error("Selenium driver is not initialized for SeleniumPageSource.")
            raise RuntimeError("Selenium driver is not initialized for SeleniumPageSource.")
        return self.driver.require_webdriver()

    def capture(self):
        """
        Capture the current screen state.
        """
        msg = 'Selenium Page Source does not support capturing the screen state.'
        internal_logger.exception(msg)
        raise NotImplementedError(msg)

    def get_page_source(self) -> Tuple[str, str]:
        """
        Get the page source of the current page.
        Returns:
            Tuple[str, str]: The raw page source and timestamp.
        """
        time_stamp = utils.get_timestamp()
        driver = self._require_webdriver()
        page_source = driver.page_source
        self.tree = etree.HTML(page_source)
        self.root = self.tree
        internal_logger.debug('Page source fetched at: %s', time_stamp)
        return str(page_source), str(time_stamp)

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None, compact: bool = False) -> List[Dict]:
        """The page's elements as Optics screen elements; see ``PlaywrightPageSource.get_interactive_elements``."""
        self.get_page_source()
        return self._web_interactive_elements(filter_config, compact)

    def _inspect_nodes(
        self, xpaths: List[Optional[str]], locate: List[Optional[str]]
    ) -> Tuple[Optional[List[Optional[Dict[str, Any]]]], List[Optional[str]]]:
        rects = self._run_batch(BOUNDS_JS, xpaths)
        locators = self._run_batch(SUGGEST_LOCATORS_JS, locate) if rects is not None and any(locate) else None
        return rects, locators or [None] * len(xpaths)

    def _try_inner_text(self, node: etree.Element) -> Tuple[Optional[str], Optional[str]]:
        """The browser's innerText, which leaves out script source and CSS-hidden text."""
        xpath = self._build_simple_xpath(node)
        text = self._run_script(_INNER_TEXT_JS, xpath) if xpath else None
        return (text.strip(), "innerText") if isinstance(text, str) and text.strip() else (None, None)

    def _run_batch(self, script: str, xpaths: List[Optional[str]]) -> Optional[List[Any]]:
        """``script``'s answer per xpath in one round trip, or ``None`` when the call or its payload fails."""
        result = self._run_script(f"return ({script})(arguments[0]);", xpaths)
        return result if isinstance(result, list) and len(result) == len(xpaths) else None

    def _run_script(self, script: str, argument: Any) -> Any:
        try:
            return self._require_webdriver().execute_script(script, argument)
        except WebDriverException as e:
            raise_if_session_dead(e)
            internal_logger.debug("[SeleniumPageSource] Page script failed: %s", e)
            return None

    def locate(self, element: str, index: Optional[int] = None) -> Any:
        self._require_webdriver()
        return selenium_locator.find(self.driver, element, index or 0)

    def get_element_bboxes(
        self, elements: list
    ) -> List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]]:
        """Return bounding boxes for each element using WebElement location and size."""

        def locate_safe(element: str) -> Any:
            try:
                return self.locate(element)
            except Exception:
                return None

        return utils.bboxes_from_webelements(locate_safe, elements)

    def get_bbox_for_element(
        self, element: Any
    ) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """Return bounding box for an already-located WebElement."""
        return utils.bbox_from_webelement_like(element)

    def locate_using_index(self) -> None:
        msg = 'Selenium Page Source does not support locating elements using index.'
        internal_logger.error(msg)
        raise NotImplementedError(msg)

    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> None:
        self._require_webdriver()
        selenium_locator.wait_for(self.driver, elements, timeout, rule)

    def assert_elements_visible(self, elements: list, timeout: int = 30, rule: str = 'any') -> None:
        self._require_webdriver()
        selenium_locator.wait_for(self.driver, elements, timeout, rule, visible=True)

    def check_elements(self, elements: list[str], visible: bool = False) -> list[bool]:
        self._require_webdriver()
        return selenium_locator.check(self.driver, elements, visible)
