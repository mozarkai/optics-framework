import time
from typing import Optional, Any, List

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

    def get_page_source(self) -> str:
        """
        Returns current DOM HTML
        """
        page = self._require_page()
        return run_async(page.content())

    def get_interactive_elements(
            self,
            filter_config: Optional[List[str]] = None
    ) -> List[Any]:
        """
        Not supported for PlaywrightFindElement.

        Design rationale:
        - PlaywrightFindElement is optimized for direct element resolution
          using known selectors (CSS, XPath, text).
        - It intentionally avoids full DOM traversal, bounding-box
          calculations, and semantic filtering.
        - Interactive element discovery is delegated to PlaywrightPageSource,
          which is designed specifically for DOM introspection and tooling use cases.

        Contract compliance:
        - This method exists to satisfy ElementSourceInterface.
        - The parameter `filter_config` is accepted for signature compatibility,
          but is not used.

        Usage guidance:
        - Use PlaywrightPageSource.get_interactive_elements() when element
          discovery or inspection is required.
        - Use PlaywrightFindElement.locate() for execution-time interactions.
        """
        internal_logger.exception(
            "PlaywrightFindElement does not support get_interactive_elements()"
        )
        return ["PlaywrightFindElement does not support get_interactive_elements()"]

    # --------------------------------------------------
    # Element location
    # --------------------------------------------------

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
            if element_type == "Image":
                return None

            # XPath
            if element_type == "XPath":
                locator = page.locator(f"xpath={element}")

            # Text
            elif element_type == "Text":
                locator = page.get_by_text(element, exact=False)

            # CSS / default
            else:
                locator = page.locator(element)

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

    # --------------------------------------------------
    # Assertions
    # --------------------------------------------------

    def assert_elements(
            self,
            elements: List[str],
            timeout: int = 10,
            rule: str = "any",
    ):
        """
        Assert presence of elements.

        rule:
        - any: return True if any found
        - all: return True only if all found
        """
        self._validate_assert_rule(rule)

        try:
            self._require_page()
        except OpticsError:
            return False, utils.get_timestamp()

        found = dict.fromkeys(elements, False)
        success = self._poll_for_elements(elements, found, timeout, rule)

        return success, utils.get_timestamp()

    @staticmethod
    def _validate_assert_rule(rule: str) -> None:
        if rule not in ("any", "all"):
            raise OpticsError(
                Code.E0403,
                message="Invalid rule. Use 'any' or 'all'",
            )

    def _poll_for_elements(
            self,
            elements: List[str],
            found: dict,
            timeout: int,
            rule: str,
    ) -> bool:
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                if self._evaluate_elements(elements, found, rule):
                    return True
                time.sleep(0.5)
            except Exception as e:
                raise OpticsError(
                    Code.E0401,
                    message=f"Error during element assertion: {e}",
                ) from e

        return False

    def _evaluate_elements(
        self,
        elements: List[str],
        found: dict,
        rule: str,
    ) -> bool:
        for el in elements:
            if not found[el] and self.locate(el):
                found[el] = True
                if rule == "any":
                    return True

        return rule == "all" and all(found.values())
