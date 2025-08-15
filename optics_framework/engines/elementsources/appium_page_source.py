from optics_framework.common.elementsource_interface import ElementSourceInterface
from typing import Optional, Any, Tuple
from appium.webdriver.webdriver import WebDriver
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from appium.webdriver.common.appiumby import AppiumBy
from optics_framework.engines.drivers.appium_UI_helper import UIHelper
from lxml import etree
import time


class AppiumPageSource(ElementSourceInterface):
    REQUIRED_DRIVER_TYPE = "appium"
    """
    Appium Find Element Class
    """

    driver: Optional[WebDriver]
    ui_helper: UIHelper
    tree: Optional[Any]
    root: Optional[Any]

    def __init__(self, driver: Optional[WebDriver] = None):
        """
        Initialize the AppiumPageSource Class.
        Args:
            driver: The Appium driver instance (should be passed explicitly).
        """
        self.driver = driver
        self.ui_helper = UIHelper()
        self.tree = None
        self.root = None

    def _require_driver(self) -> WebDriver:
        """Helper to ensure self.driver is initialized, else raise error."""
        if self.driver is None:
            internal_logger.error("Appium driver is not initialized for AppiumPageSource.")
            raise RuntimeError("Appium driver is not initialized for AppiumPageSource.")
        return self.driver

    def capture(self):
        """
        Capture the current screen state.

        return """
        internal_logger.exception('Appium Find Element does not support capturing the screen state.')
        raise NotImplementedError(
            'Appium Find Element does not support capturing the screen state.')

    def get_page_source(self) -> Tuple[str, str]:
        """
        Get the page source of the current page.

        Returns:
            str: The page source.
        """
        time_stamp = utils.get_timestamp()

        driver = self._require_driver()
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

    def get_interactive_elements(self):
        return self.ui_helper.get_interactive_elements()

    def locate(self, element: str, index: Optional[int] = None) -> Tuple[Any, ...]:
        """
        Locate a UI element on the current page using Appium.

        This method determines the type of the element (text, XPath, or image) and attempts
        to locate it using the Appium driver. Image-based search is not supported.

        Args:
            element (str): The element identifier to locate. This can be text, an XPath, or an image path.
            index (int, optional): If multiple elements match the given text, the index specifies
                which one to retrieve. Used only when element type is text.

        Returns:
            tuple: A tuple containing the found WebElement(s), or an empty tuple for unsupported types (e.g., image).
        """
        driver = self._require_driver()
        element_type = utils.determine_element_type(element)

        if element_type == 'Image':
            internal_logger.debug('Appium Find Element does not support finding images.')
            return ()
        elif element_type == 'Text':
            if index is not None:
                xpath = self.find_xpath_from_text_index(element, index)
            else:
                xpath = self.find_xpath_from_text(element)
            try:
                element_obj = driver.find_element(AppiumBy.XPATH, xpath)
                return (element_obj,)
            except Exception:
                internal_logger.exception("Error finding element by text: %s", xpath)
                return ()
        elif element_type == 'XPath':
            xpath, _ = self.ui_helper.find_xpath(element)
            try:
                element_obj = driver.find_element(AppiumBy.XPATH, xpath)
                return (element_obj,)
            except Exception:
                internal_logger.exception("Error finding element by xpath: %s", xpath)
                return ()
        return ()


    def locate_using_index(self, element, index, strategy=None) -> Optional[Any]:
        locators = self.ui_helper.get_locator_and_strategy_using_index(element, index, strategy)
        if locators:
            strategy = locators['strategy']
            locator = locators['locator']
            xpath = self.ui_helper.get_view_locator(strategy=strategy, locator=locator)
            try:
                element_obj = self._require_driver().find_element(AppiumBy.XPATH, xpath)
            except Exception:
                internal_logger.exception("Error finding element by index: %s", xpath)
                return None
            return element_obj
        return None


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
        if rule not in ["any", "all"]:
            raise ValueError("Invalid rule. Use 'any' or 'all'.")

        start_time = time.time()

        while time.time() - start_time < timeout:
            texts = [el for el in elements if utils.determine_element_type(el) == 'Text']
            xpaths = [el for el in elements if utils.determine_element_type(el) == 'XPath']

            self.get_page_source()  # Refresh page source

            # Check text-based elements
            text_found = self.ui_text_search(texts, rule) if texts else (rule == "all")

            # Check XPath-based elements
            xpath_results = [self.ui_helper.find_xpath(xpath)[0] for xpath in xpaths] if xpaths else [rule == "all"]
            xpath_found = (all(xpath_results) if rule == "all" else any(xpath_results))

            # Rule evaluation
            if (rule == "any" and (text_found or xpath_found)) or (rule == "all" and text_found and xpath_found):
                return

            # Optional: time.sleep(0.3)  # Delay to reduce busy looping

        # Timeout reached
        internal_logger.warning(f"Timeout reached. Rule: {rule}, Elements: {elements}")
        raise TimeoutError(
            f"Timeout reached: Elements not found based on rule '{rule}': {elements}"
        )


    def find_xpath_from_text(self, text):
        """
        Find the XPath of an element based on the text content.

        Args:
            text (str): The text content to search for in the UI tree.

        Returns:
            str: The XPath of the element containing the
            text content, or None if not found.
        """
        locators = self.ui_helper.get_locator_and_strategy(text)
        if locators:
            strategy = locators['strategy']
            locator = locators['locator']
            xpath = self.ui_helper.get_view_locator(strategy=strategy, locator=locator)
            return xpath
        return None

    def find_xpath_from_text_index(self, text, index, strategy=None):
        locators = self.ui_helper.get_locator_and_strategy_using_index(text, index, strategy)
        if locators:
            strategy = locators['strategy']
            locator = locators['locator']
            xpath = self.ui_helper.get_view_locator(strategy=strategy, locator=locator)
            return xpath
        return None


    def ui_text_search(self, texts, rule='any'):
        """
        Checks if any or all given texts exist in the UI tree.

        Args:
            texts (list): List of text strings to search for.
            rule (str): Rule for matching ('any' or 'all').

        Returns:
            bool: True if the condition is met, otherwise False.
        """
        strategies = ["text", "resource-id", "content-desc", "name", "value", "label"]

        found_texts = set()

        for text in texts:
            internal_logger.debug(f'Searching for text: {text}')

            for attrib in strategies:
                matching_elements = self.tree.xpath(f"//*[@{attrib}]")

                for elem in matching_elements:
                    attrib_value = elem.attrib.get(attrib, '').strip()

                    if attrib_value and utils.compare_text(attrib_value, text):
                        internal_logger.debug(f"Match found using {attrib} for '{text}'")
                        found_texts.add(text)  # Mark this text as found
                        break  # Stop searching other elements for this text

                if text in found_texts:  # Stop checking other strategies if already found
                    break

            if rule == 'any' and text in found_texts:
                return True  # Early exit if at least one match is found

        return len(found_texts) == len(texts) if rule == 'all' else False
