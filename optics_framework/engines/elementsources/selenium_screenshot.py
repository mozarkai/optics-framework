from typing import Optional, Any, List
import cv2
import numpy as np
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.utils import capture_base64_screenshot_bytes


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

    def capture_screenshot_bytes(self) -> bytes:
        """
        Return the screen as native PNG bytes via Selenium's base64 endpoint.

        Skips the ``base64 -> numpy -> cv2.imdecode`` decode that
        :meth:`capture_screenshot_as_numpy` does, for callers that only need encoded
        bytes. The retry (shared with :class:`AppiumScreenshot`) absorbs
        transient/truncated responses from remote Grid nodes.
        """
        driver = self._require_driver()
        return capture_base64_screenshot_bytes(driver.get_screenshot_as_base64, "Selenium")

    def capture_screenshot_as_numpy(self) -> np.ndarray:
        """
        Captures a screenshot using Selenium and returns it as a NumPy array.

        Reuses :meth:`capture_screenshot_bytes` (shared fetch + retry) and only adds
        the decode, so the numpy and bytes paths stay in sync.

        Returns:
            np.ndarray: The captured screenshot as a NumPy array.
        """
        screenshot_bytes = self.capture_screenshot_bytes()
        numpy_image = np.frombuffer(screenshot_bytes, np.uint8)
        return cv2.imdecode(numpy_image, cv2.IMREAD_COLOR)

    def assert_elements(self, elements, timeout=30, rule='any') -> None:
        internal_logger.exception("SeleniumScreenshot does not support asserting elements.")
        raise NotImplementedError("SeleniumScreenshot does not support asserting elements.")

    def locate(self, element, index=None) -> tuple:
        internal_logger.exception("SeleniumScreenshot does not support locating elements.")
        raise NotImplementedError("SeleniumScreenshot does not support locating elements.")
