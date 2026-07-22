import time
from typing import Optional, Any, List, Tuple, Callable
from appium.webdriver.webdriver import WebDriver
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import WebDriverException
from lxml import etree # type: ignore
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.error import OpticsError, Code
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
            raise OpticsError(Code.E0101, message="Appium driver is not initialized for AppiumFindElement.")
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


    def get_page_source(self) -> Tuple[str, str]:
        """
        Get the page source of the current page.
        Returns:
            Tuple[str, str]: (page_source, timestamp)
        """
        # Fetch the current UI tree (page source) from the Appium driver.
        driver = self._require_driver()
        page_source = driver.page_source
        timestamp = utils.get_timestamp()
        self.tree = etree.ElementTree(etree.fromstring(page_source.encode('utf-8')))
        if self.tree is not None:
            self.root = self.tree.getroot()
        else:
            self.root = None
        return str(page_source), str(timestamp)

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None) -> List[Any]:
        """
        Get all interactive elements from the current page source.

        Args:
            filter_config: Optional list of filter types (not used for this implementation).

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
        driver = self._require_driver()
        element_type = utils.determine_element_type(element)

        if element_type == 'Image':
            return None
        elif element_type == 'XPath':
            try:
                found_element = driver.find_element(AppiumBy.XPATH, element)
                if not found_element:
                    return None
                return found_element
            except (AttributeError, TypeError) as e:
                internal_logger.error('Error finding element: %s', element, exc_info=True)
                raise OpticsError(Code.E0201, message=f"Element of type {element_type} not found using: {element}", cause=e) from e
        elif element_type == 'Text':
            locator = element[len("text="):] if element.lower().startswith("text=") else element
            try:
                found_element = driver.find_element(AppiumBy.ACCESSIBILITY_ID, locator)
                return found_element
            except Exception as e:
                internal_logger.exception(f" element: {element}", exc_info=e)
                raise OpticsError(Code.E0201, message=f"Element of type {element_type} not found using: {element}", cause=e) from e
        elif element_type == 'Class':
            try:
                found_elements = driver.find_elements(AppiumBy.CLASS_NAME, element)

                index_to_use = 0 if index is None else index
                if not found_elements or index_to_use < 0 or index_to_use >= len(found_elements):
                    raise OpticsError(
                        Code.E0201,
                        message=f"Element of type {element_type} and index {index_to_use} not found using: {element}",
                    )

                found_element = found_elements[index_to_use]
                return found_element
            except Exception as e:
                internal_logger.exception(f" element: {element}", exc_info=e)
                raise OpticsError(Code.E0201, message=f"Element of type {element_type} not found using: {element}", cause=e) from e
        return None

    def get_element_bboxes(
        self, elements: List[str]
    ) -> List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]]:
        """
        Return bounding boxes for each element using WebElement location and size.
        Compatible with Android (UIAutomator2) and iOS (XCUITest); both expose
        W3C location/size/rect on the WebElement.
        """
        return utils.bboxes_from_webelements(self.locate, elements)

    def get_bbox_for_element(
        self, element: Any
    ) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """
        Return bounding box for an already-located Appium WebElement.
        Uses location/size or rect first (Android and iOS); falls back to
        get_attribute("bounds") (Android) or get_attribute("rect") (iOS).
        """
        bbox = utils.bbox_from_webelement_like(element)
        if bbox is not None:
            return bbox
        return utils.bbox_from_appium_attribute_fallback(element)

    def _assert_elements_one_pass(
        self, elements: List[str], found: dict, rule: str, check_fn: Callable[[str], bool]
    ) -> Optional[Tuple[bool, Any]]:
        """Run one pass over elements using check_fn; return (True, timestamp) if rule is satisfied, else None."""
        for el in elements:
            if not found[el] and check_fn(el):
                found[el] = True
                if rule == "any":
                    return (True, utils.get_timestamp())
        if rule == "all" and all(found.values()):
            return (True, utils.get_timestamp())
        return None

    def _assert_elements_common(
        self, elements: List[str], timeout: int, rule: str,
        check_fn: Callable[[str], bool], error_desc: str, not_found_desc: str,
    ):
        """Shared polling loop backing assert_elements/assert_elements_visible.

        Only the per-element check (presence vs. visibility) and error text differ.
        """
        if rule not in ["any", "all"]:
            raise OpticsError(Code.E0403, message="Invalid rule. Use 'any' or 'all'.")

        start_time = time.time()
        found = dict.fromkeys(elements, False)

        while time.time() - start_time < timeout:
            try:
                result = self._assert_elements_one_pass(elements, found, rule, check_fn)
                if result is not None:
                    return result
            except Exception as e:
                raise OpticsError(Code.E0401, message=f"Error during {error_desc}: {e}") from e
        internal_logger.warning(f"Timeout reached. Rule: {rule}, Elements: {elements}")
        raise TimeoutError(
            f"Timeout reached: {not_found_desc} based on rule '{rule}': {elements}"
        )

    def assert_elements(self, elements: List[str], timeout: int = 10, rule: str = "any"):
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
        return self._assert_elements_common(
            elements, timeout, rule, self.locate,
            error_desc="element assertion", not_found_desc="Elements not found",
        )

    def _is_within_screen_bounds(self, located: Any) -> bool:
        """True if `located`'s bounding box intersects the current screen/window bounds.

        `.is_displayed()` alone doesn't check this on Appium (confirmed on both Android and
        iOS: an element scrolled well off-screen still reports displayed=True), the same class
        of gap Selenium's `is_displayed()` has -- closed there via a getBoundingClientRect() vs.
        viewport check. This is the Appium equivalent, using bounds/window-size instead of JS.
        """
        try:
            bbox = self.get_bbox_for_element(located)
            if bbox is None:
                return False
            (x1, y1), (x2, y2) = bbox
            window_size = self._require_driver().get_window_size()
            win_w, win_h = window_size["width"], window_size["height"]
            return x1 < win_w and x2 > 0 and y1 < win_h and y2 > 0
        except (OpticsError, WebDriverException):
            return False

    def _is_located_and_displayed(self, element: str) -> bool:
        """True only if `element` both locates, is currently displayed, and is within the
        current screen bounds.

        `locate()` alone only proves the element exists in the accessibility tree --
        Appium's find_element does not filter on visibility, so an element scrolled
        off-screen (but still present in the hierarchy dump) locates successfully too.
        `.is_displayed()` alone isn't enough either -- it doesn't account for scroll position,
        so it's paired with `_is_within_screen_bounds` below.
        """
        try:
            located = self.locate(element)
            return bool(
                located is not None
                and located.is_displayed()
                and self._is_within_screen_bounds(located)
            )
        except (OpticsError, WebDriverException):
            return False

    def assert_elements_visible(self, elements: List[str], timeout: int = 10, rule: str = "any"):
        """
        Assert that elements are currently displayed on screen (not merely present in the
        accessibility tree) based on the specified rule.

        Works identically for Android (UiAutomator2) and iOS (WebDriverAgent) -- both pair
        WebElement.is_displayed() with a bounding-box-vs-window-size intersection check, since
        is_displayed() alone doesn't account for scroll position on either platform.

        Raises:
            TimeoutError: If elements are not visible based on the rule within the timeout.
        """
        return self._assert_elements_common(
            elements, timeout, rule, self._is_located_and_displayed,
            error_desc="element visibility assertion", not_found_desc="Elements not visible",
        )
