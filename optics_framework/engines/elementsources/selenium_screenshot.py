from typing import Optional, Any, List
from optics_framework.common.elementsource_interface import EncodedScreenshotSource
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.utils import capture_base64_screenshot_bytes


class SeleniumScreenshot(EncodedScreenshotSource):
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

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        internal_logger.exception("SeleniumScreenshot does not support getting interactive elements.")
        raise NotImplementedError("SeleniumScreenshot does not support getting interactive elements.")

    def capture_screenshot_bytes(self) -> bytes:
        """
        Return the screen as native PNG bytes via Selenium's base64 endpoint.

        Skips the ``base64 -> decode_image`` decode that
        :meth:`capture` does, for callers that only need encoded
        bytes. The retry (shared with :class:`AppiumScreenshot`) absorbs
        transient/truncated responses from remote Grid nodes.
        """
        webdriver = self._require_driver().require_webdriver()
        return capture_base64_screenshot_bytes(webdriver.get_screenshot_as_base64, "Selenium")

    def assert_elements(self, elements, timeout=30, rule='any') -> None:
        internal_logger.exception("SeleniumScreenshot does not support asserting elements.")
        raise NotImplementedError("SeleniumScreenshot does not support asserting elements.")

    def locate(self, element, index=None) -> tuple:
        internal_logger.exception("SeleniumScreenshot does not support locating elements.")
        raise NotImplementedError("SeleniumScreenshot does not support locating elements.")
