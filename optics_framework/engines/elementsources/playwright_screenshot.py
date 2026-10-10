import asyncio
from typing import Optional, Any, List
import numpy as np
import cv2

from playwright.async_api import Error as PlaywrightError
from playwright.sync_api import Page
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.async_utils import run_async


# Headed Chromium under a virtual display (xvfb on CI runners) intermittently fails
# Page.captureScreenshot with this message while the page is loaded and visible;
# the same call succeeds a moment later.
_TRANSIENT_CAPTURE_ERROR = "Unable to capture screenshot"
_CAPTURE_RETRY_DELAYS_S = (0.1, 0.3)


async def _screenshot_viewport(page) -> bytes:
    for delay in _CAPTURE_RETRY_DELAYS_S:
        try:
            return await page.screenshot(full_page=False)
        except PlaywrightError as e:
            if _TRANSIENT_CAPTURE_ERROR not in str(e):
                raise
            internal_logger.debug(
                "Playwright screenshot failed transiently, retrying in %.1fs", delay
            )
            await asyncio.sleep(delay)
    return await page.screenshot(full_page=False)


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
        Capture a screenshot of the current viewport.

        Returns:
            np.ndarray: Screenshot as OpenCV-compatible NumPy array
        """
        return self.capture_screenshot_as_numpy()

    def capture_screenshot_bytes(self) -> bytes:
        """
        Return the viewport as native PNG bytes.

        ``page.screenshot()`` already returns encoded PNG, so this is the cheapest
        path of all backends — no base64 decode and no OpenCV decode/encode at all.
        """
        page = self._require_page()
        try:
            internal_logger.debug("Capturing Playwright screenshot")
            return run_async(_screenshot_viewport(page))
        except Exception as e:
            internal_logger.warning(
                "Error capturing Playwright screenshot bytes: %s", e, exc_info=True
            )
            raise RuntimeError(f"Error capturing Playwright screenshot bytes: {e}") from e

    def capture_screenshot_as_numpy(self) -> np.ndarray:
        """
        Captures screenshot via Playwright and converts to NumPy image.
        Only captures the viewport, not the full page.

        Reuses :meth:`capture_screenshot_bytes` and only adds the decode.

        Returns:
            numpy.ndarray: Screenshot image
        """
        screenshot_bytes = self.capture_screenshot_bytes()
        np_image = np.frombuffer(screenshot_bytes, np.uint8)
        np_image = cv2.imdecode(np_image, cv2.IMREAD_COLOR)  # type: ignore
        if np_image is None:
            raise RuntimeError("Failed to decode Playwright screenshot")
        return np_image

    # --------------------------------------------------
    # Unsupported operations
    # --------------------------------------------------

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        """
        Not supported (use PlaywrightPageSource for getting interactive elements).

        Args:
            filter_config: Optional list of filter types (not used for this implementation).
        """
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
