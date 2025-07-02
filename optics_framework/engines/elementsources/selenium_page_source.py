from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.engines.drivers.selenium_driver_manager import get_selenium_driver
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from optics_framework.engines.drivers.selenium_UI_helper import UIHelper
from selenium.webdriver.remote.webelement import WebElement
from typing import Tuple
import time


class SeleniumPageSource(ElementSourceInterface):
    """
    Selenium Page Source Handler Class

    This class is responsible for retrieving and interacting with the page source
    using Selenium WebDriver.
    """

    def __init__(self):
        """
        Initialize the Selenium Page Source Handler Class.

        Args:
            driver: The Selenium driver instance.
        """
        self.driver = None
        self.ui_helper = UIHelper()
        self.tree = None
        self.root = None

    def _get_selenium_driver(self):
        if self.driver is None:
            self.driver = get_selenium_driver()
        return self.driver

    def capture(self):
        """
        Capture the current screen state.

        Not Supported: This method is not implemented for Selenium Find Element.
        """
        internal_logger.exception('Selenium Find Element does not support capturing the screen state.')
        raise NotImplementedError(
            'Selenium Find Element does not support capturing the screen state.')



    def get_page_source(self) -> str:
        """
        Get the page source of the current page, parse it using BeautifulSoup,
        and write it to a human-readable HTML file for user reference.

        Returns:
            str: The raw page source.
        """
        ui_helper = UIHelper()
        return ui_helper.get_page_source()

    def get_interactive_elements(self):

        internal_logger.exception("Getting interactive elements is not yet suppored using Selenium Page Source.")
        raise NotImplementedError(
            "Getting interactive elements is not yet suppored using Selenium Page Source."
        )

    def locate(self, element: str, index: int = None) -> WebElement:
        """
        Locates a Selenium WebElement using either text or XPath from the current page source.

        This method:
        - Determines the type of the input (text, XPath, image)
        - Finds the corresponding tag in the page source using HTML parsing
        - Converts the matched tag info into a Selenium WebElement using DOM search

        Args:
            element (str): The element to locate (text string or XPath expression).
            index (int, optional): Index of the matching element to return if multiple are found.

        Returns:
            WebElement: The located and resolved Selenium WebElement ready for interaction.

        Raises:
            Exception: If the element cannot be found or mapped to the DOM.
        """
        element_type = utils.determine_element_type(element)

        try:
            if element_type == 'Image':
                internal_logger.debug('Selenium does not support finding elements by image.')
                return None

            elif element_type == 'Text':
                match = self.ui_helper.find_html_element_by_text(element, index)
                element = self.ui_helper.convert_to_selenium_element(match)
                internal_logger.debug(f"Text-based element match found: {match},{element}")
                return element

            elif element_type == 'XPath':
                match = self.ui_helper.find_html_element_by_xpath(element, index)
                element = self.ui_helper.convert_to_selenium_element(match)
                internal_logger.debug(f"XPath-based element match found: {match},{element}")
                return element

            else:
                internal_logger.warning(f"Unsupported element type detected: {element_type}")
                return None

        except Exception as e:
            internal_logger.exception(f"Error locating element '{element}': {e}")
            raise Exception(f"Error locating element '{element}': {e}")


    def locate_using_index(self, element, index: int=None) -> dict:
        return self.locate(element, index)


    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Tuple[bool, str]:
        """
        Assert the presence of elements in the page source (text or xpath based).

        Args:
            elements (list): List of element identifiers (text or XPath).
            timeout (int): Maximum time to wait for elements to appear.
            rule (str): "any" (return if any found) or "all" (require all).

        Returns:
            bool: True if assertion passes.

        Raises:
            TimeoutError: If condition is not met within timeout.
        """
        if rule not in {"any", "all"}:
            raise ValueError("Invalid rule. Use 'any' or 'all'.")

        start_time = time.time()
        texts = [el for el in elements if utils.determine_element_type(el) == 'Text']
        xpaths = [el for el in elements if utils.determine_element_type(el) == 'XPath']

        while time.time() - start_time < timeout:
            found_texts = [text for text in texts if self._is_text_found(text)]
            found_xpaths = [xpath for xpath in xpaths if self._is_xpath_found(xpath)]

            if (rule == "any" and (found_texts or found_xpaths)) or \
            (rule == "all" and len(found_texts) == len(texts) and len(found_xpaths) == len(xpaths)):
                timestamp = utils.get_timestamp()
                internal_logger.debug(f"Elements found with rule '{rule}' at {timestamp}")
                return True, timestamp

            # time.sleep(0.3)  # Optional: Polling interval

        # Timeout reached
        missing_texts = list(set(texts) - set(found_texts))
        missing_xpaths = list(set(xpaths) - set(found_xpaths))
        internal_logger.warning(f"Timeout reached: Missing texts: {missing_texts}, Missing xpaths: {missing_xpaths}")
        raise TimeoutError(
            f"Timeout reached: Not all elements were found.\n"
            f"Missing texts: {missing_texts}\nMissing xpaths: {missing_xpaths}"
        )

    # ----- Supporting Methods -----
    def _is_text_found(self, text: str) -> bool:
        try:
            self.ui_helper.find_html_element_by_text(text)
            return True
        except ValueError:
            return False

    def _is_xpath_found(self, xpath: str) -> bool:
        try:
            self.ui_helper.find_html_element_by_xpath(xpath)
            return True
        except ValueError:
            return False
