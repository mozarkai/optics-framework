import subprocess # nosec
from typing import Any, Dict, Optional
from appium import webdriver
from appium.options.android.uiautomator2.base import UiAutomator2Options
from appium.options.ios import XCUITestOptions
from optics_framework.common.config_handler import ConfigHandler
from appium.webdriver.common.appiumby import AppiumBy
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from optics_framework.engines.drivers.appium_driver_manager import set_appium_driver
from optics_framework.engines.drivers.appium_UI_helper import UIHelper

# Hotfix: Disable debug logs from Appium to prevent duplicates on live logs
# logging.disable(logging.DEBUG)


class Appium(DriverInterface):
    _instance = None
    DEPENDENCY_TYPE = "driver_sources"
    NAME = "appium"

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(Appium, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "initialized") and self.initialized:
            return
        self.driver = None
        config_handler = ConfigHandler.get_instance()
        config: Optional[Dict[str, Any]] = config_handler.get_dependency_config(self.DEPENDENCY_TYPE, self.NAME)

        if not config:
            internal_logger.error(f"No configuration found for {self.DEPENDENCY_TYPE}: {self.NAME}")
            raise ValueError("Appium driver not enabled in config")

        self.appium_server_url: str = config.get("url", "http://127.0.0.1:4723")
        self.capabilities: Dict[str, Any] = config.get("capabilities", {})
        # UI Tree handling
        self.ui_helper = None
        self.tree = None
        self.root = None
        self.prev_hash = None
        self.initialized = True

    # session management
    def start_session(self):
        """Start the Appium session if not already started."""
        app_package = self.capabilities.get('appPackage')
        app_activity = self.capabilities.get('appActivity')
        platform = self.capabilities.get('platformName')
        device_serial = self.capabilities.get('deviceName')
        automation_name = self.capabilities.get('automationName')

        internal_logger.debug(f"Appium Server URL: {self.appium_server_url}")
        new_comm_timeout = 3600  # Command timeout

        if not platform or not device_serial or not automation_name:
            raise ValueError("Missing required capability: platformName, deviceName, or automationName")

        if platform.lower() == "android":
            if not app_package or not app_activity:
                raise ValueError("Android requires 'app_package' and 'app_activity'.")

            options = UiAutomator2Options()
            options.platform_name = platform
            options.device_name = device_serial
            options.udid = device_serial
            options.ensure_webviews_have_pages = True
            options.native_web_screenshot = True
            options.new_command_timeout = new_comm_timeout
            options.connect_hardware_keyboard = True
            options.force_app_launch = True
            options.should_terminate_app = False
            options.automation_name = automation_name
            options.no_reset = False
            options.app_package = app_package
            options.app_activity = app_activity
            options.ignore_hidden_api_policy_error = True

        elif platform.lower() == "ios":
            if not app_package:
                raise ValueError("iOS requires a valid 'app' path.")

            options = XCUITestOptions()
            options.platform_name = platform
            options.device_name = device_serial
            options.udid = device_serial
            options.ensure_webviews_have_pages = True
            options.native_web_screenshot = True
            options.new_command_timeout = new_comm_timeout
            options.connect_hardware_keyboard = True
            options.force_app_launch = True
            options.should_terminate_app = False
            options.automation_name = automation_name
            options.no_reset = True
            options.app = app_package

        else:
            raise ValueError("Unsupported platform. Use 'Android' or 'iOS'.")

        try:
            if self.driver is None:
                self.driver = webdriver.Remote(self.appium_server_url, options=options)
                set_appium_driver(self.driver)
                self.ui_helper = UIHelper()
                return self.driver
        except Exception as e:
            internal_logger.debug(f"Failed to start Appium session: {e}")
            raise

    def terminate(self):
        """End the Appium session if active."""
        if self.driver:
            self.driver.quit()
            self.driver = None

    def get_app_version(self) -> str:
        """Get the version of the application."""
        app_package = self.app_details.get('appPackage')
        if not app_package:
            raise ValueError("Missing required capability: appPackage")

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
            internal_logger.error("Error executing adb command:", e.output)
        return None

    def initialise_setup(self) -> None:
        """Initialize the Appium setup by starting the session."""
        self.start_session()
        internal_logger.debug("Appium setup initialized.")

    def launch_app(self, event_name: str | None = None) -> None:
        """Launch the app using the Appium driver."""
        if self.driver is None:
            self.start_session()
        internal_logger.debug(f"Launched application with event: {event_name}")

    def get_driver(self):
        """Return the Appium driver instance."""
        return self.driver

# APPIUM api wrappers
    def click_element(self, element, event_name=None):
        """
        Click on the specified element using Appium's click method.
        """
        timestamp = utils.get_current_time_for_events()
        try:
            element.click()
            if event_name:
                # TODO: Trigger event
                internal_logger.debug(f"Clicked on element: {element} at {timestamp}")
        except Exception as e:
            internal_logger.debug(e)

    def tap_at_coordinates(self, x, y, event_name=None) -> None:
        """
        Uses Appium 2.x's mobile: clickGesture to tap at specific coordinates.
        """
        try:
            self.driver.tap([(x, y)], 100)
            if event_name:
                # TODO: Trigger event
                pass
            internal_logger.debug(f"Tapped at coordinates ({x}, {y})")
        except Exception as e:
            internal_logger.debug(f"Failed to tap at ({x}, {y}): {e}")

    def swipe(self, start_x, start_y,direction, swipe_length, event_name=None):
        """
        Perform a swipe action using Appium's W3C Actions API.

        :param driver: The Appium WebDriver instance.
        :param start_x: The starting X coordinate.
        :param start_y: The starting Y coordinate.
        :param end_x: The ending X coordinate.
        :param end_y: The ending Y coordinate.
        :param duration: Duration of the swipe in milliseconds (default: 1000ms).
        """
        start_x, start_y = int(start_x), int(start_y)
        if direction == "up":
            end_x = start_x
            end_y = start_y - swipe_length
        elif direction == "down":
            end_x = start_x
            end_y = start_y + swipe_length
        elif direction == "left":
            end_x = start_x - swipe_length
            end_y = start_y
        elif direction == "right":
            internal_logger.debug(f'type of swipe_length: {type(swipe_length)}, type of start_x: {type(start_x)}')
            end_x = start_x + swipe_length
            end_y = start_y
        self.driver.swipe(start_x, start_y, end_x, end_y, 1000)

    def swipe_percentage(self, x_percentage, y_percentage, direction, swipe_percentage, event_name=None):
        window_size = self.driver.get_window_size()
        width = window_size['width']
        height = window_size['height']
        start_x = int(width * x_percentage / 100)
        start_y = int(height * y_percentage / 100)
        if direction == "up" or direction == "down":
            swipe_length = int(height * swipe_percentage / 100)
        elif direction == "left" or direction == "right":
            swipe_length = int(width * swipe_percentage / 100)
        self.swipe(start_x, start_y, direction, swipe_length, 1000)

    def swipe_element(self, element, direction, swipe_length, event_name=None):
        location = element.location
        size = element.size
        start_x = location['x'] + size['width'] // 2
        start_y = location['y'] + size['height'] // 2
        if direction == "up" or direction == "down":
            end_x = start_x
            end_y = start_y + swipe_length if direction == "down" else start_y - swipe_length
        elif direction == "left" or direction == "right":
            end_y = start_y
            end_x = start_x + swipe_length if direction == "right" else start_x - swipe_length
        self.swipe(start_x, start_y, end_x, end_y, 1000)

    def scroll(self, direction, duration=1000, even_name=None):
        window_size = self.driver.get_window_size()
        width = window_size['width']
        height = window_size['height']
        if direction == "up":
            start_x = width // 2
            start_y = int(height * 0.8)
            end_y = int(height * 0.2)
            self.swipe(start_x, start_y, start_x, end_y, duration)
        elif direction == "down":
            start_x = width // 2
            start_y = int(height * 0.2)
            end_y = int(height * 0.8)
            self.swipe(start_x, start_y, start_x, end_y, duration)


    def enter_text_element(self, element, text, event_name=None):
        if event_name:
            #TODO: Trigger event
            pass
        element.send_keys(text)

    def clear_text_element(self, element, event_name=None):
        if event_name:
            # TODO: Trigger event
            pass
        element.clear()

    def enter_text(self, text, event_name=None):
        if event_name:
            #TODO: Trigger event
            pass
        self.driver.execute_script("mobile: type", {"text": text})

    def clear_text(self, event_name=None):
        if event_name:
            # TODO: Trigger event
            pass
        self.driver.execute_script("mobile: clear")

    def press_keycode(self,keycode, even_name=None):
        self.driver.press_keycode(keycode)

    def enter_text_using_keyboard(self, text, event_name=None):
        for char in text:
            keycode = self.get_char_as_keycode(char)
            if keycode:
                self.press_keycode(keycode, event_name)
            else:
                internal_logger.debug(f"Keycode not found for character: {char}")
        if event_name:
            #TODO: Trigger event
            pass


    def get_char_as_keycode(self, char):
        # Basic lowercase mapping; extend as needed
        mapping = {
            'a': 29, 'b': 30, 'c': 31, 'd': 32, 'e': 33, 'f': 34, 'g': 35,
            'h': 36, 'i': 37, 'j': 38, 'k': 39, 'l': 40, 'm': 41, 'n': 42,
            'o': 43, 'p': 44, 'q': 45, 'r': 46, 's': 47, 't': 48, 'u': 49,
            'v': 50, 'w': 51, 'x': 52, 'y': 53, 'z': 54,
            '0': 7,  '1': 8,  '2': 9,  '3': 10, '4': 11,
            '5': 12, '6': 13, '7': 14, '8': 15, '9': 16,
            ' ': 62,
            '\n': 66  # Enter key
        }

        return mapping.get(char.lower())  # handle lowercase input

    def get_text_element(self, element):
        text = element.get_attribute('text') or element.get_attribute('value')
        internal_logger.info(f"Text of element: {text}")
        return text

# helper functions
    def pixel_2_appium(self, x, y, screenshot):
        if not x or not y:
            return None
        window_size = self.driver.get_window_size()
        screen_width = window_size['width']
        screen_height = window_size['height']
        internal_logger.debug(f'Appium Window Size: {screen_width, screen_height}')
        screenshot_height, screenshot_width = screenshot.shape[:2]
        internal_logger.debug(f'screenshot size: {screenshot_width, screen_height}')
        scaled_x = int(x * screen_width / screenshot_width)
        scaled_y = int(y * screen_height / screenshot_height)
        internal_logger.debug(f'scaled values : {scaled_x, scaled_y}')
        return scaled_x, scaled_y

# action keywords

    def press_element(self, element,repeat, event_name=None):
        for _ in range(repeat):
            try:
                timestamp = utils.get_current_time_for_events()
                element.click()
            except Exception as e:
                raise Exception(f"Error occurred while clicking on element: {e}")
            if event_name:
                # trigger event
                internal_logger.debug(f"Clicked on element: {element} at {timestamp}")

    def press_coordinates(self, x, y, event_name=None):
        """
        Press an element by absolute coordinates.

        Args:
            self.tap_at_coordinates(x, y, event_name)
            coor_y (int): The y-coordinate to press.
            repeat (int): The number of times to repeat the press.
            event_name (str | None): The name of the event to trigger, if any.
        """
        self.tap_at_coordinates(x, y, event_name)

    def press_percentage_coordinates(self, percentage_x, percentage_y, repeat, event_name=None):
        window_size = self.driver.get_window_size()
        x = int(window_size['width'] * percentage_x/100)
        y = int(window_size['height'] * percentage_y/100)
        self.press_coordinates(x, y, repeat, event_name)

    def press_xpath_using_coordinates(self, xpath, event_name):
        """
        Press an element by its XPath using the bounding box coordinates.
        Can be used as a fallback method when interacting with element is not possible.
        Args:
            xpath (str): The XPath of the element to press.
            event_name (str | None): The name of the event to trigger, if any.
        """
        bbox = self.ui_helper.get_bounding_box_for_xpath(xpath)
        if bbox:
            x_centre = (bbox[0] + bbox[2]) // 2
            y_centre = (bbox[1] + bbox[3]) // 2
            self.tap_at_coordinates(x_centre, y_centre, event_name)
        else:
            internal_logger.debug(f"Bounding box not found for element with xpath: {xpath}")

    def appium_find_element(self, element):
        element_type = utils.determine_element_type(element)
        if element_type == 'XPath':
            return self.driver.find_element(AppiumBy.XPATH, element)
        elif element_type == 'Text':
            return self.driver.find_element(AppiumBy.ACCESSIBILITY_ID, element)
