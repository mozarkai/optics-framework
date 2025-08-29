import subprocess  # nosec
from typing import Any, Dict, Optional, Union
from appium import webdriver
from appium.webdriver.webdriver import WebDriver
from appium.options.android.uiautomator2.base import UiAutomator2Options
from appium.options.ios import XCUITestOptions # type: ignore
from appium.webdriver.common.appiumby import AppiumBy
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.logging_config import internal_logger, execution_logger
from optics_framework.common import utils
from optics_framework.common.utils import SpecialKey
from optics_framework.common.eventSDK import EventSDK
from optics_framework.engines.drivers.appium_UI_helper import UIHelper



class Appium(DriverInterface):
    DEPENDENCY_TYPE = "driver_sources"
    NAME = "appium"
    NOT_INITIALIZED = "Appium driver is not initialized. Please start the session first."

    def __init__(self, config: Optional[Dict[str, Any]] = None, event_sdk: Optional[EventSDK] = None) -> None:
        self.driver: Optional[WebDriver] = None
        if event_sdk is None:
            internal_logger.error("No EventSDK instance provided to Appium driver.")
            raise ValueError("Appium driver requires an EventSDK instance.")
        self.event_sdk: EventSDK = event_sdk
        if config is None:
            internal_logger.error(
                f"No configuration provided for {self.DEPENDENCY_TYPE}: {self.NAME}"
            )
            raise ValueError("Appium driver not enabled in config")

        self.appium_server_url: str = str(config.get("url", "http://127.0.0.1:4723"))

        self.capabilities: Dict[str, Any] = config.get("capabilities", {})
        if not self.capabilities:
            internal_logger.error("No capabilities found in config")
            raise ValueError("Appium capabilities not found in config")

        # UI Tree handling
        self.ui_helper: Optional[UIHelper] = None
        self.initialized: bool = True

    def _require_driver(self) -> WebDriver:
        """Helper to ensure self.driver is initialized, else raise error."""
        if self.driver is None:
            internal_logger.error(self.NOT_INITIALIZED)
            raise RuntimeError(self.NOT_INITIALIZED)
        return self.driver

    def start_session(
        self,
        app_package: Optional[str] = None,
        app_activity: Optional[str] = None,
        event_name: Optional[str] = None,
    ) -> WebDriver:
        """
        Start the Appium session if not already started, incorporating custom capabilities.
        Optionally override appPackage and appActivity capabilities for Android.
        """
        if self.driver is not None:
            old_session_id = self.driver.session_id
            internal_logger.info(f"Cleaning up old driver with session_id: {old_session_id}")
            try:
                internal_logger.info("Cleaning up existing driver before starting new session")
                self.driver.quit()
            except Exception as cleanup_error:
                internal_logger.warning(f"Failed to clean up existing driver: {cleanup_error}")
            finally:
                self.driver = None

        all_caps = self.capabilities.copy() if self.capabilities else {}

        # If app_package or app_activity are provided, update capabilities
        if app_package:
            all_caps["appPackage"] = app_package
            all_caps["appium:appPackage"] = app_package
        if app_activity:
            all_caps["appActivity"] = app_activity
            all_caps["appium:appActivity"] = app_activity

        options, default_options = self._get_platform_and_options(all_caps)

        # Combine default and user-provided capabilities, with user's config taking precedence
        final_caps = {**default_options, **all_caps}
        internal_logger.debug(f"Final capabilities being applied: {final_caps}")

        # Apply all final capabilities to the options object
        for key, value in final_caps.items():
            options.set_capability(key, value)

        if event_name:
            self.event_sdk.capture_event(event_name)
        internal_logger.debug(
            f"Starting Appium session with capabilities: {options.to_capabilities()}"
        )
        try:
            self.driver = webdriver.Remote(self.appium_server_url, options=options)  # type: ignore
            if self.driver is None:
                raise RuntimeError("Failed to create Appium WebDriver instance")
            # CRITICAL: Log the new session ID
            new_session_id = self.driver.session_id
            internal_logger.info(f"NEW Appium session created with session_id: {new_session_id}")
            self.ui_helper = UIHelper(self)
            return self.driver
        except Exception as e:
            internal_logger.error(f"Failed to create new Appium session: {e}")
            self.driver = None
            raise RuntimeError("Failed to create new Appium session due to: " + str(e)) from e


    def _get_platform_and_options(self, all_caps: Dict[str, Any]) -> tuple[Any, Dict[str, Any]]:
        """Helper to determine platform, create options, and set defaults."""
        platform = all_caps.get("platformName") or all_caps.get("appium:platformName")

        if not platform:
            # Fallback for case-insensitivity, though keys are usually case-sensitive
            for key in all_caps:
                if key.lower() == "platformname":
                    platform = all_caps[key]
                    break
            if not platform:
                raise ValueError("'platformName' capability is required.")

        internal_logger.debug(f"Appium Server URL: {self.appium_server_url}")
        internal_logger.debug(f"All capabilities from config: {all_caps}")

        # Set default options that can be overridden by user config
        default_options = {
            "newCommandTimeout": 3600,
            "ensureWebviewsHavePages": True,
            "nativeWebScreenshot": True,
            "noReset": True,
            "shouldTerminateApp": True,
            "forceAppLaunch": True,
            "connectHardwareKeyboard": True,
        }

        if platform.lower() == "android":
            options = UiAutomator2Options()
            # Add Android-specific defaults
            default_options["ignoreHiddenApiPolicyError"] = True
        elif platform.lower() == "ios":
            options = XCUITestOptions()
        else:
            raise ValueError(
                f"Unsupported platform: {platform}. Use 'Android' or 'iOS'."
            )
        return options, default_options

    def force_terminate_app(self, app_name: str, event_name: Optional[str] = None) -> None:
        """
        Forcefully terminates the specified application.

        :param app_name: The name of the application to terminate.
        :param event_name: The event triggering the forced termination, if any.
        """
        if not self.driver:
            internal_logger.error(self.NOT_INITIALIZED)
            return

        if event_name:
            self.event_sdk.capture_event(event_name)

        internal_logger.debug(f"Force terminating app: {app_name}")
        try:
            self.driver.terminate_app(app_name)
            internal_logger.info(f"Successfully terminated app: {app_name}")
        except Exception as e:
            internal_logger.error(f"Failed to force terminate app '{app_name}': {e}")

    def terminate(self, event_name: Optional[str] = None) -> None:
        """End the Appium session if active."""
        if self.driver:

            current_session_id = self.driver.session_id
            internal_logger.info(
                f"Terminating Appium session with session_id: {current_session_id}"
            )
            if event_name:
                self.event_sdk.capture_event(event_name)
            self.driver.quit()
            self.driver = None
            self.event_sdk.send_all_events()

    def get_app_version(self) -> str:
        """Get the version of the application."""
        app_package = self.capabilities.get("appPackage") or self.capabilities.get(
            "appium:appPackage"
        )
        if not app_package:
            raise ValueError(
                "Missing required capability: appPackage or appium:appPackage"
            )

        command = f"adb shell dumpsys package {app_package} | grep versionName"
        try:
            # Run the adb command and capture the output.
            output = subprocess.check_output(command, shell=False, stderr=subprocess.STDOUT, text=True) # nosec B603
            # Process the output to find the line containing "versionName"
            for line in output.splitlines():
                if "versionName=" in line:
                    # Extract the version string.
                    return line.split("versionName=")[-1].strip()
        except subprocess.CalledProcessError as e:
            internal_logger.error(f"Error executing adb command: {e.output}")
        return ""

    def initialise_setup(self) -> None:
        """Initialize the Appium setup by starting the session."""
        self.start_session()
        internal_logger.debug("Appium setup initialized.")

    def launch_app(
        self,
        app_identifier: Optional[str] = None,
        app_activity: Optional[str] = None,
        event_name: Optional[str] = None,
    ) -> None:
        """Launch the app using the Appium driver."""
        if self.driver is None:
            self.start_session(
                app_package=app_identifier,
                app_activity=app_activity,
                event_name=event_name,
            )
        execution_logger.debug(f"Launched application with event: {event_name}")


    def launch_other_app(self, app_name: str, event_name: Optional[str] = None) -> None:
        """Launch an app on the Appium-connected device using ADB by fuzzy matching the app name."""
        if self.driver is None:
            self.start_session(event_name=event_name)
        if self.driver:
            self.driver.activate_app(app_name)
            internal_logger.debug(f"Activated app: {app_name} with event: {event_name}")
        else:
            internal_logger.error(self.NOT_INITIALIZED)

    def get_driver(self) -> Optional[WebDriver]:
        """Return the Appium driver instance."""
        return self.driver

    # APPIUM api wrappers
    def click_element(self, element: Any, event_name: Optional[str] = None) -> None:
        """
        Click on the specified element using Appium's click method.
        """
        timestamp = self.event_sdk.get_current_time_for_events()
        try:
            element.click()
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
                internal_logger.debug(f"Clicked on element: {element} at {timestamp}")
        except Exception as e:
            internal_logger.debug(e)

    def tap_at_coordinates(self, x: int, y: int, event_name: Optional[str] = None) -> None:
        """
        Simulates a tap gesture at the specified screen coordinates using Appium's `tap` method.
        """
        try:
            driver = self._require_driver()
            timestamp = self.event_sdk.get_current_time_for_events()
            driver.tap([(x, y)], 100)
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
            internal_logger.debug(f"Tapped at coordinates ({x}, {y})")
        except Exception as e:
            internal_logger.debug(f"Failed to tap at ({x}, {y}): {e}")

    def swipe(
        self,
        x_coor: int,
        y_coor: int,
        direction: str,
        swipe_length: int,
        event_name: Optional[str] = None
    ) -> None:
        driver = self._require_driver()
        x_coor = int(x_coor)
        y_coor = int(y_coor)
        end_x: int = x_coor
        end_y: int = y_coor
        if direction == "up":
            end_y = y_coor - swipe_length
        elif direction == "down":
            end_y = y_coor + swipe_length
        elif direction == "left":
            end_x = x_coor - swipe_length
        elif direction == "right":
            end_x = x_coor + swipe_length
        else:
            internal_logger.error(f"Unknown swipe direction: {direction}")
            return
        timestamp = self.event_sdk.get_current_time_for_events()
        try:
            execution_logger.debug(
                f"Swiping from ({x_coor}, {y_coor}) to ({end_x}, {end_y})"
            )
            driver.swipe(x_coor, y_coor, end_x, end_y, 1000)
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
        except Exception as e:
            execution_logger.debug(
                f"Failed to swipe from ({x_coor}, {y_coor}) to ({end_x}, {end_y}): {e}"
            )


    def swipe_percentage(
        self,
        x_percentage: float,
        y_percentage: float,
        direction: str,
        swipe_percentage: float,
        event_name: Optional[str] = None
    ) -> None:
        driver = self._require_driver()
        window_size = driver.get_window_size()
        width = window_size["width"]
        height = window_size["height"]
        start_x = int(width * x_percentage / 100)
        start_y = int(height * y_percentage / 100)
        swipe_length: int
        if direction in ("up", "down"):
            swipe_length = int(height * swipe_percentage / 100)
        elif direction in ("left", "right"):
            swipe_length = int(width * swipe_percentage / 100)
        else:
            internal_logger.error(f"Unknown swipe direction: {direction}")
            return
        self.swipe(start_x, start_y, direction, swipe_length, event_name)

    def swipe_element(
        self,
        element: Any,
        direction: str,
        swipe_length: int,
        event_name: Optional[str] = None
    ) -> None:
        driver = self._require_driver()
        location = element.location
        swipe_length = int(swipe_length)
        size = element.size
        start_x = location["x"] + size["width"] // 2
        start_y = location["y"] + size["height"] // 2
        end_x: int
        end_y: int
        if direction in ("up", "down"):
            end_x = start_x
            end_y = start_y + swipe_length if direction == "down" else start_y - swipe_length
        elif direction in ("left", "right"):
            end_y = start_y
            end_x = start_x + swipe_length if direction == "right" else start_x - swipe_length
        else:
            internal_logger.error(f"Unknown swipe direction: {direction}")
            return
        timestamp = self.event_sdk.get_current_time_for_events()
        try:
            execution_logger.debug(
                f"Swiped from ({start_x}, {start_y}) to ({end_x}, {end_y})"
            )
            driver.swipe(start_x, start_y, end_x, end_y, 1000)
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
        except Exception as e:
            execution_logger.debug(
                f"Failed to swipe from ({start_x}, {start_y}) to ({end_x}, {end_y}): {e}"
            )

    def scroll(
        self,
        direction: str,
        duration: int = 1000,
        event_name: Optional[str] = None
    ) -> None:
        driver = self._require_driver()
        window_size = driver.get_window_size()
        width = window_size["width"]
        height = window_size["height"]
        start_x: int
        start_y: int
        end_y: int
        if direction == "up":
            start_x = width // 2
            start_y = int(height * 0.8)
            end_y = int(height * 0.2)
        elif direction == "down":
            start_x = width // 2
            start_y = int(height * 0.2)
            end_y = int(height * 0.8)
        else:
            internal_logger.error(f"Scroll direction '{direction}' not supported.")
            return
        timestamp = self.event_sdk.get_current_time_for_events()
        try:
            internal_logger.debug(
                f"Scrolling {direction} from ({start_x}, {start_y}) to ({start_x}, {end_y})"
            )
            driver.swipe(start_x, start_y, start_x, end_y, duration)
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
        except Exception as e:
            execution_logger.debug(f"Failed to scroll {direction}: {e}")

    def enter_text_element(self, element: Any, text: Union[str, SpecialKey], event_name: Optional[str] = None) -> None:
        if event_name:
            self.event_sdk.capture_event(event_name)

        if isinstance(text, SpecialKey):
            keycode_map = {
                SpecialKey.ENTER: 66,
                SpecialKey.TAB: 61,
                SpecialKey.BACKSPACE: 67,
                SpecialKey.SPACE: 62,
                SpecialKey.ESCAPE: 111,
            }
            internal_logger.debug(
                f"Pressing Detected SpecialKey in element: {text}. Keycode: {keycode_map[text]}"
            )
            execution_logger.debug(f"Pressing SpecialKey in element: {text}")
            driver = self._require_driver()
            driver.press_keycode(keycode_map[text])
        else:
            execution_logger.debug(f"Entering text '{text}' into element: {element}")
            element.send_keys(utils.strip_sensitive_prefix(str(text)))

    def clear_text_element(self, element: Any, event_name: Optional[str] = None) -> None:
        if event_name:
            self.event_sdk.capture_event(event_name)
        execution_logger.debug(f"Clearing text in element: {element}")
        element.clear()

    def enter_text(self, text: Union[str, SpecialKey], event_name: Optional[str] = None) -> None:
        driver = self._require_driver()
        if event_name:
            self.event_sdk.capture_event(event_name)

        if isinstance(text, SpecialKey):
            keycode_map = {
                SpecialKey.ENTER: 66,
                SpecialKey.TAB: 61,
                SpecialKey.BACKSPACE: 67,
                SpecialKey.SPACE: 62,
                SpecialKey.ESCAPE: 111,
            }
            internal_logger.debug(
                f"Pressing Detected SpecialKey: {text}. Keycode: {keycode_map[text]}"
            )
            execution_logger.debug(f"Pressing SpecialKey: {text}")
            driver.press_keycode(keycode_map[text])
        else:
            execution_logger.debug(f"Entering text: {text}")
            text_to_send = utils.strip_sensitive_prefix(str(text))
            driver.execute_script("mobile: type", {"text": text_to_send})

    def clear_text(self, event_name: Optional[str] = None) -> None:
        driver = self._require_driver()
        if event_name:
            self.event_sdk.capture_event(event_name)
        execution_logger.debug("Clearing text input")
        driver.execute_script("mobile: clear")

    def press_keycode(self, keycode: str, event_name: Optional[str] = None) -> None:
        driver = self._require_driver()
        if event_name:
            self.event_sdk.capture_event(event_name)
        execution_logger.debug(f"Pressing keycode: {keycode}")
        driver.press_keycode(int(utils.strip_sensitive_prefix(keycode)))

    def enter_text_using_keyboard(
        self,
        text: Union[str, SpecialKey],
        event_name: Optional[str] = None
    ) -> None:
        driver = self._require_driver()
        keycode_map = {
            SpecialKey.ENTER: 66,
            SpecialKey.TAB: 61,
            SpecialKey.BACKSPACE: 67,
            SpecialKey.SPACE: 62,
            SpecialKey.ESCAPE: 111,
        }
        try:
            timestamp = self.event_sdk.get_current_time_for_events()

            if isinstance(text, SpecialKey):
                internal_logger.debug(
                    f"Pressing Detected SpecialKey: {text}. Keycode: {keycode_map[text]}"
                )
                execution_logger.debug(f"Pressing SpecialKey: {text}")
                driver.press_keycode(keycode_map[text])
            else:
                text_value = str(text)
                execution_logger.debug(f"Entering text using keyboard: {text_value}")
                driver.execute_script(
                    "mobile: type", {"text": utils.strip_sensitive_prefix(text_value)}
                )

            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
        except Exception as e:
            raise RuntimeError(f"Appium failed to enter input: {e}")

    def get_char_as_keycode(self, char: str) -> Optional[int]:
        # Basic lowercase mapping; extend as needed
        mapping = {
            "a": 29,
            "b": 30,
            "c": 31,
            "d": 32,
            "e": 33,
            "f": 34,
            "g": 35,
            "h": 36,
            "i": 37,
            "j": 38,
            "k": 39,
            "l": 40,
            "m": 41,
            "n": 42,
            "o": 43,
            "p": 44,
            "q": 45,
            "r": 46,
            "s": 47,
            "t": 48,
            "u": 49,
            "v": 50,
            "w": 51,
            "x": 52,
            "y": 53,
            "z": 54,
            "0": 7,
            "1": 8,
            "2": 9,
            "3": 10,
            "4": 11,
            "5": 12,
            "6": 13,
            "7": 14,
            "8": 15,
            "9": 16,
            " ": 62,
            "\n": 66,  # Enter key
        }

        return mapping.get(char.lower())  # handle lowercase input

    def get_text_element(self, element: Any) -> str:
        text = element.get_attribute("text") or element.get_attribute("value")
        internal_logger.info(f"Text of element: {text}")
        if text is None:
            raise ValueError("Element text is None")
        return text

    # helper functions
    def pixel_2_appium(self, x: int, y: int, screenshot: Any) -> Optional[tuple[int, int]]:
        driver = self._require_driver()
        if not x or not y:
            return None
        window_size = driver.get_window_size()
        screen_width = window_size["width"]
        screen_height = window_size["height"]
        internal_logger.debug(f"Appium Window Size: {screen_width, screen_height}")
        screenshot_height, screenshot_width = screenshot.shape[:2]
        internal_logger.debug(f"screenshot size: {screenshot_width, screen_height}")
        scaled_x = int(x * screen_width / screenshot_width)
        scaled_y = int(y * screen_height / screenshot_height)
        internal_logger.debug(f"scaled values : {scaled_x, scaled_y}")
        return scaled_x, scaled_y

    # action keywords

    def press_element(self, element: Any, repeat: int, event_name: Optional[str] = None) -> None:
        timestamp = None
        for _ in range(repeat):
            try:
                timestamp = self.event_sdk.get_current_time_for_events()
                element.click()
            except Exception as e:
                raise Exception(f"Error occurred while clicking on element: {e}")
        if event_name and timestamp is not None:
            self.event_sdk.capture_event_with_time_input(event_name, timestamp)
            execution_logger.debug("Clicked on element: %s at %s", element, timestamp)

    def press_coordinates(self, coor_x: int, coor_y: int, event_name: Optional[str] = None) -> None:
        """
        Press an element by absolute coordinates.

        Args:
            self.tap_at_coordinates(coor_x, coor_y, event_name)
            coor_y (int): The y-coordinate to press.
            repeat (int): The number of times to repeat the press.
            event_name (str | None): The name of the event to trigger, if any.
        """
        coor_x, coor_y = int(coor_x), int(coor_y)
        execution_logger.debug(f"Pressing at coordinates: ({coor_x}, {coor_y})")
        self.tap_at_coordinates(coor_x, coor_y, event_name)

    def press_percentage_coordinates(
        self,
        percentage_x: float,
        percentage_y: float,
        repeat: int,
        event_name: Optional[str] = None
    ) -> None:
        percentage_x, percentage_y = int(percentage_x), int(percentage_y)
        driver = self._require_driver()
        window_size = driver.get_window_size()
        x = int(window_size["width"] * percentage_x / 100)
        y = int(window_size["height"] * percentage_y / 100)
        for _ in range(repeat):
            execution_logger.debug(
                f"Pressing at percentage coordinates: ({percentage_x}%, {percentage_y}%)"
            )
            self.press_coordinates(x, y, event_name)

    def press_xpath_using_coordinates(self, xpath: str, event_name: Optional[str] = None) -> None:
        """
        Press an element by its XPath using the bounding box coordinates.
        Can be used as a fallback method when interacting with element is not possible.
        Args:
            xpath (str): The XPath of the element to press.
            event_name (str | None): The name of the event to trigger, if any.
        """
        if self.ui_helper is None:
            raise Exception("UIHelper is not initialized.")
        bbox = self.ui_helper.get_bounding_box_for_xpath(xpath)
        if bbox:
            # Unpack bbox as ((x1, y1), (x2, y2))
            (x1, y1), (x2, y2) = bbox
            x_centre = (x1 + x2) // 2
            y_centre = (y1 + y2) // 2
            self.tap_at_coordinates(x_centre, y_centre, event_name)
        else:
            internal_logger.debug(
                f"Bounding box not found for element with xpath: {xpath}"
            )

    def appium_find_element(self, element: str) -> Optional[Any]:
        element_type: str = utils.determine_element_type(element)
        if self.driver is None:
            internal_logger.error(self.NOT_INITIALIZED)
            return None
        if element_type == "XPath":
            return self.driver.find_element(AppiumBy.XPATH, element)
        elif element_type == "Text":
            return self.driver.find_element(AppiumBy.ACCESSIBILITY_ID, element)
        else:
            internal_logger.error(f"Unknown element type: {element_type}")
            return None
