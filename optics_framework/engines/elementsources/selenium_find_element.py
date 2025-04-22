from typing import Any, Tuple
from selenium.common.exceptions import NoSuchElementException
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger
from selenium.webdriver.common.by import By
from optics_framework.engines.drivers.selenium_driver_manager import get_selenium_driver
from optics_framework.engines.drivers.selenium_UI_helper import UIHelper
from optics_framework.common import utils
import time


class SeleniumFindElement(ElementSourceInterface):
    """
    Selenium Find Element Class
    """

    def __init__(self):
        """
        Initialize the Selenium Find Element Class.
        """
        self.driver = None
        self.tree = None
        self.root = None


    def _get_selenium_driver(self):
        if self.driver is None:
            self.driver = get_selenium_driver()
        return self.driver

    def capture(self) -> None:
        """
        Capture the current screen state.
        """
        internal_logger.exception('Selenium Find Element does not support capturing the screen state.')
        raise NotImplementedError('Selenium Find Element does not support capturing the screen state.')

    def get_page_source(self) -> str:
        """
        Get the page source of the current page.
        """
        return UIHelper.get_page_source()


    def locate(self, element: str, index: int = None) -> Any:
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
        element_type = utils.determine_element_type(element)
        driver = self._get_selenium_driver()

        if index is not None:
            raise ValueError('Selenium Find Element does not support locating elements using index.')

        try:
            if element_type == "Image":
                internal_logger.debug("Selenium does not support locating elements by image.")
                return None

            elif element_type == "XPath":
                internal_logger.debug(f"Locating by XPath: {element}")
                return driver.find_element(By.XPATH, element)

            elif element_type == "Text":
                # First try using text-based XPath
                try:
                    xpath = f"//*[contains(text(), '{element}')]"
                    internal_logger.debug(f"Trying text-based XPath: {xpath}")
                    return driver.find_element(By.XPATH, xpath)
                except NoSuchElementException:
                    # Fallback to ID if text fails
                    internal_logger.debug(f"Text not found, falling back to ID: {element}")
                    return driver.find_element(By.ID, element)

            elif element_type == "ID":
                internal_logger.debug(f"Locating by ID: {element}")
                return driver.find_element(By.ID, element)

        except NoSuchElementException:
            internal_logger.warning(f"Element not found: {element}")
            return None
        except Exception as e:
            internal_logger.error(f"Unexpected error locating element {element}: {e}")
            return None


    def assert_elements(self, elements, timeout=10, rule="any") -> None:
        """
        Assert that elements are present based on the specified rule using Selenium.

        Args:
            elements (list): List of element identifiers to locate (e.g., XPath strings).
            timeout (int): Maximum time to wait for elements in seconds (default: 10).
            rule (str): Rule to apply ("any" or "all").

        Returns:
            None: This method does not return a value.

        Raises:
            ValueError: If an invalid rule is provided.
            TimeoutError: If elements are not found based on the rule within the timeout.
        """
        if rule not in ["any", "all"]:
            raise ValueError("Invalid rule. Use 'any' or 'all'.")

        if self.driver is None:
            raise RuntimeError(
                "Selenium session not started. Call start_session() first.")

        start_time = time.time()
        found_elements = []  # Initialize found_elements to avoid unbound error

        while time.time() - start_time < timeout:
            found_elements = [self.locate(
                element) is not None for element in elements]

            if (rule == "all" and all(found_elements)) or (rule == "any" and any(found_elements)):
                internal_logger.debug(
                    f"Assertion passed with rule '{rule}' for elements: {elements}")
                return

            time.sleep(0.3)  # Polling interval

        # If timeout is reached, raise appropriate exception
        if rule == "all":
            missing_elements = [elem for elem, found in zip(
                elements, found_elements) if not found]
            internal_logger.error(
                f"Timeout reached: Elements not found: {missing_elements}")
            raise TimeoutError(
                f"Timeout reached: Elements not found: {missing_elements}")

        if rule == "any":
            internal_logger.error(
                f"Timeout reached: None of the elements were found: {elements}")
            raise TimeoutError(
                "Timeout reached: None of the specified elements were found.")

        return  # This should never be reached due to exceptions


    def locate_using_index(self, element: Any, index: int) -> Tuple[int, int] | None:
        raise NotImplementedError(
            'Selenium Find Element does not support locating elements using index.')
