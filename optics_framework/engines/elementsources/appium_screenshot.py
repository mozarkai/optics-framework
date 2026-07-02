import base64
import time
from typing import Optional, Any, List
import numpy as np
import cv2
from appium.webdriver.webdriver import WebDriver
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger

# Delay between screenshot retries; an immediate retry tends to hit the same transient failure.
_SCREENSHOT_RETRY_BACKOFF_S = 0.5


class AppiumScreenshot(ElementSourceInterface):
    REQUIRED_DRIVER_TYPE = "appium"
    """
    Capture screenshots of the screen using the `mss` library.
    """

    driver: Optional[Any]  # Can be Appium WebDriver or Appium wrapper

    def __init__(self, driver: Optional[Any] = None):
        """
        Initialize the Appium Screenshot Class.
        Args:
            driver: The Appium driver instance (should be passed explicitly).
        """
        self.driver = driver

    def _require_driver(self) -> WebDriver:
        # If self.driver is None, raise error first
        if self.driver is None:
            internal_logger.error(
                "Appium driver is not initialized for AppiumScreenshot."
            )
            raise RuntimeError(
                "Appium driver is not initialized for AppiumScreenshot."
            )
        # If self.driver is a wrapper, extract the raw driver
        if hasattr(self.driver, "driver"):
            return self.driver.driver
        return self.driver

    def capture(self) -> np.ndarray:
        """
        Capture a screenshot of the screen.
        Returns:
            Optional[np.ndarray]: The captured screen image as a NumPy array,
            or `None` if capture fails.
        """
        return self.capture_screenshot_as_numpy()

    def capture_screenshot_bytes(self) -> bytes:
        """
        Return the device screen as native PNG bytes via Appium's base64 endpoint.

        Skips the ``base64 -> numpy -> cv2.imdecode`` decode that
        :meth:`capture_screenshot_as_numpy` does, for callers that only need encoded
        bytes. The retry absorbs the truncated-response ("Incorrect padding") base64
        failures occasionally seen against busy remote hubs.
        """
        driver = self._require_driver()
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                return base64.b64decode(driver.get_screenshot_as_base64())
            except Exception as e:
                last_exc = e
                internal_logger.warning(
                    "Appium native screenshot bytes attempt %d/2 failed: %s", attempt + 1, e
                )
                if attempt == 0:
                    time.sleep(_SCREENSHOT_RETRY_BACKOFF_S)
        raise RuntimeError(f"Error capturing Appium screenshot bytes: {last_exc}") from last_exc

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        internal_logger.exception("AppiumScreenshot does not support getting interactive elements.")
        raise NotImplementedError(
            "AppiumScreenshot does not support getting interactive elements."
        )

    def capture_screenshot_as_numpy(self) -> np.ndarray:
        """
        Captures a screenshot using Appium and returns it as a NumPy array.

        Reuses :meth:`capture_screenshot_bytes` (shared fetch + retry) and only adds
        the decode, so the numpy and bytes paths stay in sync.

        Returns:
            numpy.ndarray: The captured screenshot as a NumPy array.
        """
        screenshot_bytes = self.capture_screenshot_bytes()
        numpy_image = np.frombuffer(screenshot_bytes, np.uint8)
        return cv2.imdecode(numpy_image, cv2.IMREAD_COLOR)  # type: ignore

    def assert_elements(self, elements, timeout=30, rule='any') -> None:
        internal_logger.exception("AppiumScreenshot does not support asserting elements.")
        raise NotImplementedError("AppiumScreenshot does not support asserting elements.")


    def locate(self, element, index=None) -> tuple:
        internal_logger.exception("AppiumScreenshot does not support locating elements.")
        raise NotImplementedError("AppiumScreenshot does not support locating elements.")


    def locate_using_index(self):
        internal_logger.exception("AppiumScreenshot does not support locating elements using index.")
        raise NotImplementedError("AppiumScreenshot does not support locating elements using index.")
