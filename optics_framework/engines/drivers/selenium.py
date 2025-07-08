from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.common.keys import Keys
from typing import Any, Dict, Optional, Union
from optics_framework.common.utils import SpecialKey, strip_sensitive_prefix
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.eventSDK import EventSDK
from optics_framework.engines.drivers.selenium_driver_manager import set_selenium_driver
from optics_framework.engines.drivers.selenium_UI_helper import UIHelper


class SeleniumDriver(DriverInterface):
    _instance = None
    DEPENDENCY_TYPE = "driver_sources"
    NAME = "selenium"
    ACTION_NOT_SUPPORTED = "Action not supported in Selenium."

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
            self.DEPENDENCY_TYPE, self.NAME
        )
        if not config:
            internal_logger.error(
                f"No configuration found for {self.DEPENDENCY_TYPE}: {self.NAME}"
            )
            raise ValueError("Selenium driver not enabled in config")

        self.selenium_server_url: str = config.get("url", "http://localhost:4444/wd/hub")
        self.capabilities = config.get("capabilities", {})
        if not self.capabilities:
            internal_logger.error("No capabilities found in config")
            raise ValueError("Selenium capabilities not found in config")

        self.browser_url = self.capabilities.get("browserURL", "about:blank")
        self.eventSDK = EventSDK.get_instance()
        self.initialized = True
        self.ui_helper = None

    def start_session(self, event_name: str | None = None) -> webdriver.Remote:
        """Start a new Selenium session with the specified browser."""
        if self.driver is None:
            all_caps = self.capabilities
            browser_name = all_caps.get("browserName")

            if not browser_name:
                raise ValueError("'browserName' capability is required.")

            browser_name = browser_name.lower()

            default_options = {}
            if browser_name == "chrome":
                options = ChromeOptions()
                default_options = {
                    "goog:chromeOptions": {
                        "args": [
                            "--remote-debugging-address=0.0.0.0",
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-gpu",
                        ]
                    }
                }
            elif browser_name == "firefox":
                options = FirefoxOptions()
            else:
                raise ValueError(f"Unsupported browser: {browser_name}")

            final_caps = {**default_options, **all_caps}
            internal_logger.debug(f"Final capabilities being applied: {final_caps}")

            for key, value in final_caps.items():
                options.set_capability(key, value)

            try:
                self.driver = webdriver.Remote(
                    command_executor=self.selenium_server_url,
                    options=options
                )

                set_selenium_driver(self.driver)
                if event_name:
                    internal_logger.debug(
                        f"Starting Selenium session with event: {event_name}")
                    self.eventSDK.capture_event(event_name)
                self.ui_helper = UIHelper()
                internal_logger.debug(
                    f"Started Selenium session at {self.selenium_server_url} with browser: {browser_name}")

            except Exception as e:
                internal_logger.error(f"Failed to start Selenium session: {e}")
                raise
        return self.driver

    def terminate(self, event_name: str | None = None) -> None:
        """End the current Selenium session."""
        if self.driver is not None:
            try:
                self.driver.quit()
                internal_logger.debug("Selenium session ended")
                if event_name:
                    internal_logger.debug(
                        f"Ending Selenium session with event: {event_name}")
                    self.eventSDK.capture_event(event_name)
            except Exception as e:
                internal_logger.error(f"Failed to end Selenium session: {e}")
            finally:
                self.driver = None

    def launch_app(self, event_name: str | None = None) -> None:
        """Launch the web application by navigating to the browser URL."""
        if self.driver is None:
            self.start_session()
        try:
            self.driver.get(self.browser_url)
            if event_name:
                internal_logger.debug(
                    f"Launching app at {self.browser_url} with event: {event_name}")
                self.eventSDK.capture_event(event_name)
            internal_logger.debug(
                f"Launched web app at {self.browser_url} with event: {event_name}")
        except Exception as e:
            internal_logger.error(f"Failed to launch app at {self.browser_url}: {e}")
            raise

    def launch_other_app(self, app_name, event_name):
        raise NotImplementedError("Selenium driver does not support launching apps yet.")

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
                timestamp = self.eventSDK.get_current_time_for_events()
                element.click()
            internal_logger.debug(
                f"Pressed element {repeat} times with event: {event_name}")
            if event_name:
                self.eventSDK.capture_event_with_time_input(event_name, timestamp)
        except Exception as e:
            internal_logger.error(f"Failed to press element: {e}")
            raise


    # Placeholder implementations for remaining abstract methods
    def get_app_version(self) -> str:
        raise NotImplementedError

    def press_coordinates(self, coor_x: int, coor_y: int, event_name: str | None = None) -> None:
        """Click at specific screen coordinates using JavaScript (limited support)."""
        try:
            script = f"""
            var element = document.elementFromPoint({coor_x}, {coor_y});
            if (element) element.click();
            """
            timestamp = self.eventSDK.get_current_time_for_events()
            self.driver.execute_script(script)
            if event_name:
                self.eventSDK.capture_event_with_time_input(event_name, timestamp)
            internal_logger.debug(f"Clicked at coordinates ({coor_x}, {coor_y}) with event: {event_name}")
        except Exception as e:
            internal_logger.error(f"Failed to click at coordinates: {e}")
            raise

    def press_percentage_coordinates(self, percentage_x: float, percentage_y: float, repeat: int = 1, event_name: str | None = None) -> None:
        """Click based on screen percentage coordinates."""
        try:
            size = self.driver.get_window_size()
            abs_x = int(size['width'] * percentage_x / 100)
            abs_y = int(size['height'] * percentage_y / 100)
            self.press_coordinates(abs_x, abs_y, event_name)
        except Exception as e:
            internal_logger.error(f"Failed to click using percentage coordinates: {e}")
            raise


    def enter_text(self, text: str, event_name: str | None = None) -> None:
        """Selenium needs a focused element to type into."""
        active_element = self.driver.switch_to.active_element
        if active_element:
            if text == "KEYS.ENTER":
                timestamp = self.eventSDK.get_current_time_for_events()
                active_element.send_keys(Keys.ENTER)
            else:
                timestamp = self.eventSDK.get_current_time_for_events()
                active_element.send_keys(strip_sensitive_prefix(text))
            if event_name:
                self.eventSDK.capture_event_with_time_input(event_name, timestamp)
            internal_logger.debug(f"Typed '{text}' into active element with event: {event_name}")
        else:
            internal_logger.error("No active element to type into.")
            raise RuntimeError("No active element to type into.")

    def enter_text_element(self, element, text: str, event_name: str | None = None) -> None:
        """Enter text into a specific Selenium WebElement."""
        if self.driver is None:
            raise RuntimeError(
                "Selenium session not started. Call start_session() first.")
        try:
            element.clear()  # Clear existing text first
            timestamp = self.eventSDK.get_current_time_for_events()
            element.send_keys(strip_sensitive_prefix(text))
            internal_logger.debug(
                f"Entered text '{text}' into element with event: {event_name}")
            if event_name:
                self.eventSDK.capture_event_with_time_input(event_name, timestamp)
        except Exception as e:
            internal_logger.error(f"Failed to enter text into element: {e}")
            raise

    def press_keycode(self, keycode: int, event_name: str | None = None) -> None:
        """Selenium does not support raw keycodes. Log a warning."""
        self._raise_action_not_supported()

    def enter_text_using_keyboard(self, input_value: Union[str, SpecialKey], event_name: Optional[str] = None):
        key_map = {
            SpecialKey.ENTER: Keys.ENTER,
            SpecialKey.TAB: Keys.TAB,
            SpecialKey.BACKSPACE: Keys.BACKSPACE,
            SpecialKey.SPACE: Keys.SPACE,
            SpecialKey.ESCAPE: Keys.ESCAPE,
        }

        try:
            active_element = self.driver.switch_to.active_element
            timestamp = self.eventSDK.get_current_time_for_events()
            if isinstance(input_value, SpecialKey):
                active_element.send_keys(key_map[input_value])
            else:
                active_element.send_keys(strip_sensitive_prefix(input_value))
            if event_name:
                self.eventSDK.capture_event_with_time_input(event_name, timestamp)
        except Exception as e:
            internal_logger.error(f"Failed to enter input using keyboard: {e}")
            raise RuntimeError(f"Selenium failed to enter input: {e}")

    def clear_text(self, event_name: str | None = None) -> None:
        """Clear the active element (if possible)."""
        try:
            active_element = self.driver.switch_to.active_element
            if event_name:
                self.eventSDK.capture_event(event_name)
            active_element.clear()
            internal_logger.debug(f"Cleared text in active element with event: {event_name}")
        except Exception as e:
            internal_logger.error(f"Failed to clear active element: {e}")
            raise


    def clear_text_element(self, element, event_name: str | None = None) -> None:
        try:
            if event_name:
                self.eventSDK.capture_event(event_name)
            element.clear()
            internal_logger.debug(f"Cleared text in specified element with event: {event_name}")
        except Exception as e:
            internal_logger.error(f"Failed to clear element: {e}")
            raise


    def swipe(self, x_coor: int, y_coor: int, direction: str, swipe_length: int, event_name: str | None = None) -> None:
        self._raise_action_not_supported()

    def swipe_percentage(self, x_percentage: float, y_percentage: float, direction: str, swipe_percentage: float, event_name: str | None = None) -> None:
        self._raise_action_not_supported()

    def swipe_element(self, element, direction: str, swipe_length: int, event_name: str | None = None) -> None:
        self._raise_action_not_supported()

    def scroll(self, direction: str, duration: int = 1000, event_name: str | None = None) -> None:
        try:
            if event_name:
                self.eventSDK.capture_event(event_name)
            if direction == "down":
                self.driver.execute_script("window.scrollBy(0, window.innerHeight);")
            elif direction == "up":
                self.driver.execute_script("window.scrollBy(0, -window.innerHeight);")
            elif direction == "right":
                self.driver.execute_script("window.scrollBy(window.innerWidth, 0);")
            elif direction == "left":
                self.driver.execute_script("window.scrollBy(-window.innerWidth, 0);")
            internal_logger.debug(f"Scrolled {direction} with event: {event_name}")
        except Exception as e:
            internal_logger.error(f"Failed to scroll {direction}: {e}")
            raise

    def get_text_element(self, element) -> str:
        try:
            text = element.text
            internal_logger.debug(f"Extracted text from element: {text}")
            return text
        except Exception as e:
            internal_logger.error(f"Failed to get text from element: {e}")
            return ""

    def _raise_action_not_supported(self) -> None:
        internal_logger.warning(self.ACTION_NOT_SUPPORTED)
        raise NotImplementedError(self.ACTION_NOT_SUPPORTED)
