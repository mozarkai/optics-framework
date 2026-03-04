import time
from typing import Optional, Any, Tuple, List
from lxml import etree  # type: ignore
from appium.webdriver.webdriver import WebDriver
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import NoSuchElementException

from optics_framework.common.logging_config import internal_logger, execution_logger
from optics_framework.common import utils
from optics_framework.common.elementsource_interface import ElementSourceInterface
import re

APPIUM_NOT_INITIALISED_MSG = "Appium driver is not initialized for AppiumPageSource."

class AppiumPageSource(ElementSourceInterface):
    REQUIRED_DRIVER_TYPE = "appium"
    """
    Appium Find Element Class
    """

    driver: Optional[Any]  # Can be Appium WebDriver or Appium wrapper
    tree: Optional[Any]
    root: Optional[Any]

    def __init__(self, driver: Optional[Any] = None):
        """
        Initialize the Appium Page Source Class.
        Args:
            driver: The Appium driver instance (should be passed explicitly).
        """
        self.driver = driver
        self.tree = None
        self.root = None

    def _require_webdriver(self) -> WebDriver:
        # If self.driver is None, raise error first
        if self.driver is None:
            internal_logger.error(
                APPIUM_NOT_INITIALISED_MSG
            )
            raise RuntimeError(
                APPIUM_NOT_INITIALISED_MSG
            )
        # If self.driver is a wrapper, extract the raw driver
        if hasattr(self.driver, "driver"):
            return self.driver.driver
        raise RuntimeError(APPIUM_NOT_INITIALISED_MSG)

    def capture(self):
        """
        Capture the current screen state.

        return """
        internal_logger.exception('Appium Page Source does not support capturing the screen state.')
        raise NotImplementedError(
            'Appium Page Source does not support capturing the screen state.')

    def get_page_source(self) -> Tuple[str, str]:
        """
        Get the page source of the current page.

        Returns:
            str: The page source.
        """
        time_stamp = utils.get_timestamp()

        driver = self._require_webdriver()
        page_source = driver.page_source
        self.tree = etree.ElementTree(etree.fromstring(page_source.encode('utf-8')))
        if self.tree is not None:
            self.root = self.tree.getroot()
        else:
            self.root = None
        internal_logger.debug('\n\n========== PAGE SOURCE FETCHED ==========' )
        internal_logger.debug('Page source fetched at: %s', time_stamp)
        internal_logger.debug('\n==========================================')
        return str(page_source), str(time_stamp)

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        if self.driver is not None and hasattr(self.driver, "ui_helper"):
            return self.driver.ui_helper.get_interactive_elements(filter_config)
        internal_logger.error(APPIUM_NOT_INITIALISED_MSG)
        raise RuntimeError(APPIUM_NOT_INITIALISED_MSG)

    def _find_by_class_index(self, driver, element: str) -> Any:
        """Helper to handle 'index_N:class.name' patterns.

        Example:
            index_0:android.widget.TextView
        """
        try:
            index_part, class_name = element.split(":", 1)
            index = int(index_part.replace("index_", ""))
        except (ValueError, IndexError) as e:
            raise ValueError(f"Invalid index format in element string: {element}") from e

        if index < 0:
            raise IndexError(f"Index must be >= 0. Received: {index}")

        elements = driver.find_elements(AppiumBy.CLASS_NAME, class_name)

        if not elements:
            raise NoSuchElementException(f"No elements found for class: {class_name}")

        if index >= len(elements):
            raise IndexError(f"Index {index} out of range. Found {len(elements)} elements.")

        return elements[index]

    def locate(self, element: str, index: Optional[int] = None) -> Any:
        """
        Locate a UI element on the current page using Appium.
        """
        driver = self._require_webdriver()
        element_type = utils.determine_element_type(element)

        if element_type == "Image":
            internal_logger.debug("Appium Page Source does not support finding images.")
            return None

        if element_type == "Text":
            if re.match(r"^index_\d+:", element):
                return self._find_by_class_index(driver, element)

            xpath = (
                self.find_xpath_from_text_index(element, index)
                if index is not None
                else self.find_xpath_from_text(element)
            )

            return self._find_element(driver, xpath, "text")

        if element_type == "XPath":
            if not (self.driver and hasattr(self.driver, "ui_helper") and self.driver.ui_helper):
                internal_logger.error(APPIUM_NOT_INITIALISED_MSG)
                raise RuntimeError(APPIUM_NOT_INITIALISED_MSG)

            xpath, _ = self.driver.ui_helper.find_xpath(element)
            return self._find_element(driver, xpath, "xpath")

        raise RuntimeError("Unknown element type.")

    def get_element_bboxes(
        self, elements: List[str]
    ) -> List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]]:
        """
        Return bounding boxes for each element using WebElement location and size.
        Compatible with Android (UIAutomator2) and iOS (XCUITest); both expose
        W3C location/size/rect on the WebElement.
        """
        def locate_safe(element: str) -> Any:
            try:
                return self.locate(element)
            except Exception:
                return None

        return utils.bboxes_from_webelements(locate_safe, elements)

    def get_bbox_for_element(
        self, element: Any
    ) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """
        Return bounding box for an already-located Appium WebElement.
        Uses location/size or rect first (Android and iOS); falls back to
        get_attribute("bounds") (Android) or get_attribute("rect") (iOS).
        """
        bbox = utils.bbox_from_webelement_like(element)
        if bbox is not None:
            return bbox
        return utils.bbox_from_appium_attribute_fallback(element)

    def locate_using_index(self, element, index, strategy=None) -> Optional[Any]:
        if self.driver is not None and hasattr(self.driver, "ui_helper") and self.driver.ui_helper is not None:
            locators = self.driver.ui_helper.get_locator_and_strategy_using_index(element, index, strategy)
            if locators:
                strategy = locators['strategy']
                locator = locators['locator']
                xpath = self.driver.ui_helper.get_view_locator(strategy=strategy, locator=locator)
                try:
                    element_obj = self._require_webdriver().find_element(AppiumBy.XPATH, xpath)
                except Exception:
                    internal_logger.exception("Error finding element by index: %s", xpath)
                    return None
                return element_obj
            return None
        else:
            internal_logger.error(APPIUM_NOT_INITIALISED_MSG)
            raise RuntimeError(APPIUM_NOT_INITIALISED_MSG)


    def assert_elements(self, elements, timeout=30, rule='any'):
        """
        Assert the presence of elements on the current page.

        Args:
            elements (list): List of elements to assert on the page.
            timeout (int): Maximum time to wait for the elements to appear.
            rule (str): Rule to apply ("any" or "all").
            polling_interval (float): Interval between retries in seconds.

        Returns:
            None

        Raises:
            Exception: If elements are not found based on the rule within the timeout.
        """
        self._validate_rule(rule)
        start_time = time.time()

        while time.time() - start_time < timeout:
            texts = [el for el in elements if utils.determine_element_type(el) == 'Text']
            xpaths = [el for el in elements if utils.determine_element_type(el) == 'XPath']

            self.get_page_source()  # Refresh page source

            # Check text-based elements
            text_found = self.ui_text_search(texts, rule) if texts else (rule == "all")

            # Check XPath-based elements
            if self.driver is not None and hasattr(self.driver, "ui_helper") and self.driver.ui_helper is not None and xpaths:
                xpath_results = [self.driver.ui_helper.find_xpath(xpath)[0] for xpath in xpaths]
            else:
                xpath_results = [rule == "all"]
            xpath_found = (all(xpath_results) if rule == "all" else any(xpath_results))

            # Rule evaluation
            if (rule == "any" and (text_found or xpath_found)) or (rule == "all" and text_found and xpath_found):
                return True, utils.get_timestamp()

            # Optional: time.sleep(0.3)  # Delay to reduce busy looping

        # Timeout reached
        internal_logger.warning(f"Timeout reached. Rule: {rule}, Elements: {elements}")
        raise TimeoutError(
            f"Timeout reached: Elements not found based on rule '{rule}': {elements}"
        )

    def _validate_rule(self, rule):
        if rule not in ["any", "all"]:
            raise ValueError("Invalid rule. Use 'any' or 'all'.")

    def find_xpath_from_text(self, text):
        """
        Find the XPath of an element based on the text content.

        Args:
            text (str): The text content to search for in the UI tree.

        Returns:
            str: The XPath of the element containing the
            text content, or None if not found.
        """
        if self.driver is not None and hasattr(self.driver, "ui_helper") and self.driver.ui_helper is not None:
            locators = self.driver.ui_helper.get_locator_and_strategy(text)
            if locators:
                strategy = locators['strategy']
                locator = locators['locator']
                xpath = self.driver.ui_helper.get_view_locator(strategy=strategy, locator=locator)
                return xpath
        else:
            internal_logger.error(APPIUM_NOT_INITIALISED_MSG)
            raise RuntimeError(APPIUM_NOT_INITIALISED_MSG)
        raise RuntimeError("Failed to find XPath from text.")

    def find_xpath_from_text_index(self, text, index, strategy=None):
        if self.driver is not None and hasattr(self.driver, "ui_helper") and self.driver.ui_helper is not None:
            locators = self.driver.ui_helper.get_locator_and_strategy_using_index(text, index, strategy)
            if locators:
                strategy = locators['strategy']
                locator = locators['locator']
                xpath = self.driver.ui_helper.get_view_locator(strategy=strategy, locator=locator)
                return xpath
            return None
        else:
            internal_logger.error(APPIUM_NOT_INITIALISED_MSG)
            raise RuntimeError(APPIUM_NOT_INITIALISED_MSG)

    def _validate_tree(self):
        """Validates that the element tree is initialized."""
        if self.tree is None:
            internal_logger.error("Element tree is not initialized. Cannot perform xpath search.")
            raise RuntimeError("Element tree is not initialized.")

    def _search_text_in_attribute(self, text, attrib):
        """Searches for text in a specific attribute across all elements."""
        matching_elements = self.tree.xpath(f"//*[@{attrib}]")

        for elem in matching_elements:
            attrib_value = elem.attrib.get(attrib, '').strip()
            if attrib_value and utils.compare_text(attrib_value, text):
                internal_logger.debug(f"Match found using {attrib} for '{text}'")
                return True
        return False

    def _search_single_text(self, text):
        """Searches for a single text across all strategies."""
        strategies = ["text", "resource-id", "content-desc", "name", "value", "label"]
        internal_logger.debug(f'Searching for text: {text}')

        for attrib in strategies:
            if self._search_text_in_attribute(text, attrib):
                return True
        return False

    def ui_text_search(self, texts, rule='any'):
        """
        Checks if any or all given texts exist in the UI tree.

        Args:
            texts (list): List of text strings to search for.
            rule (str): Rule for matching ('any' or 'all').

        Returns:
            bool: True if the condition is met, otherwise False.
        """
        self._validate_tree()
        found_texts = set()

        for text in texts:
            if self._search_single_text(text):
                found_texts.add(text)
                if rule == 'any':
                    return True

        return len(found_texts) == len(texts) if rule == 'all' else False

    def _find_element(self, driver, xpath: str, source: str):
        try:
            execution_logger.debug(f"Finding element by {source}: {xpath}")
            return driver.find_element(AppiumBy.XPATH, xpath)
        except Exception:
            internal_logger.exception("Error finding element by %s: %s", source, xpath)
            raise RuntimeError(f"Error finding element by {source}.")