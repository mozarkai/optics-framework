import time
from typing import Optional, Any, List, Tuple

from playwright.sync_api import TimeoutError as PWTimeout
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common import utils
from optics_framework.common.async_utils import run_async


class PlaywrightFindElement(ElementSourceInterface):
    REQUIRED_DRIVER_TYPE = "playwright"

    def __init__(self, driver: Optional[Any] = None):
        self.driver = driver
        self.page = None

    def _require_page(self):
        if self.driver is None or not hasattr(self.driver, "page"):
            raise OpticsError(
                Code.E0101,
                message="Playwright driver is not initialized for PlaywrightFindElement",
            )
        self.page = self.driver.page
        return self.page

    # --------------------------------------------------
    # Screenshot / page source
    # --------------------------------------------------

    def capture(self) -> None:
        """
        Playwright element source does not capture screenshots.
        """
        internal_logger.exception(
            "PlaywrightFindElement does not support capture()"
        )
        raise NotImplementedError(
            "PlaywrightFindElement does not support capture()"
        )

    def get_page_source(self) -> Tuple[str, str]:
        """
        Returns current DOM HTML and timestamp.
        Returns:
            Tuple[str, str]: (page_source, timestamp)
        """
        page = self._require_page()
        html = run_async(page.content())
        timestamp = utils.get_timestamp()
        return str(html), str(timestamp)

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None) -> List[Any]:
        """
        Not supported (use Playwright native locators instead)

        Args:
            filter_config: Optional list of filter types (not used for this implementation).
        """
        internal_logger.exception(
            "PlaywrightFindElement does not support get_interactive_elements()"
        )
        raise NotImplementedError(
            "PlaywrightFindElement does not support get_interactive_elements()"
        )

    # --------------------------------------------------
    # Element location
    # --------------------------------------------------

    def _strip_prefix(self, element: str, prefix: str) -> str:
        """
        Strip prefix from element string if present (case-insensitive).

        :param element: Element string
        :param prefix: Prefix to strip (e.g., "xpath=", "text=", "css=")
        :return: Element string without prefix
        """
        if element.lower().startswith(prefix.lower()):
            eq_index = element.find("=")
            return element[eq_index + 1:] if eq_index >= 0 else element
        return element

    def _build_locator(self, page: Any, element: str, element_type: str) -> Optional[Any]:
        """
        Build Playwright locator based on element type.

        :param page: Playwright page object
        :param element: Element selector string
        :param element_type: Type of element (XPath, Text, CSS, Image)
        :return: Playwright locator or None
        """
        if element_type == "Image":
            return None

        if element_type == "XPath":
            xpath_value = self._strip_prefix(element, "xpath=")
            return page.locator(f"xpath={xpath_value}")

        if element_type == "Text":
            text_value = self._strip_prefix(element, "text=")
            return page.get_by_text(text_value, exact=False)

        # CSS / default
        css_value = self._strip_prefix(element, "css=")
        return page.locator(css_value)

    def locate(self, element: str, index: Optional[int] = None) -> Any:
        """
        Locate an element using Playwright selectors

        Supports:
        - CSS
        - text=Exact / Partial
        - XPath (via //)
        """

        page = self._require_page()

        if index is not None:
            raise OpticsError(
                Code.E0202,
                message="PlaywrightFindElement does not support index-based locating",
            )

        element_type = utils.determine_element_type(element)

        try:
            locator = self._build_locator(page, element, element_type)
            if locator is None:
                return None

            # Use run_async to await async Playwright methods
            count = run_async(locator.count())
            if count == 0:
                return None

            run_async(locator.first.wait_for(state="visible", timeout=3000))
            return locator.first

        except PWTimeout:
            return None

        except Exception as e:
            internal_logger.error(
                f"Error locating element: {element}", exc_info=True
            )
            raise OpticsError(
                Code.E0201,
                message=f"Error locating element: {element}",
                cause=e,
            ) from e

    def get_element_bboxes(
        self, elements: List[str]
    ) -> List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]]:
        """Return bounding boxes for each element using Playwright bounding_box()."""
        result: List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]] = []
        for element in elements:
            try:
                handle = self.locate(element)
            except Exception:
                result.append(None)
                continue
            result.append(self.get_bbox_for_element(handle))
        return result

    def get_bbox_for_element(
        self, element: Any
    ) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """Return bounding box for an already-located Playwright element handle."""
        if element is None:
            return None
        try:
            bbox = run_async(element.bounding_box())
            if bbox is not None:
                x1 = int(bbox.get("x", 0))
                y1 = int(bbox.get("y", 0))
                x2 = int(x1 + bbox.get("width", 0))
                y2 = int(y1 + bbox.get("height", 0))
                return ((x1, y1), (x2, y2))
        except (TypeError, ValueError, AttributeError):
            pass
        return None

    # --------------------------------------------------
    # Assertions
    # --------------------------------------------------

    def _check_element_found(self, element: str, found: dict) -> bool:
        """
        Check if a single element is found and update found dict.

        :param element: Element to check
        :param found: Dictionary tracking found elements
        :return: True if element was found (and not previously found)
        """
        if not found[element] and self.locate(element):
            found[element] = True
            return True
        return False

    def _check_assertion_complete(self, rule: str, found: dict) -> bool:
        """
        Check if assertion is complete based on rule.

        :param rule: Assertion rule ("any" or "all")
        :param found: Dictionary tracking found elements
        :return: True if assertion is complete
        """
        if rule == "any":
            return any(found.values())
        return all(found.values())

    def assert_elements(
        self,
        elements: List[str],
        timeout: int = 10,
        rule: str = "any",
    ):
        """
        Assert presence of elements

        rule:
        - any: return True if any found
        - all: return True only if all found
        """

        if rule not in ("any", "all"):
            raise OpticsError(
                Code.E0403,
                message="Invalid rule. Use 'any' or 'all'",
            )

        # Ensure driver is initialized before entering the loop (OpticsError propagates if not)
        self._require_page()

        start_time = time.time()
        found = dict.fromkeys(elements, False)

        while time.time() - start_time < timeout:
            try:
                for el in elements:
                    if self._check_element_found(el, found) and rule == "any":
                        return True, utils.get_timestamp()

                if self._check_assertion_complete(rule, found):
                    return True, utils.get_timestamp()

                time.sleep(0.5)

            except Exception as e:
                raise OpticsError(
                    Code.E0401,
                    message=f"Error during element assertion: {e}",
                ) from e

        internal_logger.warning(
            "[PlaywrightFindElement] Timeout reached. rule=%s elements=%s",
            rule, elements
        )
        raise TimeoutError(
            f"Timeout reached: Elements not found based on rule '{rule}': {elements}"
        )

    def assert_elements_visible(
        self,
        elements: List[str],
        timeout: int = 10,
        rule: str = "any",
    ):
        """
        Not yet enabled pending live verification. In principle presence here already
        implies visibility -- locate() (used by assert_elements) waits for state="visible"
        before returning, so no separate on-screen check should be needed -- but that
        assumption hasn't been confirmed live yet. The call below is kept in place,
        unreachable, to switch back to once verified.
        """
        raise NotImplementedError(
            "PlaywrightFindElement does not support assert_elements_visible yet."
        )
        return self.assert_elements(elements, timeout, rule)
