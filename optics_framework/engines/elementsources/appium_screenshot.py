from typing import Optional, Any, List
from appium.webdriver.webdriver import WebDriver
from optics_framework.common.elementsource_interface import EncodedScreenshotSource
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.utils import capture_base64_screenshot_bytes


class AppiumScreenshot(EncodedScreenshotSource):
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

    def capture_screenshot_bytes(self) -> bytes:
        """
        Return the device screen as native PNG bytes via Appium's base64 endpoint.

        Skips the ``base64 -> decode_image`` decode that
        :meth:`capture` does, for callers that only need encoded
        bytes. The retry (shared with :class:`SeleniumScreenshot`) absorbs the
        truncated-response ("Incorrect padding") base64 failures occasionally seen
        against busy remote hubs.
        """
        if not getattr(self.driver, "can_native_screenshot", lambda: True)():
            raise OpticsError(Code.E0303, message="This TV cannot take a screenshot through Appium; use camera_screenshot")
        driver = self._require_driver()
        return capture_base64_screenshot_bytes(driver.get_screenshot_as_base64, "Appium")

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        internal_logger.exception("AppiumScreenshot does not support getting interactive elements.")
        raise NotImplementedError(
            "AppiumScreenshot does not support getting interactive elements."
        )

    def assert_elements(self, elements, timeout=30, rule='any') -> None:
        internal_logger.exception("AppiumScreenshot does not support asserting elements.")
        raise NotImplementedError("AppiumScreenshot does not support asserting elements.")

    def locate(self, element, index=None) -> tuple:
        internal_logger.exception("AppiumScreenshot does not support locating elements.")
        raise NotImplementedError("AppiumScreenshot does not support locating elements.")

    def locate_using_index(self):
        internal_logger.exception("AppiumScreenshot does not support locating elements using index.")
        raise NotImplementedError("AppiumScreenshot does not support locating elements using index.")
