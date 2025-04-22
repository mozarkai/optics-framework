from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.engines.drivers.selenium_driver_manager import get_selenium_driver
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from optics_framework.engines.drivers.selenium_UI_helper import UIHelper
import time


class SeleniumPageSource(ElementSourceInterface):
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
            self.driver = get_selenium_driver()
        return self.driver

    def capture(self):
        """
        Capture the current screen state.

        Not Supported: This method is not implemented for Selenium Find Element.
        """
        internal_logger.exception('Appium Find Element does not support capturing the screen state.')
        raise NotImplementedError(
            'Appium Find Element does not support capturing the screen state.')



    def get_page_source(self) -> str:
        """
        Get the page source of the current page, parse it using BeautifulSoup,
        and write it to a human-readable HTML file for user reference.

        Returns:
            str: The raw page source.
        """
        return UIHelper.get_page_source()


    def locate(self, element: str, index: int = None) -> dict:
        """
        Locates an element on the current page using the Appium HTML source.

        Args:
            element (str): The element descriptor (text or XPath).
            index (int, optional): Index to return if multiple matches are found.
            strategy (str, optional): Reserved for future strategy types.

        Returns:
            dict: A dictionary with tag info if found, otherwise None.
        """
        element_type = utils.determine_element_type(element)

        try:
            if element_type == 'Image':
                internal_logger.debug('Appium does not support finding elements by image.')
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


    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> bool:
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
        if rule not in ["any", "all"]:
            raise ValueError("Invalid rule. Use 'any' or 'all'.")

        start_time = time.time()

        while time.time() - start_time < timeout:
            texts = [el for el in elements if utils.determine_element_type(el) == 'Text']
            xpaths = [el for el in elements if utils.determine_element_type(el) == 'XPath']

            # ----- Text Matching -----
            text_matches = []
            for text in texts:
                try:
                    self.ui_helper.find_html_element_by_text(text)
                    text_matches.append(text)
                    if rule == "any":
                        return True  # Found at least one
                except ValueError:
                    continue

            # ----- XPath Matching -----
            xpath_matches = []
            for xpath in xpaths:
                try:
                    self.ui_helper.find_html_element_by_xpath(xpath)
                    xpath_matches.append(xpath)
                    if rule == "any":
                        return True
                except ValueError:
                    continue

            # ----- Check Combined Match Result -----
            if rule == "all":
                if len(text_matches) == len(texts) and len(xpath_matches) == len(xpaths):
                    return True

            time.sleep(0.1)

        # ----- Timeout Handling -----
        if rule == "all":
            missing_texts = list(set(texts) - set(text_matches))
            missing_xpaths = list(set(xpaths) - set(xpath_matches))
            raise TimeoutError(f"Timeout reached: Not all elements were found.\nMissing texts: {missing_texts}\nMissing xpaths: {missing_xpaths}")

        if rule == "any":
            raise TimeoutError(f"Timeout reached: None of the specified elements were found: {elements}")
