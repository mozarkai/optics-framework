import base64
from typing import Optional, Any, List
import numpy as np
import cv2
from appium.webdriver.webdriver import WebDriver
from selenium.common.exceptions import ScreenshotException
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger


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

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        internal_logger.exception("AppiumScreenshot does not support getting interactive elements.")
        raise NotImplementedError(
            "AppiumScreenshot does not support getting interactive elements."
        )

    def capture_screenshot_as_numpy(self) -> np.ndarray:
        """
        Captures a screenshot using Appium and returns it as a NumPy array.

        Returns:
            numpy.ndarray: The captured screenshot as a NumPy array.
        """
        try:
            # Use Base64 encoding for faster processing
            driver = self._require_driver()
            internal_logger.debug('driver session_id: %s', driver.session_id)
            screenshot_base64 = driver.get_screenshot_as_base64()
            screenshot_bytes = base64.b64decode(screenshot_base64)
            internal_logger.debug("Screenshot bytes length: %d", len(screenshot_bytes))
            numpy_image = np.frombuffer(screenshot_bytes, np.uint8)
            numpy_image = cv2.imdecode(numpy_image, cv2.IMREAD_COLOR) # type: ignore
            return numpy_image

        except ScreenshotException as se:
            # internal_logger.debug(f'ScreenshotException: {se}. Using external camera')
            internal_logger.warning(f'ScreenshotException : {se}. Using external camera.')
            raise RuntimeError("ScreenshotException occurred.")
        except Exception as e:
            # Log the error and fallback to external camera
            internal_logger.warning(f"Error capturing Appium screenshot: {e}. Using external camera.")
            raise RuntimeError(f"Error capturing Appium screenshot: {e}") from e

    def assert_elements(self, elements, timeout=30, rule='any') -> None:
        internal_logger.exception("AppiumScreenshot does not support asserting elements.")
        raise NotImplementedError("AppiumScreenshot does not support asserting elements.")


    def locate(self, element, index=None) -> tuple:
        internal_logger.exception("AppiumScreenshot does not support locating elements.")
        raise NotImplementedError("AppiumScreenshot does not support locating elements.")


    def locate_using_index(self):
        internal_logger.exception("AppiumScreenshot does not support locating elements using index.")
        raise NotImplementedError("AppiumScreenshot does not support locating elements using index.")
