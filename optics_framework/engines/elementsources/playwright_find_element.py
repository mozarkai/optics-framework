import time
from typing import Optional, Any, List, Tuple

from optics_framework.common.logging_config import internal_logger
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common import utils
from optics_framework.common.async_utils import run_async
from optics_framework.engines.drivers import playwright_locator


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

    def _strict_element_match(self) -> bool:
        try:
            return bool(self.driver.event_sdk.config_handler.config.strict_element_match)
        except AttributeError:
            return False

    def _element_timeout_s(self) -> float:
        timeout = getattr(self.driver, "element_timeout_s", None)
        return timeout() if callable(timeout) else 0.0

    def locate(self, element: str, index: Optional[int] = None) -> Any:
        """Locate a visible element, waiting up to the driver's element timeout."""
        page = self._require_page()
        if utils.determine_element_type(element) == "Image":
            return None
        try:
            return playwright_locator.find(
                page,
                element,
                index=int(index or 0),
                exact=self._strict_element_match(),
                timeout_s=self._element_timeout_s(),
            )
        except Exception as e:
            internal_logger.error(f"Error locating element: {element}", exc_info=True)
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
        page = self._require_page()
        for element in elements:
            try:
                handle = playwright_locator.find(page, element, exact=self._strict_element_match())
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

    def assert_elements(
        self,
        elements: list[str],
        timeout: int = 10,
        rule: str = "any",
    ):
        return self._poll_until(elements, timeout, rule, playwright_locator.is_present)

    def assert_elements_visible(
        self,
        elements: list[str],
        timeout: int = 10,
        rule: str = "any",
    ):
        return self._poll_until(elements, timeout, rule, playwright_locator.is_visible)

    def _poll_until(self, elements, timeout, rule, check):
        if rule not in ("any", "all"):
            raise OpticsError(Code.E0403, message="Invalid rule. Use 'any' or 'all'")
        if isinstance(elements, str):
            elements = [elements]
        page = self._require_page()
        exact = self._strict_element_match()
        deadline = time.monotonic() + float(timeout)
        while True:
            try:
                found = [check(page, element, exact=exact) for element in elements]
            except Exception as e:
                raise OpticsError(
                    Code.E0401, message=f"Error during element assertion: {e}"
                ) from e
            done = any(found) if rule == "any" else all(found)
            if done:
                return True, utils.get_timestamp()
            if time.monotonic() >= deadline:
                break
            time.sleep(0.3)
        internal_logger.warning(
            "[PlaywrightFindElement] Timeout reached. rule=%s elements=%s", rule, elements
        )
        raise TimeoutError(
            f"Timeout reached: Elements not found based on rule '{rule}': {elements}"
        )
