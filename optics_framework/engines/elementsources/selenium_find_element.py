import time
from typing import Any, Optional, List, Tuple, Callable
from selenium.common.exceptions import WebDriverException
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from optics_framework.engines.drivers import selenium_locator

NOT_INITIALISED_MSG = "Selenium driver is not initialized for SeleniumFindElement."

class SeleniumFindElement(ElementSourceInterface):
    """
    Selenium Find Element Class
    """

    REQUIRED_DRIVER_TYPE = "selenium"

    def __init__(self, driver: Any = None):
        """
        Initialize the Selenium Find Element Class.
        Args:
            driver: The Selenium driver instance (should be passed explicitly).
        """
        self.driver = driver
        self.tree = None
        self.root = None

    def capture(self) -> None:
        """
        Capture the current screen state.
        """
        msg = 'Selenium Find Element does not support capturing the screen state.'
        internal_logger.exception(msg)
        raise NotImplementedError(msg)

    def get_page_source(self) -> Tuple[str, str]:
        """
        Get the page source of the current page.
        Returns:
            Tuple[str, str]: (page_source, timestamp)
        """
        if self.driver is None:
            internal_logger.error(NOT_INITIALISED_MSG)
            raise RuntimeError(NOT_INITIALISED_MSG)
        page_source = self.driver.require_webdriver().page_source
        timestamp = utils.get_timestamp()
        return str(page_source), str(timestamp)

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        msg = "Getting interactive elements is not yet supported using Selenium Find Element."
        internal_logger.exception(msg)
        raise NotImplementedError(msg)

    def locate(self, element: str, index: Optional[int] = None) -> Any:
        if self.driver is None:
            internal_logger.error(NOT_INITIALISED_MSG)
            raise RuntimeError(NOT_INITIALISED_MSG)
        return selenium_locator.find(self.driver, element, index or 0)

    def get_element_bboxes(
        self, elements: list
    ) -> List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]]:
        """Return bounding boxes for each element using WebElement location and size."""
        return utils.bboxes_from_webelements(self.locate, elements)

    def get_bbox_for_element(
        self, element: Any
    ) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """Return bounding box for an already-located WebElement."""
        return utils.bbox_from_webelement_like(element)

    def _assert_elements_common(
        self, elements: list, timeout: int, rule: str,
        check_fn: Callable[[str], bool], not_found_desc: str,
    ) -> None:
        """Shared polling loop backing assert_elements/assert_elements_visible.

        Only the per-element check (presence vs. visibility) and the timeout message differ.
        """
        if rule not in ["any", "all"]:
            msg = "Invalid rule. Use 'any' or 'all'."
            internal_logger.error(msg)
            raise ValueError(msg)

        if self.driver is None:
            msg = "Selenium session not started. Call start_session() first."
            internal_logger.error(msg)
            raise RuntimeError(msg)

        start_time = time.time()

        while time.time() - start_time < timeout:
            results = [check_fn(element) for element in elements]

            if (rule == "all" and all(results)) or (rule == "any" and any(results)):
                internal_logger.debug(f"Assertion passed with rule '{rule}' for elements: {elements}")
                return

            time.sleep(0.3)
        msg = f"Timeout reached: None of the specified elements were {not_found_desc}."
        internal_logger.error(msg)
        raise TimeoutError(msg)

    def assert_elements(self, elements: list, timeout: int = 10, rule: str = "any") -> None:
        """
        Assert that elements are present based on the specified rule using Selenium.

        Args:
            elements (list): List of element identifiers to locate (e.g., XPath strings).
            timeout (int): Maximum time to wait for elements in seconds (default: 10).
            rule (str): Rule to apply ("any" or "all").

        Raises:
            TimeoutError: If elements are not found based on the rule within the timeout.
        """
        self._assert_elements_common(
            elements, timeout, rule,
            check_fn=self._is_present,
            not_found_desc="found",
        )

    def check_elements(self, elements: list[str], visible: bool = False) -> list[bool]:
        return [self._is_present(element) for element in elements]

    def _is_present(self, element: str) -> bool:
        return self.locate(element) is not None

    def _is_located_and_visible(self, element: str) -> bool:
        """True only if `element` both locates, is not CSS-hidden, and is within the viewport.

        `.is_displayed()` alone only checks CSS (display/visibility/opacity) -- an element
        that is `display:block` but scrolled out of the current viewport still reports
        True, so it's paired here with a bounding-rect-vs-viewport intersection check.
        """
        try:
            located = self.locate(element)
            if located is None or not located.is_displayed():
                # something which doesn't exist cannot be visible
                # is_displayed() checks css of that element
                return False
            # check the bounding box of the screen vs the element
            return bool(self.driver.execute_script(
                "var r = arguments[0].getBoundingClientRect();"
                "return (r.top < window.innerHeight && r.bottom > 0 &&"
                " r.left < window.innerWidth && r.right > 0);",
                located,
            ))
        except WebDriverException:
            return False

    def assert_elements_visible(self, elements: list, timeout: int = 10, rule: str = "any") -> None:
        """Not yet enabled: _is_located_and_visible holds the bounds check to wire in once verified against a browser."""
        raise NotImplementedError(
            "SeleniumFindElement does not support assert_elements_visible yet."
        )
        # TODO: once verified, replace the raise above with:
        #   self._assert_elements_common(
        #       elements, timeout, rule,
        #       check_fn=self._is_located_and_visible,
        #       not_found_desc="visible",
        #   )


    def locate_using_index(self) -> None:
        msg = 'Selenium Find Element does not support locating elements using index.'
        internal_logger.error(msg)
        raise NotImplementedError(msg)
