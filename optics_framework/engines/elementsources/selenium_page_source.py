import time
from typing import Optional, Any, Tuple, List
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from optics_framework.engines.drivers.selenium_UI_helper import UIHelper


class SeleniumPageSource(ElementSourceInterface):
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
        self.ui_helper: Optional[UIHelper] = (
            getattr(self.driver, "ui_helper", None) if self.driver is not None else None
        )
        self.tree = None
        self.root = None

    def _require_webdriver(self) -> Any:
        if self.driver is None:
            internal_logger.error("Selenium driver is not initialized for SeleniumPageSource.")
            raise RuntimeError("Selenium driver is not initialized for SeleniumPageSource.")
        if self.ui_helper is None and hasattr(self.driver, "ui_helper"):
            self.ui_helper = self.driver.ui_helper
        return self.driver

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
        # Optionally parse tree/root for future extensibility
        self.tree = None
        self.root = None
        internal_logger.debug('Page source fetched at: %s', time_stamp)
        return str(page_source), str(time_stamp)

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        msg = "Getting interactive elements is not yet supported using Selenium Page Source."
        internal_logger.exception(msg)
        raise NotImplementedError(msg)

    def _strict_element_match(self) -> bool:
        """Whether project config requests exact-only matching for locate (Config.strict_element_match).

        Presence/assert checks ignore this and are always strict -- see assert_elements.
        """
        try:
            return bool(self.driver.event_sdk.config_handler.config.strict_element_match)
        except AttributeError:
            return False

    def locate(self, element: str, index: Optional[int] = None, *, strict_override: Optional[bool] = None) -> Any:
        """
        Locate an element on the current webpage using Selenium.

        Args:
            element (str): The identifier of the element to locate. Can be an XPath string, visible text, or element ID.
            index (int, optional): Index-based selection is not supported for Selenium and will raise a ValueError if provided.
            strict_override (bool, optional): bypasses Config.strict_element_match -- used by
                assert_elements, which always resolves text strictly regardless of config.

        Returns:
            WebElement or None: The located Selenium WebElement if found, otherwise None.

        Raises:
            ValueError: If the `index` argument is provided, as it's unsupported in Selenium.
        """
        driver = self._require_webdriver()
        element_type = utils.determine_element_type(element)
        if index is not None:
            msg = 'Selenium Page Source does not support locating elements using index.'
            internal_logger.error(msg)
            raise ValueError(msg)
        strict = strict_override if strict_override is not None else self._strict_element_match()
        try:
            if element_type == "Image":
                internal_logger.debug("Selenium does not support locating elements by image.")
                return None
            elif element_type == "XPath":
                internal_logger.debug(f"Locating by XPath: {element}")
                found_element = driver.find_element("xpath", element)
                return found_element if found_element else None
            elif element_type == "Text":
                return self._locate_text(driver, element, strict)
        except Exception as e:
            internal_logger.error(f"Unexpected error locating element {element}: {e}")
            return None

    def _locate_text(self, driver: Any, element: str, strict: bool) -> Any:
        try:
            xpath = (
                f"//*[normalize-space(text())='{element}']"
                if strict
                else f"//*[contains(text(), '{element}') or normalize-space(text())='{element}']"
            )
            internal_logger.debug(f"Trying text-based XPath: {xpath}")
            found_element = driver.find_element("xpath", xpath)
            return found_element if found_element else None
        except Exception:
            if strict:
                internal_logger.debug(f"Strict matching enabled; no exact text match found for: {element}")
                return None
            internal_logger.debug(f"Text not found, falling back to ID: {element}")
            return self._find_element_by_any(driver, element, strict=strict)

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

    def _find_element_by_any(self, driver: Any, locator_value: str, strict: bool = False) -> Any:
        """
        Try locating an element using all known Selenium strategies.

        strict=True skips "partial link text" -- the one strategy here that matches on
        a substring rather than an exact value.
        """
        strategies = [
            ("id", locator_value),
            ("name", locator_value),
            ("class name", locator_value),
            ("tag name", locator_value),
            ("link text", locator_value),
        ]
        if not strict:
            strategies.append(("partial link text", locator_value))
        strategies += [
            ("css selector", locator_value),
            ("xpath", locator_value),
        ]
        for strategy, value in strategies:
            try:
                internal_logger.debug(f"Trying {strategy}: {value}")
                found_element = driver.find_element(strategy, value)
                if found_element:
                    return found_element
            except Exception as e:
                internal_logger.debug(f"Strategy {strategy} failed for value {value}: {e}")
                continue
        internal_logger.warning(f"No matching element found using any strategy for: {locator_value}")
        return None


    def locate_using_index(self) -> None:
        msg = 'Selenium Page Source does not support locating elements using index.'
        internal_logger.error(msg)
        raise NotImplementedError(msg)


    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> None:
        """
        Assert the presence of elements on the current page.

        Args:
            elements (list): List of elements to assert on the page.
            timeout (int): Maximum time to wait for the elements to appear.
            rule (str): Rule to apply ("any" or "all").

        Returns:
            None

        Raises:
            Exception: If elements are not found based on the rule within the timeout.
        """
        if rule not in ["any", "all"]:
            raise ValueError("Invalid rule. Use 'any' or 'all'.")

        start_time = time.time()

        while time.time() - start_time < timeout:
            found_elements = [
                self.locate(element, strict_override=True) is not None for element in elements
            ]

            if (rule == "all" and all(found_elements)) or (rule == "any" and any(found_elements)):
                internal_logger.debug(f"Assertion passed with rule '{rule}' for elements: {elements}")
                return

            time.sleep(0.3)
        internal_logger.warning(f"Timeout reached. Rule: {rule}, Elements: {elements}")
        raise TimeoutError(
            f"Timeout reached: Elements not found based on rule '{rule}': {elements}"
        )

    # ----- Supporting Methods -----
    def _is_text_found(self, text: str) -> bool:
        if self.ui_helper is None:
            internal_logger.warning("UIHelper is not initialized.")
            return False
        try:
            self.ui_helper.find_html_element_by_text(text)
            return True
        except ValueError:
            return False

    def _is_xpath_found(self, xpath: str) -> bool:
        if self.ui_helper is None:
            internal_logger.warning("UIHelper is not initialized.")
            return False
        try:
            self.ui_helper.find_html_element_by_xpath(xpath)
            return True
        except ValueError:
            return False
