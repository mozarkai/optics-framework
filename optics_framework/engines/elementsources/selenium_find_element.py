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

    def get_interactive_elements(self):

        internal_logger.exception("Getting interactive elements is not yet suppored using Selenium Find Element.")
        raise NotImplementedError(
            "Getting interactive elements is not yet suppored using Selenium Find Element."
        )

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
                    # Fallback to other strategies if text fails
                    internal_logger.debug(f"Text not found, falling back to ID: {element}")
                    return self._find_element_by_any(element)
        except NoSuchElementException:
            internal_logger.warning(f"Element not found: {element}")
            return None
        except Exception as e:
            internal_logger.error(f"Unexpected error locating element {element}: {e}")
            return None

    def _find_element_by_any(self, locator_value: str):
        """
        Try locating an element using all known Selenium strategies.
        """
        driver = self._get_selenium_driver()
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
                return driver.find_element(strategy, value)
            except NoSuchElementException:
                continue
        internal_logger.warning(f"No matching element found using any strategy for: {locator_value}")
        return None


    def assert_elements(self, elements, timeout=10, rule="any") -> Tuple[bool, str]:
        """
        Assert that elements are present based on the specified rule using Selenium.

        Args:
            elements (list): List of element identifiers to locate (e.g., XPath strings).
            timeout (int): Maximum time to wait for elements in seconds (default: 10).
            rule (str): Rule to apply ("any" or "all").

        Returns:
            Tuple (bool, timestamp) if elements are found before timeout.

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
                timestamp = utils.get_timestamp()
                internal_logger.debug(
                    f"Assertion passed with rule '{rule}' for elements: {elements}")
                return True,timestamp

            time.sleep(0.3)  # Polling interval
        raise TimeoutError(
            "Timeout reached: None of the specified elements were found.")


    def locate_using_index(self, element: Any, index: int) -> Tuple[int, int] | None:
        raise NotImplementedError(
            'Selenium Find Element does not support locating elements using index.')
