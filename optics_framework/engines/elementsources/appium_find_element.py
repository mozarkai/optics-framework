import time
from typing import Optional, Any, List
from appium.webdriver.webdriver import WebDriver
from appium.webdriver.common.appiumby import AppiumBy
from lxml import etree # type: ignore
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common import utils


class AppiumFindElement(ElementSourceInterface):
    REQUIRED_DRIVER_TYPE = "appium"
    """
    Appium Find Element Class
    """

    driver: Optional[Any]  # Can be Appium WebDriver or Appium wrapper
    tree: Optional[Any]
    root: Optional[Any]

    def __init__(self, driver: Optional[Any] = None):
        """
        Initialize the Appium Find Element Class.
        Args:
            driver: The Appium driver instance (should be passed explicitly).
            config: Optional config dictionary for extensibility.
        """
        self.driver = driver
        self.tree = None
        self.root = None

    def _require_driver(self) -> WebDriver:
        if self.driver is None:
            internal_logger.error("Appium driver is not initialized for AppiumFindElement.")
            raise RuntimeError("Appium driver is not initialized for AppiumFindElement.")
        if hasattr(self.driver, "driver"):
            return self.driver.driver
        return self.driver

    def capture(self) -> None:
        """
        Capture the current screen state.

        Returns:
            None
        """
        internal_logger.exception('Appium Find Element does not support capturing the screen state.')
        raise NotImplementedError('Appium Find Element does not support capturing the screen state.')


    def get_page_source(self) -> str:
        """
        Get the page source of the current page.
        Returns:
            str: The page source.
        """
        # Fetch the current UI tree (page source) from the Appium driver.
        driver = self._require_driver()
        page_source = driver.page_source
        self.tree = etree.ElementTree(etree.fromstring(page_source.encode('utf-8')))
        if self.tree is not None:
            self.root = self.tree.getroot()
        else:
            self.root = None
        return page_source

    def get_interactive_elements(self) -> List[Any]:
        """
        Get all interactive elements from the current page source.

        Returns:
            list: A list of interactive elements (buttons, links, etc.) found in the page source.
        """
        internal_logger.exception(
            'Appium Find Element does not support getting interactive elements. Please use AppiumPageSource for this functionality.'
        )
        raise NotImplementedError(
            'Appium Find Element does not support getting interactive elements. Please use AppiumPageSource for this functionality.'
        )


    def locate(self, element: str, index: Optional[int] = None) -> Any:
        """
        Find the specified element on the current page.

        Args:
            element: The element to find on the page.

        Returns:
            The found element object if found, None otherwise.
        """
        """
        Find the specified element on the current page using the Appium driver.
        """
        driver = self._require_driver()
        element_type = utils.determine_element_type(element)

        if index is not None:
            raise ValueError('Appium Find Element does not support locating elements using index.')

        if element_type == 'Image':
            return None
        elif element_type == 'XPath':
            try:
                found_element = driver.find_element(AppiumBy.XPATH, element)
                if not found_element:
                    return None
                return found_element
            except (AttributeError, TypeError):
                internal_logger.error('Error finding element: %s', element, exc_info=True)
                return None
        elif element_type == 'Text':
            try:
                found_element = driver.find_element(AppiumBy.ACCESSIBILITY_ID, element)
            except (AttributeError, TypeError):
                internal_logger.exception('Element: %s', element, exc_info=True)
                return None
            return found_element
        return None

    def assert_elements(self, elements: List[str], timeout: int = 10, rule: str = "any") -> None:
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
                    found[el] = True
                    if rule == "any":
                        return
            if rule == "all" and all(found.values()):
                return

        missing_elements = [el for el, ok in found.items() if not ok]
        internal_logger.error(
            "Elements not found based on rule '%s': %s", rule, missing_elements
        )
        raise TimeoutError(
            f"Timeout reached: Elements not found based on rule '{rule}': {elements}"
        )
