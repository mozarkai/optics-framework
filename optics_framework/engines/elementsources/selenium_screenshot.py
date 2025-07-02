"""
Capture Screen Module for Selenium

This module provides a concrete implementation of `ElementSourceInterface`
that captures images from the screen using Selenium WebDriver.
"""
from typing import Optional
import cv2
import base64
import numpy as np
from selenium.common.exceptions import ScreenshotException
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.engines.drivers.selenium_driver_manager import get_selenium_driver


class SeleniumScreenshot(ElementSourceInterface):
    """
    Capture screenshots of the screen using Selenium WebDriver.
    """

    def __init__(self):
        """
        Initialize the screen capture utility with a Selenium driver.
        """
        self.driver = None

    def _get_selenium_driver(self):
        """
        Get the Selenium driver instance.

        Returns:
            WebDriver: The Selenium driver instance.
        """
        if self.driver is None:
            self.driver = get_selenium_driver()
        return self.driver

    def capture(self) -> np.ndarray:
        """
        Capture a screenshot of the screen using Selenium.

        Returns:
            Optional[np.ndarray]: The captured screen image as a NumPy array,
            or `None` if capture fails.
        """
        return self.capture_screenshot_as_numpy()

    def get_interactive_elements(self):

        internal_logger.exception("Selenium Screenshot does not support getting interactive elements.")
        raise NotImplementedError(
            "Selenium Screenshot does not support getting interactive elements."
        )

    def capture_screenshot_as_numpy(self) -> Optional[np.ndarray]:
        """
        Captures a screenshot using Selenium and returns it as a NumPy array.

        Returns:
            Optional[numpy.ndarray]: The captured screenshot as a NumPy array,
            or None if capture fails.
        """
        try:
            # Use Base64 encoding for faster processing
            driver = self._get_selenium_driver()
            screenshot_base64 = driver.get_screenshot_as_base64()
            screenshot_bytes = base64.b64decode(screenshot_base64)

            # Convert to NumPy array
            numpy_image = np.frombuffer(screenshot_bytes, np.uint8)
            numpy_image = cv2.imdecode(numpy_image, cv2.IMREAD_COLOR)
            return numpy_image

        except ScreenshotException as se:
            internal_logger.warning(
                f"ScreenshotException: {se}. Using external camera.")
            return None
        except Exception as e:
            internal_logger.warning(
                f"Error capturing Selenium screenshot: {e}. Using external camera.")
            return None

    def assert_elements(self, elements, timeout=30, rule='any') -> None:
        """
        Placeholder for asserting elements (not implemented).

        Args:
            elements: List of elements to assert.
            timeout (int): Maximum time to wait in seconds (default: 30).
            rule (str): 'any' or 'all' to specify if any or all elements must be present (default: 'any').
        """
        raise NotImplementedError(
            "SeleniumScreenshot does not implement assert_elements.")

    def locate(self, element) -> tuple:
        """
        Placeholder for locating elements (not implemented).

        Args:
            element: The element to locate.

        Returns:
            tuple: Coordinates of the element, or None.
        """
        internal_logger.exception(
            "SeleniumScreenshot does not support locating elements.")
        raise NotImplementedError(
            "SeleniumScreenshot does not support locate.")

    def locate_using_index(self, element, index) -> tuple:
        """
        Placeholder for locating elements by index (not implemented).

        Args:
            element: The element to locate.
            index (int): Index of the instance to locate.

        Returns:
            tuple: Coordinates of the element, or None.
        """
        internal_logger.exception(
            "SeleniumScreenshot does not support locating elements using index.")
        raise NotImplementedError(
            "SeleniumScreenshot does not support locate_using_index.")
