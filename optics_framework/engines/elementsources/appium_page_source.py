from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.engines.drivers.appium_driver_manager import get_appium_driver
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from appium.webdriver.common.appiumby import AppiumBy
from optics_framework.engines.drivers.appium_UI_helper import UIHelper
from lxml import etree
import time


class AppiumPageSource(ElementSourceInterface):
    """
    Appium Find Element Class
    """

    def __init__(self):
        """
        Initialize the Appium Find Element Class.

        Args:
            driver: The Appium driver instance.
        """
        self.driver = None
        self.ui_helper = UIHelper()
        self.tree = None
        self.root = None

    def _get_appium_driver(self):
        if self.driver is None:
            self.driver = get_appium_driver()
        return self.driver

    def capture(self):
        """
        Capture the current screen state.

        return """
        internal_logger.exception('Appium Find Element does not support capturing the screen state.')
        raise NotImplementedError(
            'Appium Find Element does not support capturing the screen state.')

    def get_page_source(self) -> str:
        """
        Get the page source of the current page.

        Returns:
            str: The page source.
        """
        time_stamp = utils.get_current_time_for_events()

        driver = self._get_appium_driver()
        page_source = driver.page_source

        self.tree = etree.ElementTree(etree.fromstring(page_source.encode('utf-8')))
        self.root = self.tree.getroot()

        internal_logger.debug('\n\n========== PAGE SOURCE FETCHED ==========\n')
        internal_logger.debug(f'Page source fetched at: {time_stamp}')
        internal_logger.debug('\n==========================================\n')
        return page_source


    def locate(self, element: str, index=None) -> dict:
        """
        Locate a UI element on the current page using Appium.

        This method determines the type of the element (text, XPath, or image) and attempts
        to locate it using the Appium driver. Image-based search is not supported.

        Args:
            element (str): The element identifier to locate. This can be text, an XPath, or an image path.
            index (int, optional): If multiple elements match the given text, the index specifies
                which one to retrieve. Used only when element type is text.

        Returns:
            dict or None: A WebElement dictionary if the element is found; otherwise, None for unsupported types (e.g., image).
        """
        driver = self._get_appium_driver()
        element_type = utils.determine_element_type(element)

        if element_type == 'Image':
            # Find the element by image
            internal_logger.debug('Appium Find Element does not support finding images.')
            return None
        else:
            if element_type == 'Text':
                if index is not None:
                    xpath = self.find_xpath_from_text_index(element, index)
                else:
                    xpath = self.find_xpath_from_text(element)

                try:
                    element = driver.find_element(AppiumBy.XPATH, xpath)
                except Exception as e:
                    internal_logger.exception(f"Error finding element by text: {e}")
                    raise Exception (f"Error finding element by text: {e}")
                return element
            elif element_type == 'XPath':
                xpath, _ = self.ui_helper.find_xpath(element)
                try:
                    element = driver.find_element(AppiumBy.XPATH, xpath)
                except Exception as e:
                    internal_logger.exception(f"Error finding element by xpath: {e}")
                    raise Exception (f"Error finding element by xpath: {e}")
                return element


    def locate_using_index(self, element, index, strategy=None) -> dict:
        locators = self.ui_helper.get_locator_and_strategy_using_index(element, index, strategy)
        if locators:
            strategy = locators['strategy']
            locator = locators['locator']
            xpath = self.ui_helper.get_view_locator(strategy=strategy, locator=locator)
            try:
                element = self.driver.find_element(AppiumBy.XPATH, xpath)
            except Exception as e:
                internal_logger.exception(f"Error finding element by index: {e}")
                raise Exception (f"Error finding element by index: {e}")
            return element
        return {}


    def assert_elements(self, elements, timeout=30, rule='any'):
        """
        Assert the presence of elements on the current page.

        Args:
            elements (list): List of elements to assert on the page.
            timeout (int): Maximum time to wait for the elements to appear.
            rule (str): Rule to apply ("any" or "all").
            polling_interval (float): Interval between retries in seconds.

        Returns:
            bool: True if the elements are found.

        Raises:
            Exception: If elements are not found based on the rule within the timeout.
        """
        if rule not in ["any", "all"]:
            raise ValueError("Invalid rule. Use 'any' or 'all'.")

        start_time = time.time()

        while time.time() - start_time < timeout:
            texts = [el for el in elements if utils.determine_element_type(el) == 'Text']
            xpaths = [el for el in elements if utils.determine_element_type(el) == 'XPath']

            self.get_page_source()  # Refresh page source before checking

            # Check text-based elements
            if texts:
                text_found = self.ui_text_search(texts, rule)
                if rule == "all" and not text_found:
                    time.sleep(0.3)
                    continue
                if rule == "any" and text_found:
                    return True  # Early exit if any text is found

            # Check XPath-based elements
            xpath_found = False
            for xpath in xpaths:
                xpath_result,_ = self.ui_helper.find_xpath(xpath)
                if rule == "all" and not xpath_result:
                    xpath_found = False
                    break  # Stop checking further if one is missing
                if rule == "any" and xpath_result:
                    return True  # Early exit if any XPath is found
                xpath_found = True  # If all xpaths found in "all" mode

            if rule == "all" and text_found and xpath_found:
                return True  # All required elements are found

            time.sleep(0.3)  # Wait before retrying

        # Timeout reached, raise an exception based on rule
        if rule == "all":
            raise TimeoutError(f"Timeout reached: Not all elements were found: {elements}")

        if rule == "any":
            raise TimeoutError("Timeout reached: None of the specified elements were found.")

        return False  # This should never be reached due to exceptions


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
