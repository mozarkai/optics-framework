from typing import Any, Optional, List, Tuple
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from optics_framework.engines.drivers import selenium_locator

NOT_INITIALISED_MSG = "Selenium driver is not initialized for SeleniumFindElement."

class SeleniumFindElement(ElementSourceInterface):
    """
    Selenium Find Element Class
    """

    REQUIRED_DRIVER_TYPE = "selenium"

    def __init__(self, driver: Any = None):
        """
        Initialize the Selenium Find Element Class.
        Args:
            driver: The Selenium driver instance (should be passed explicitly).
        """
        self.driver = driver
        self.tree = None
        self.root = None

    def capture(self) -> None:
        """
        Capture the current screen state.
        """
        msg = 'Selenium Find Element does not support capturing the screen state.'
        internal_logger.exception(msg)
        raise NotImplementedError(msg)

    def get_page_source(self) -> Tuple[str, str]:
        """
        Get the page source of the current page.
        Returns:
            Tuple[str, str]: (page_source, timestamp)
        """
        if self.driver is None:
            internal_logger.error(NOT_INITIALISED_MSG)
            raise RuntimeError(NOT_INITIALISED_MSG)
        page_source = self.driver.require_webdriver().page_source
        timestamp = utils.get_timestamp()
        return str(page_source), str(timestamp)

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        msg = "Getting interactive elements is not yet supported using Selenium Find Element."
        internal_logger.exception(msg)
        raise NotImplementedError(msg)

    def locate(self, element: str, index: Optional[int] = None) -> Any:
        if self.driver is None:
            internal_logger.error(NOT_INITIALISED_MSG)
            raise RuntimeError(NOT_INITIALISED_MSG)
        return selenium_locator.find(self.driver, element, index or 0)

    def get_element_bboxes(
        self, elements: list
    ) -> List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]]:
        """Return bounding boxes for each element using WebElement location and size."""
        return utils.bboxes_from_webelements(self.locate, elements)

    def get_bbox_for_element(
        self, element: Any
    ) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """Return bounding box for an already-located WebElement."""
        return utils.bbox_from_webelement_like(element)

    def assert_elements(self, elements: list, timeout: int = 10, rule: str = "any") -> None:
        selenium_locator.wait_for(self.driver, elements, timeout, rule)

    def assert_elements_visible(self, elements: list, timeout: int = 10, rule: str = "any") -> None:
        selenium_locator.wait_for(self.driver, elements, timeout, rule, visible=True)

    def check_elements(self, elements: list[str], visible: bool = False) -> list[bool]:
        return selenium_locator.check(self.driver, elements, visible)

    def locate_using_index(self) -> None:
        msg = 'Selenium Find Element does not support locating elements using index.'
        internal_logger.error(msg)
        raise NotImplementedError(msg)
