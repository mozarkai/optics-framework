"""
Capture Screen Module

This module provides a concrete implementation of `ScreenshotInterface`
that captures images from the screen.
"""
import cv2
import base64
import numpy as np
from typing import Optional
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import logger
from optics_framework.engines.drivers.appium_driver_manager import get_appium_driver
from selenium.common.exceptions import ScreenshotException

class DeviceScreenshot(ElementSourceInterface):
    """
    Capture screenshots of the screen using the `mss` library.
    """

    def __init__(self):
        """
        Initialize the screen capture utility.
        """
        self.driver = None

    def _get_appium_driver(self):
        """
        Get the Appium driver instance.

        Returns:
            WebDriver: The Appium driver instance.
        """
        if self.driver is None:
            self.driver = get_appium_driver()
        return self.driver

    def capture(self) -> Optional[np.ndarray]:
        """
        Capture a screenshot of the screen.

        Returns:
            Optional[np.ndarray]: The captured screen image as a NumPy array,
            or `None` if capture fails.
        """
        return self.capture_screenshot_as_numpy()


    def capture_screenshot_as_numpy(self):
        """
        Captures a screenshot using Appium and returns it as a NumPy array.

        Returns:
            numpy.ndarray: The captured screenshot as a NumPy array.
        """
        try:
            # Use Base64 encoding for faster processing
            driver = self._get_appium_driver()
            # logger.debug(f'{driver}, type(driver): {type(driver)}')
            screenshot_base64 = driver.get_screenshot_as_base64()
            screenshot_bytes = base64.b64decode(screenshot_base64)

            # Convert to NumPy array
            numpy_image = np.frombuffer(screenshot_bytes, np.uint8)
            numpy_image = cv2.imdecode(numpy_image, cv2.IMREAD_COLOR)
            return numpy_image

        except ScreenshotException as se:
            # logger.debug(f'ScreenshotException: {se}. Using external camera')
            logger.warning(f'ScreenshotException : {se}. Using external camera.')
            return None
        except Exception as e:
            # Log the error and fallback to external camera
            logger.warning(f"Error capturing Appium screenshot: {e}. Using external camera.")
            return None

    def assert_elements(self, elements):
        return super().assert_elements(elements)


    def locate(self, image: np.ndarray, template: np.ndarray) -> Optional[tuple]:
        logger.exception("CameraScreenshot does not support locating elements.")
        raise NotImplementedError("CameraScreenshot does not support locating elements.")
