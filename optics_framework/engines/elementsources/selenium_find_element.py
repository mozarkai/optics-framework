import time
from typing import Any, Optional, List, Tuple, Callable
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.common.by import By
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils

NOT_INITIALISED_MSG = "Selenium driver is not initialized for SeleniumFindElement."

class SeleniumFindElement(ElementSourceInterface):
    """
    Selenium Find Element Class
    """

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
        page_source = self.driver.page_source
        timestamp = utils.get_timestamp()
        return str(page_source), str(timestamp)

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        msg = "Getting interactive elements is not yet supported using Selenium Find Element."
        internal_logger.exception(msg)
        raise NotImplementedError(msg)

    def locate(self, element: str, index: int = 0) -> Any:
        """
        Locate an element on the current webpage using Selenium.

        This method determines the element type (XPath, text, or ID) and locates it using
        the appropriate Selenium strategy. Text-based elements are searched using an XPath
        contains() expression, with a fallback to ID if not found. Image-based elements are
        not supported in Selenium.

        Args:
            element (str): The identifier of the element to locate. Can be an XPath string,
                visible text, or element ID.
            index (int, optional): Index-based selection is not supported for Selenium and
                will raise a ValueError if provided.

        Returns:
            WebElement or None: The located Selenium WebElement if found, otherwise None.

        Raises:
            ValueError: If the `index` argument is provided, as it's unsupported in Selenium.
        """
        if self.driver is None:
            internal_logger.error(NOT_INITIALISED_MSG)
            raise RuntimeError(NOT_INITIALISED_MSG)
        element_type = utils.determine_element_type(element)
        if index != 0:
            msg = 'Selenium Find Element does not support locating elements using index.'
            internal_logger.error(msg)
            raise ValueError(msg)
        try:
            if element_type == "Image":
                internal_logger.debug("Selenium does not support locating elements by image.")
                return None
            elif element_type == "XPath":
                internal_logger.debug(f"Locating by XPath: {element}")
                found_element = self.driver.find_element(By.XPATH, element)
                return found_element if found_element else None
            elif element_type == "Text":
                try:
                    xpath = f"//*[contains(text(), '{element}') or normalize-space(text())='{element}']"
                    internal_logger.debug(f"Trying text-based XPath: {xpath}")
                    found_element = self.driver.find_element(By.XPATH, xpath)
                    return found_element if found_element else None
                except NoSuchElementException:
                    internal_logger.debug(f"Text not found, falling back to ID: {element}")
                    return self._find_element_by_any(element)
        except NoSuchElementException:
            internal_logger.warning(f"Element not found: {element}")
            return None
        except Exception as e:
            internal_logger.error(f"Unexpected error locating element {element}: {e}")
            return None

    def _find_element_by_any(self, locator_value: str) -> Any:
        """
        Try locating an element using all known Selenium strategies.
        """
        if self.driver is None:
            internal_logger.error(NOT_INITIALISED_MSG)
            raise RuntimeError(NOT_INITIALISED_MSG)
        strategies = [
            (By.ID, locator_value),
            (By.NAME, locator_value),
            (By.CLASS_NAME, locator_value),
            (By.TAG_NAME, locator_value),
            (By.LINK_TEXT, locator_value),
            (By.PARTIAL_LINK_TEXT, locator_value),
            (By.CSS_SELECTOR, locator_value),
            (By.XPATH, locator_value),
        ]
        for strategy, value in strategies:
            try:
                internal_logger.debug(f"Trying {strategy}: {value}")
                found_element = self.driver.find_element(strategy, value)
                if found_element:
                    return found_element
            except NoSuchElementException:
                continue
        internal_logger.warning(f"No matching element found using any strategy for: {locator_value}")
        return None

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
            check_fn=lambda element: self.locate(element) is not None,
            not_found_desc="found",
        )

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
        """
        Not yet enabled: SeleniumFindElement.locate()/execute_script() currently raise
        AttributeError because the injected driver is the SeleniumDriver wrapper, not the
        raw webdriver.Remote it wraps (a separate, pre-existing bug). The call below has the
        real bounds-check logic ready -- kept in place, unreachable, to switch back to once
        that driver-unwrap bug is fixed and this has been verified live.
        """
        raise NotImplementedError(
            "SeleniumFindElement does not support assert_elements_visible yet."
        )
        self._assert_elements_common(
            elements, timeout, rule,
            check_fn=self._is_located_and_visible,
            not_found_desc="visible",
        )


    def locate_using_index(self) -> None:
        msg = 'Selenium Find Element does not support locating elements using index.'
        internal_logger.error(msg)
        raise NotImplementedError(msg)
