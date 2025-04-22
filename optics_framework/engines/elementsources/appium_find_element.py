from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.engines.drivers.appium_driver_manager import get_appium_driver
from appium.webdriver.common.appiumby import AppiumBy
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
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
        if self.driver is None:
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

    def assert_elements(self, elements, timeout=10, rule="any") -> bool:
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

        while time.time() - start_time < timeout:
            found_elements = [self.locate(element) for element in elements]

            if (rule == "all" and all(found_elements)) or (rule == "any" and any(found_elements)):
                return True

            time.sleep(0.3)  # Wait before retrying

        # If timeout is reached and conditions are not met, raise an exception
        if rule == "all":
            missing_elements = [elem for elem, found in zip(elements, found_elements) if not found]
            raise TimeoutError(f"Timeout reached: Elements not found: {missing_elements}")

        if rule == "any":
            raise TimeoutError("Timeout reached: None of the specified elements were found.")

        return False  # This should never be reached due to exceptions
