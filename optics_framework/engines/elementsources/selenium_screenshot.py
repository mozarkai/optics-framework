from typing import Optional, Any, List
import base64
import cv2
import numpy as np
from selenium.common.exceptions import ScreenshotException
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger


class SeleniumScreenshot(ElementSourceInterface):
    REQUIRED_DRIVER_TYPE = "selenium"

    driver: Optional[Any]

    def __init__(self, driver: Optional[Any] = None):
        """
        Initialize the Selenium Screenshot Class.
        Args:
            driver: The Selenium driver instance (should be passed explicitly).
        """
        self.driver = driver

    def _require_driver(self):
        if self.driver is None:
            internal_logger.error("Selenium driver is not initialized for SeleniumScreenshot.")
            raise RuntimeError("Selenium driver is not initialized for SeleniumScreenshot.")
        return self.driver

    def capture(self) -> np.ndarray:
        """
        Capture a screenshot of the screen.
        Returns:
            np.ndarray: The captured screen image as a NumPy array.
        """
        return self.capture_screenshot_as_numpy()

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        internal_logger.exception("SeleniumScreenshot does not support getting interactive elements.")
        raise NotImplementedError("SeleniumScreenshot does not support getting interactive elements.")

    def capture_screenshot_as_numpy(self) -> np.ndarray:
        """
        Captures a screenshot using Selenium and returns it as a NumPy array.
        Returns:
            np.ndarray: The captured screenshot as a NumPy array.
        """
        try:
            driver = self._require_driver()
            screenshot_base64 = driver.get_screenshot_as_base64()
            screenshot_bytes = base64.b64decode(screenshot_base64)
            numpy_image = np.frombuffer(screenshot_bytes, np.uint8)
            numpy_image = cv2.imdecode(numpy_image, cv2.IMREAD_COLOR)
            return numpy_image
        except ScreenshotException as se:
            internal_logger.warning("ScreenshotException : %s. Using external camera.", se)
            raise RuntimeError("ScreenshotException occurred.") from se
        except Exception as e:
            internal_logger.warning("Error capturing Selenium screenshot: %s. Using external camera.", e)
            raise RuntimeError("Error capturing Selenium screenshot.") from e

    def assert_elements(self, elements, timeout=30, rule='any') -> None:
        internal_logger.exception("SeleniumScreenshot does not support asserting elements.")
        raise NotImplementedError("SeleniumScreenshot does not support asserting elements.")

    def locate(self, element, index=None) -> tuple:
        internal_logger.exception("SeleniumScreenshot does not support locating elements.")
        raise NotImplementedError("SeleniumScreenshot does not support locating elements.")
