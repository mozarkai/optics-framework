from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from typing import Any, Dict, Optional
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.logging_config import logger, apply_logger_format_to_all
from optics_framework.engines.drivers.selenium_driver_manager import set_selenium_driver

@apply_logger_format_to_all("internal")
class SeleniumDriver(DriverInterface):
    _instance = None
    DEPENDENCY_TYPE = "driver_sources"
    NAME = "selenium"

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(SeleniumDriver, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "initialized") and self.initialized:
            return
        self.driver: Optional[webdriver.Remote] = None
        config_handler = ConfigHandler.get_instance()
        config: Optional[Dict[str, Any]] = config_handler.get_dependency_config(
            self.DEPENDENCY_TYPE, self.NAME)
        if not config:
            logger.error(
                f"No configuration found for {self.DEPENDENCY_TYPE}: {self.NAME}")
            raise ValueError("Selenium driver not enabled in config")
        self.selenium_server_url: str = config.get(
            "url", "http://localhost:4444/wd/hub")
        self.capabilities: Dict[str, Any] = config.get("capabilities", {})
        self.browser_url: str = self.capabilities.get(
            "browserURL", "about:blank")
        self.initialized = True

    def start_session(self):
        """Start a new Selenium session with the specified browser."""
        if self.driver is None:
            browser_name = self.capabilities.get(
                "browserName", "chrome").lower()
            if browser_name == "chrome":
                options = ChromeOptions()
            elif browser_name == "firefox":
                options = FirefoxOptions()
            else:
                raise ValueError(f"Unsupported browser: {browser_name}")
            try:
                self.driver = webdriver.Remote(
                    command_executor=self.selenium_server_url,
                    options=options
                )
                set_selenium_driver(self.driver)
                logger.debug(
                    f"Started Selenium session at {self.selenium_server_url} with browser: {browser_name}")
            except Exception as e:
                logger.error(f"Failed to start Selenium session: {e}")
                raise
        return self.driver

    def end_session(self):
        """End the current Selenium session."""
        if self.driver is not None:
            try:
                self.driver.quit()
                logger.debug("Selenium session ended")
            except Exception as e:
                logger.error(f"Failed to end Selenium session: {e}")
            finally:
                self.driver = None

    def launch_app(self, event_name: str | None = None) -> None:
        """Launch the web application by navigating to the browser URL."""
        if self.driver is None:
            self.start_session()
        try:
            self.driver.get(self.browser_url)
            logger.debug(
                f"Launched web app at {self.browser_url} with event: {event_name}")
        except Exception as e:
            logger.error(f"Failed to launch app at {self.browser_url}: {e}")
            raise


    def press_element(self, element, repeat: int = 1, event_name: str | None = None) -> None:
        """
            Press (click) a located Selenium WebElement a specified number of times.

            Args:
                element: The WebElement to click (can be XPath string or WebElement)
                repeat: Number of times to click the element (default: 1)
                event_name: Optional name of the event for logging
            """
        try:
            for _ in range(repeat):
                element.click()
            logger.debug(
                f"Pressed element {repeat} times with event: {event_name}")
        except Exception as e:
            logger.error(f"Failed to press element: {e}")
            raise

    def enter_text_element(self, element, text: str, event_name: str | None = None) -> None:
        """Enter text into a specific Selenium WebElement."""
        if self.driver is None:
            raise RuntimeError(
                "Selenium session not started. Call start_session() first.")
        try:
            element.clear()  # Clear existing text first
            element.send_keys(text)
            logger.debug(
                f"Entered text '{text}' into element with event: {event_name}")
        except Exception as e:
            logger.error(f"Failed to enter text into element: {e}")
            raise

    # Placeholder implementations for remaining abstract methods
    def get_app_version(self) -> str:
        raise NotImplementedError

    def press_coordinates(self, coor_x: int, coor_y: int, event_name: str | None = None) -> None:
        raise NotImplementedError

    def press_percentage_coordinates(self, percentage_x: float, percentage_y: float, repeat: int, event_name: str | None = None) -> None:
        raise NotImplementedError

    def enter_text(self, text: str, event_name: str | None = None) -> None:
        raise NotImplementedError

    def press_keycode(self, keycode: int, event_name: str | None = None) -> None:
        raise NotImplementedError

    def enter_text_using_keyboard(self, text: str, event_name: str | None = None) -> None:
        raise NotImplementedError

    def clear_text(self, event_name: str | None = None) -> None:
        raise NotImplementedError

    def clear_text_element(self, element, event_name: str | None = None) -> None:
        raise NotImplementedError

    def swipe(self, x_coor: int, y_coor: int, direction: str, swipe_length: int, event_name: str | None = None) -> None:
        raise NotImplementedError

    def swipe_percentage(self, x_percentage: float, y_percentage: float, direction: str, swipe_percentage: float, event_name: str | None = None) -> None:
        raise NotImplementedError

    def swipe_element(self, element, direction: str, swipe_length: int, event_name: str | None = None) -> None:
        raise NotImplementedError

    def scroll(self, direction: str, duration: int, event_name: str | None = None) -> None:
        raise NotImplementedError

    def get_text_element(self, element) -> str:
        raise NotImplementedError
