from typing import Optional, Any, List
import numpy as np
import cv2

from playwright.sync_api import Page
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.async_utils import run_async


class PlaywrightScreenshot(ElementSourceInterface):
    """
    Capture screenshots using Playwright.
    """

    REQUIRED_DRIVER_TYPE = "playwright"

    page: Optional[Page]

    def __init__(self, driver: Optional[Any] = None):
        self.driver = driver
        self.page = None

    def _require_page(self):
        if self.driver is None or not hasattr(self.driver, "page"):
            raise RuntimeError(
                "Playwright driver is not initialized for PlaywrightScreenshot"
            )
        self.page = self.driver.page
        return self.page

    # --------------------------------------------------
    # Screenshot
    # --------------------------------------------------

    def capture(self) -> np.ndarray:
        """
        Capture a screenshot of the current page.

        Returns:
            np.ndarray: Screenshot as OpenCV-compatible NumPy array
        """
        return self.capture_screenshot_as_numpy()

    def capture_screenshot_as_numpy(self) -> np.ndarray:
        """
        Captures screenshot via Playwright and converts to NumPy image.

        Returns:
            numpy.ndarray: Screenshot image
        """
        page = self._require_page()
        try:
            internal_logger.debug("Capturing Playwright screenshot")

            # Playwright returns raw PNG bytes
            # Use run_async to handle async page.screenshot() if page is from async_api
            screenshot_bytes = run_async(page.screenshot(full_page=True))

            internal_logger.debug(
                "Playwright screenshot bytes length: %d",
                len(screenshot_bytes),
            )

            np_image = np.frombuffer(screenshot_bytes, np.uint8)
            np_image = cv2.imdecode(np_image, cv2.IMREAD_COLOR)  # type: ignore

            if np_image is None:
                raise RuntimeError("Failed to decode Playwright screenshot")

            return np_image

        except Exception as e:
            internal_logger.warning(
                f"Error capturing Playwright screenshot: {e}",
                exc_info=True,
            )
            raise RuntimeError(
                f"Error capturing Playwright screenshot: {e}"
            ) from e

    # --------------------------------------------------
    # Unsupported operations
    # --------------------------------------------------

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        internal_logger.exception(
            "PlaywrightScreenshot does not support getting interactive elements."
        )
        raise NotImplementedError(
            "PlaywrightScreenshot does not support getting interactive elements."
        )

    def assert_elements(self, elements, timeout=30, rule="any") -> None:
        internal_logger.exception(
            "PlaywrightScreenshot does not support asserting elements."
        )
        raise NotImplementedError(
            "PlaywrightScreenshot does not support asserting elements."
        )

    def locate(self, element, index=None) -> tuple:
        internal_logger.exception(
            "PlaywrightScreenshot does not support locating elements."
        )
        raise NotImplementedError(
            "PlaywrightScreenshot does not support locating elements."
        )

    def locate_using_index(self):
        internal_logger.exception(
            "PlaywrightScreenshot does not support locating elements using index."
        )
        raise NotImplementedError(
            "PlaywrightScreenshot does not support locating elements using index."
        )
