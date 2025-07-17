from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.engines.drivers.appium_driver_manager import get_appium_driver
from appium.webdriver.common.appiumby import AppiumBy
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from typing import Tuple
from lxml import etree
import time


class AppiumFindElement(ElementSourceInterface):
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
        self.tree = None
        self.root = None

    def _get_appium_driver(self):
        self.driver = get_appium_driver()
        return self.driver

    def capture(self) -> None:
        """
        Capture the current screen state.

        Returns:
            None
        """
        """
        Capture the current screen state using the Appium driver.
        """
        internal_logger.exception('Appium Find Element does not support capturing the screen state.')
        raise NotImplementedError('Appium Find Element does not support capturing the screen state.')


    def get_page_source(self) -> str:
        """
        Get the page source of the current page.

        Returns:
            str: The page source.
        """
        """
        Fetch the current UI tree (page source) from the Appium driver.
        """

        driver = self._get_appium_driver()
        page_source = driver.page_source

        self.tree = etree.ElementTree(etree.fromstring(page_source.encode('utf-8')))
        self.root = self.tree.getroot()
        return page_source

    def get_interactive_elements(self):
        """
        Get all interactive elements from the current page source.

        Returns:
            list: A list of interactive elements (buttons, links, etc.) found in the page source.
        """
        internal_logger.exception('Appium Find Element does not support getting interactive elements. Please use AppiumPageSource for this functionality.')
        raise NotImplementedError('Appium Find Element does not support getting interactive elements. Please use AppiumPageSource for this functionality.')


    def locate(self, element: str, index = None):
        """
        Find the specified element on the current page.

        Args:
            element: The element to find on the page.

        Returns:
            bool: True if the element is found, None otherwise.
        """
        """
        Find the specified element on the current page using the Appium driver.
        """
        driver = self._get_appium_driver()
        element_type = utils.determine_element_type(element)

        if index is not None:
            raise ValueError('Appium Find Element does not support locating elements using index.')

        if element_type == 'Image':
            # Find the element by image
            # internal_logger.debug(f'Appium Find Element does not support finding images.')
            return None
        elif element_type == 'XPath':
            try:
                element = driver.find_element(AppiumBy.XPATH, element)
                if not element:
                    return None
                return element
            except Exception as e:
                internal_logger.error(f'Error finding element: {element}', exc_info=e)
                return None
        elif element_type == 'Text':
            try:
                element = driver.find_element(AppiumBy.ACCESSIBILITY_ID, element)
            except Exception as e:
                internal_logger.exception(f' element: {element}', exc_info=e)
                raise Exception(f'Element not found: {element}')
                return None
            return element

    def assert_elements(self, elements, timeout=10, rule="any") -> Tuple[bool, str|None]:
        """
        Assert that elements are present based on the specified rule.

        Args:
            elements (list): List of elements to locate.
            timeout (int): Maximum time to wait for elements.
            rule (str): Rule to apply ("any" or "all").
            polling_interval (float): Interval between retries in seconds.

        Returns:
            bool: True if the assertion passes.

        Raises:
            Exception: If elements are not found based on the rule within the timeout.
        """
        if rule not in ["any", "all"]:
            raise ValueError("Invalid rule. Use 'any' or 'all'.")

        start_time = time.time()
        found = dict.fromkeys(elements, False)

        while time.time() - start_time < timeout:
            for el in elements:
                if not found[el] and self.locate(el):
                    timestamp = utils.get_timestamp()
                    found[el] = True
                    if rule == "any":
                        return True, timestamp
            if rule == "all" and all(found.values()):
                return True, utils.get_timestamp()

        missing_elements = [el for el, ok in found.items() if not ok]
        internal_logger.error(
            f"Elements not found based on rule '{rule}': {missing_elements}"
        )
        raise TimeoutError(
            f"Timeout reached: Elements not found based on rule '{rule}': {elements}"
        )
