from typing import Any, Tuple
from selenium.common.exceptions import NoSuchElementException
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import logger
from selenium.webdriver.common.by import By
from optics_framework.engines.drivers.selenium_driver_manager import get_selenium_driver
from lxml import etree
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
        logger.exception('Selenium Find Element does not support capturing the screen state.')
        raise NotImplementedError('Selenium Find Element does not support capturing the screen state.')

    def get_page_source(self) -> str:
        """
        Get the page source of the current page.
        """
        driver = self._get_selenium_driver()
        page_source = driver.page_source
        parser = etree.XMLParser()
        self.tree = etree.ElementTree(etree.fromstring(page_source.encode('utf-8'), parser=parser))
        self.root = self.tree.getroot()
        return page_source

    def locate(self, element: str):
        """
        Find the specified element on the current webpage.

        Args:
            element: The element identifier to find on the page (XPath, ID, or text).

        Returns:
            WebElement: The located element if found, None otherwise.
        """
        driver = self._get_selenium_driver()
        element_type = utils.determine_element_type(element)

        if element_type == 'Image':
            # Selenium doesn't natively support finding elements by image
            logger.debug("Selenium does not support finding elements by image")
            return None
        elif element_type == 'XPath':
            try:
                found_element = driver.find_element(By.XPATH, element)
                if not found_element:
                    return None
                return found_element
            except NoSuchElementException as e:
                logger.error(f"Error finding element by XPath: {element}: {e}")
                return None
            except Exception as e:
                logger.error(
                    f"Unexpected error finding element by XPath: {element}: {e}")
                return None
        elif element_type == 'Text':
            try:
                # Using ID as a proxy for text-based search in Selenium
                found_element = driver.find_element(By.ID, element)
                if not found_element:
                    return None
                return found_element
            except NoSuchElementException as e:
                logger.error(f"Error finding element by ID: {element}: {e}")
                return None
            except Exception as e:
                logger.error(
                    f"Unexpected error finding element by ID: {element}: {e}")
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
                logger.debug(
                    f"Assertion passed with rule '{rule}' for elements: {elements}")
                return

            time.sleep(0.3)  # Polling interval

        # If timeout is reached, raise appropriate exception
        if rule == "all":
            missing_elements = [elem for elem, found in zip(
                elements, found_elements) if not found]
            logger.error(
                f"Timeout reached: Elements not found: {missing_elements}")
            raise TimeoutError(
                f"Timeout reached: Elements not found: {missing_elements}")

        if rule == "any":
            logger.error(
                f"Timeout reached: None of the elements were found: {elements}")
            raise TimeoutError(
                "Timeout reached: None of the specified elements were found.")

        return  # This should never be reached due to exceptions


    def locate_using_index(self, element: Any, index: int) -> Tuple[int, int] | None:
        raise NotImplementedError(
            'Selenium Find Element does not support locating elements using index.')
