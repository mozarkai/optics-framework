from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.common.keys import Keys
from typing import Any, Dict, Optional, Union
from optics_framework.common.utils import SpecialKey, strip_sensitive_prefix
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.eventSDK import EventSDK
from optics_framework.engines.drivers.selenium_UI_helper import UIHelper


class SeleniumDriver(DriverInterface):
    DEPENDENCY_TYPE = "driver_sources"
    NAME = "selenium"
    ACTION_NOT_SUPPORTED = "Action not supported in Selenium."
    SETUP_NOT_INITIALIZED = "Selenium setup not initialized. Call start_session() first."

    def __init__(self, config: Optional[Dict[str, Any]] = None, event_sdk: Optional[EventSDK] = None):
        self.driver: Optional[webdriver.Remote] = None
        if event_sdk is None:
            internal_logger.error("No EventSDK instance provided to Selenium driver.")
            raise ValueError("Selenium driver requires an EventSDK instance.")
        self.event_sdk: EventSDK = event_sdk
        if config is None:
            internal_logger.error(
                f"No configuration found for {self.DEPENDENCY_TYPE}: {self.NAME}"
            )
            raise ValueError("Selenium driver not enabled in config")

        self.selenium_server_url: str = config.get("url", "http://localhost:4444/wd/hub")
        self.capabilities = config.get("capabilities", {})
        if not self.capabilities:
            internal_logger.error("No capabilities found in config")
            raise ValueError("Selenium capabilities not found in config")

        self.browser_url: str = str(self.capabilities.get("browserURL", "about:blank"))
        self.initialized = True
        self.ui_helper = None

    def start_session(
        self,
        browser_url: str | None = None,
        browser_name: str | None = None,
        event_name: str | None = None,
    ) -> webdriver.Remote:
        """
        Start a new Selenium session with the specified browser.
        Optionally override browser_url and browser_name.
        """
        if self.driver is not None:
            return self.driver

        all_caps = self.capabilities.copy() if self.capabilities else {}
        browser_name_val = self._get_browser_name(all_caps, browser_name)
        options, default_options = self._get_browser_options(browser_name_val)
        self._update_browser_url(all_caps, browser_url)
        final_caps = self._merge_capabilities(default_options, all_caps)
        internal_logger.debug(f"Final capabilities being applied: {final_caps}")

        self._set_options_capabilities(options, final_caps)

        try:
            self.driver = webdriver.Remote(
                command_executor=self.selenium_server_url,
                options=options
            )
            if event_name:
                internal_logger.debug(
                    f"Starting Selenium session with event: {event_name}")
                self.event_sdk.capture_event(event_name)
            self.ui_helper = UIHelper(self.driver)
            internal_logger.debug(
                f"Started Selenium session at {self.selenium_server_url} with browser: {browser_name_val}")
        except Exception as e:
            internal_logger.error(f"Failed to start Selenium session: {e}")
            raise
        return self.driver

    def _get_browser_name(self, all_caps: dict, browser_name: str | None) -> str:
        if browser_name:
            all_caps["browserName"] = browser_name
        browser_name_val = all_caps.get("browserName")
        if not browser_name_val:
            raise ValueError("'browserName' capability is required.")
        return browser_name_val.lower()

    def _get_browser_options(self, browser_name_val: str):
        if browser_name_val == "chrome":
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
        elif browser_name_val == "firefox":
            options = FirefoxOptions()
            default_options = {}
        else:
            raise ValueError(f"Unsupported browser: {browser_name_val}")
        return options, default_options

    def _update_browser_url(self, all_caps: dict, browser_url: str | None):
        if browser_url:
            self.browser_url = browser_url
            all_caps["browserURL"] = self.browser_url

    def _merge_capabilities(self, default_options: dict, all_caps: dict) -> dict:
        return {**default_options, **all_caps}

    def _set_options_capabilities(self, options: Any, final_caps: dict):
        for key, value in final_caps.items():
            options.set_capability(key, value)

    def terminate(self, event_name: str | None = None) -> None:
        """End the current Selenium session."""
        if self.driver is not None:
            try:
                self.driver.quit()
                internal_logger.debug("Selenium session ended")
                if event_name:
                    internal_logger.debug(
                        f"Ending Selenium session with event: {event_name}")
                    self.event_sdk.capture_event(event_name)
            except Exception as e:
                internal_logger.error(f"Failed to end Selenium session: {e}")
            finally:
                self.driver = None
                self.event_sdk.send_all_events()

    def force_terminate_app(self, app_name: str, event_name: Optional[str] = None) -> None:
        """
        Forcefully terminates the specified application.
        :param app_name: The name of the application to terminate.
        :param event_name: The event triggering the forced termination, if any.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        if self.driver is None:
            raise RuntimeError(self.SETUP_NOT_INITIALIZED)
        try:
            self.driver.close()
            internal_logger.debug(f"Forcefully terminated app: {app_name}")
            if event_name:
                self.event_sdk.capture_event(event_name)
        except Exception as e:
            internal_logger.error(f"Failed to force terminate app {app_name}: {e}")
            raise RuntimeError(f"Selenium failed to force terminate app: {e}")

    def launch_app(
        self,
        app_identifier: str | None = None,
        app_activity: str | None = None,
        event_name: str | None = None,
    ) -> None:
        """Launch the web application by navigating to the browser URL."""
        if self.driver is None:
            self.start_session(
                browser_url=app_identifier,
                browser_name=app_activity,
                event_name=event_name,
            )
        try:
            self.driver.get(self.browser_url)
            if event_name:
                internal_logger.debug(
                    f"Launching app at {self.browser_url} with event: {event_name}")
                self.event_sdk.capture_event(event_name)
            internal_logger.debug(
                f"Launched web app at {self.browser_url} with event: {event_name}")
        except Exception as e:
            internal_logger.error(f"Failed to launch app at {self.browser_url}: {e}")
            raise

    def launch_other_app(self, app_name: str, event_name):
        try:
            if self.driver is None:
                raise RuntimeError(self.SETUP_NOT_INITIALIZED)
            self.driver.get(app_name)
            if event_name:
                internal_logger.debug(
                    f"Launching other app at {app_name} with event: {event_name}")
                self.event_sdk.capture_event(event_name)
            internal_logger.debug(f"Launched other app at {app_name} with event: {event_name}")
        except Exception as e:
            internal_logger.error(f"Failed to launch other app at {app_name}: {e}")
            raise RuntimeError(f"Selenium failed to launch other app: {e}")

    def press_element(self, element, repeat: int = 1, event_name: str | None = None) -> None:
        """
            Press (click) a located Selenium WebElement a specified number of times.

            Args:
                element: The WebElement to click (can be XPath string or WebElement)
                repeat: Number of times to click the element (default: 1)
                event_name: Optional name of the event for logging
            """
        try:
            timestamp = None
            for _ in range(repeat):
                timestamp = self.event_sdk.get_current_time_for_events()
                element.click()
            internal_logger.debug(
                f"Pressed element {repeat} times with event: {event_name}")
            if event_name and timestamp is not None:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
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
            timestamp = self.event_sdk.get_current_time_for_events()
            self.driver.execute_script(script)
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
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
        if self.driver is None:
            raise RuntimeError(
                "Selenium session not started. Call start_session() first.")

        active_element = self.driver.switch_to.active_element
        if active_element:
            if text == "KEYS.ENTER":
                timestamp = self.event_sdk.get_current_time_for_events()
                active_element.send_keys(Keys.ENTER)
            else:
                timestamp = self.event_sdk.get_current_time_for_events()
                active_element.send_keys(strip_sensitive_prefix(text))
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
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
            timestamp = self.event_sdk.get_current_time_for_events()
            element.send_keys(strip_sensitive_prefix(text))
            internal_logger.debug(
                f"Entered text '{text}' into element with event: {event_name}")
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
        except Exception as e:
            internal_logger.error(f"Failed to enter text into element: {e}")
            raise

    def press_keycode(self, keycode: str, event_name: str | None = None) -> None:
        """Selenium does not support raw keycodes. Log a warning."""
        self._raise_action_not_supported()

    def enter_text_using_keyboard(self, text: Union[str, SpecialKey], event_name: Optional[str] = None):
        key_map = {
            SpecialKey.ENTER: Keys.ENTER,
            SpecialKey.TAB: Keys.TAB,
            SpecialKey.BACKSPACE: Keys.BACKSPACE,
            SpecialKey.SPACE: Keys.SPACE,
            SpecialKey.ESCAPE: Keys.ESCAPE,
        }

        try:
            active_element = self.driver.switch_to.active_element
            timestamp = self.event_sdk.get_current_time_for_events()
            if isinstance(text, SpecialKey):
                active_element.send_keys(key_map[text])
            else:
                active_element.send_keys(strip_sensitive_prefix(text))
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
        except Exception as e:
            internal_logger.error(f"Failed to enter input using keyboard: {e}")
            raise RuntimeError(f"Selenium failed to enter input: {e}")

    def clear_text(self, event_name: str | None = None) -> None:
        """Clear the active element (if possible)."""
        try:
            active_element = self.driver.switch_to.active_element
            if event_name:
                self.event_sdk.capture_event(event_name)
            active_element.clear()
            internal_logger.debug(f"Cleared text in active element with event: {event_name}")
        except Exception as e:
            internal_logger.error(f"Failed to clear active element: {e}")
            raise


    def clear_text_element(self, element, event_name: str | None = None) -> None:
        try:
            if event_name:
                self.event_sdk.capture_event(event_name)
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
                self.event_sdk.capture_event(event_name)
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

    def get_driver_session_id(self) -> Optional[str]:
        """Not applicable for Selenium; raise NotImplementedError."""
        raise NotImplementedError("Driver session id is not yet implemented for Selenium driver")
