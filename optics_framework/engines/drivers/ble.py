import time
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, ValidationError
from serial import Serial
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.eventSDK import EventSDK
from optics_framework.common import utils


class CapabilitiesConfig(BaseModel):
    device_id: str
    port: str
    x_invert: int = Field(default=1, ge=-1, le=1, description="Must be either 1 or -1")
    y_invert: int = Field(default=1, ge=-1, le=1, description="Must be either 1 or -1")
    pixel_height: int = Field(
        default=0, ge=0, description="Height of the device in pixels"
    )
    pixel_width: int = Field(
        default=0, ge=0, description="Width of the device in pixels"
    )
    mickeys_height: int = Field(
        default=0, ge=0, description="Height of the device in mickeys"
    )
    mickeys_width: int = Field(
        default=0, ge=0, description="Width of the device in mickeys"
    )

    # app_element: str = Field(default="app", description="Element to launch the app")
    class Config:
        populate_by_name = True


class BLEDriver(DriverInterface):
    """
    BLE-based implementation of the :class:`DriverInterface`.

    This driver facilitates launching applications via BLE communication.
    """

    DEPENDENCY_TYPE = "driver_sources"
    NAME = "ble"

    def __init__(self):
        config_handler = ConfigHandler.get_instance()
        self.event_sdk = EventSDK.get_instance()
        config: Optional[Dict[str, Any]] = config_handler.get_dependency_config(
            self.DEPENDENCY_TYPE, self.NAME
        )

        if not config:
            internal_logger.error(
                f"No configuration found for {self.DEPENDENCY_TYPE}: {self.NAME}"
            )
            raise ValueError("Bluetooth driver not enabled in config")

        try:
            self.capabilities_model = CapabilitiesConfig(
                **config.get("capabilities", {})
            )
        except ValidationError as ve:
            internal_logger.error(f"Invalid Bluetooth capabilities: {ve}")
            raise
        cap: CapabilitiesConfig = self.capabilities_model

        self.device_id = cap.device_id
        self.port = cap.port
        self.device_ratio = cap.pixel_height / cap.pixel_width
        self.x_invert = cap.x_invert
        self.y_invert = cap.y_invert
        self.x_scale_factor_pxmc = cap.pixel_width / cap.mickeys_width
        self.y_scale_factor_pxmc = cap.pixel_height / cap.mickeys_height
        self.pixel_height = cap.pixel_height
        self.pixel_width = cap.pixel_width
        self.mickeys_height = cap.mickeys_height
        self.mickeys_width = cap.mickeys_width
        try:
            self.ser = Serial(self.port, baudrate=115200, timeout=1)
            self.mouse_reset_position()
        except Exception as e:
            internal_logger.error(f"Failed to initialize serial port: {e}")
            raise

    def send_mouse_command(self, button_state, x_delta_mic, y_delta_mic):
        """
        Send a mouse command via serial to move the mouse and update the current coordinates.

        Args:
            button_state (int): The state of the mouse button (0 for release, 1 for press).
            x_delta_mic (int): The change in the X coordinate in mickeys.
            y_delta_mic (int): The change in the Y coordinate in mickeys.
        """
        mouse_command = f"{button_state} {x_delta_mic} {y_delta_mic}"
        self.ser.write((mouse_command + "\n").encode("utf-8"))
        time.sleep(0.1)

    def translate_coordinates_relative(self, button_state, x_coor_mic, y_coor_mic):
        """
        Translate large cartesian coordinates to multiple relative moves, handling
        movements greater than the maximum movement range.

        Args:
            button_state (int): The state of the mouse button (0 for release, 1 for press).
            x_coor_mic (int): The X coordinate in mickeys to move to.
            y_coor_mic (int): The Y coordinate in mickeys to move to.
        """
        max_movement = 127
        x_steps, x_remainder = divmod(abs(x_coor_mic), max_movement)
        y_steps, y_remainder = divmod(abs(y_coor_mic), max_movement)

        x_direction = 1 if x_coor_mic > 0 else -1
        y_direction = 1 if y_coor_mic > 0 else -1

        for _ in range(x_steps):
            self.send_mouse_command(
                button_state, max_movement * x_direction * self.x_invert, 0
            )
        for _ in range(y_steps):
            self.send_mouse_command(
                button_state, 0, max_movement * y_direction * self.y_invert
            )

        if x_remainder != 0:
            self.send_mouse_command(
                button_state, x_remainder * x_direction * self.x_invert, 0
            )
        if y_remainder != 0:
            self.send_mouse_command(
                button_state, 0, y_remainder * y_direction * self.y_invert
            )

    def mouse_reset_position(self):
        """
        Reset the mouse position to the origin (0, 0) by sending multiple movement commands.
        Mouse moves to bottom corner left of the screen.
        """
        for _ in range(40):
            self.send_mouse_command(0, -127 * self.x_invert, -127 * self.y_invert)
            time.sleep(0.1)
        self.send_mouse_command(0, 0, 0)

    def mouse_tap(self):
        """
        Simulate a mouse tap (click) by sending a press and release command.
        """
        self.send_mouse_command(0, 0, 0)
        self.send_mouse_command(1, 0, 0)
        self.send_mouse_command(0, 0, 0)

    def mouse_double_tap(self):
        """
        Simulate a mouse double-tap (double-click) by performing two consecutive taps.
        """
        self.mouse_tap()
        self.mouse_tap()

    def convert_pixel_to_mickeys(self, x_coor_px, y_coor_px):
        """
        Convert pixel coordinates to Mickeys (smallest detectable unit of movement).

        Args:
            x_coor_px (int): The X coordinate in pixels.
            y_coor_px (int): The Y coordinate in pixels.

        Returns:
            Tuple[int, int]: The converted X and Y coordinates in Mickeys.
        """
        x_coor_mic = int(x_coor_px / self.x_scale_factor_pxmc)
        y_coor_mic = int(y_coor_px / self.y_scale_factor_pxmc)
        print(f"Converted Pixel to Mickeys: {x_coor_mic}, {y_coor_mic}")
        return x_coor_mic, y_coor_mic

    def translate_coordinates_relative_pixel(self, button_state, x_coor_px, y_coor_px):
        """
        Translate pixel coordinates to relative coordinates and send the mouse command.

        Args:
            button_state (int): The state of the mouse button (0 for release, 1 for press).
            x_coor_px (int): The X coordinate in pixels.
            y_coor_px (int): The Y coordinate in pixels.
        """
        x_coor_mic, y_coor_mic = self.convert_pixel_to_mickeys(x_coor_px, y_coor_px)
        self.translate_coordinates_relative(button_state, x_coor_mic, y_coor_mic)

    def move_tap(self, x_coor_px, y_coor_px, time_press=0.1, event_name=None):
        """
        Move the mouse to the specified coordinates and perform a tap (click).

        Args:
            x_coor_px (int): The X coordinatein pixels to move to.
            y_coor_px (int): The Y coordinatein pixels to move to.
        """
        x_coor_px, y_coor_px, time_press = (
            int(x_coor_px),
            int(y_coor_px),
            int(time_press),
        )
        self.translate_coordinates_relative_pixel(0, x_coor_px, y_coor_px)
        if event_name:
            self.event_sdk.capture_event(event_name)
        self.mouse_tap()
        time.sleep(time_press)
        self.mouse_reset_position()

    def swipe_ble(self, direction, distance=40, acceleration=1):
        """
        Perform a swipe action in the specified direction with optional acceleration,
        and return to the original position without pressing the mouse during the return.

        Args:
            direction (str): The direction of the swipe (up, down, left, right).
            acceleration (int, optional): The acceleration factor for the swipe.
        """

        def drag(distance, press, x=0, y=0):
            current_distance = 0
            step = 1
            while current_distance < distance:
                # Ensure it doesn't overshoot
                move_distance = min(step, distance - current_distance)
                self.translate_coordinates_relative_pixel(
                    press, x * move_distance * 15, y * move_distance * 15
                )
                current_distance += move_distance
                step += acceleration
                time.sleep(0.1)

        # Perform the swipe with the press state active
        match direction:
            case "up":
                drag(distance, press=1, y=1)
                drag(distance, press=0, y=-1)  # Return without pressing
            case "down":
                drag(distance, press=1, y=-1)
                # drag(distance, press=0, y=1)  # Return without pressing
            case "left":
                drag(distance, press=1, x=-1)
                drag(distance, press=0, x=1)  # Return without pressing
            case "right":
                drag(distance, press=1, x=1)
                drag(distance, press=0, x=-1)  # Return without pressing
            case _:
                print("Invalid direction")

    def press_coordinates(self, coor_x: int, coor_y: int, event_name: str) -> None:
        """
        Press an element by absolute coordinates using BLE.

        :param x_coor: X coordinate of the press.
        :type x_coor: int
        :param y_coor: Y coordinate of the press.
        :type y_coor: int
        :param event_name: The event triggering the press.
        :type event_name: str
        """
        internal_logger.debug(f"Pressing coordinates ({coor_x}, {coor_y}) via BLE.")
        self.move_tap(coor_x, coor_y, event_name=event_name)

    hid_key_codes = {
        'a': 4, 'b': 5, 'c': 6, 'd': 7, 'e': 8, 'f': 9, 'g': 10,
        'h': 11, 'i': 12, 'j': 13, 'k': 14, 'l': 15, 'm': 16, 'n': 17,
        'o': 18, 'p': 19, 'q': 20, 'r': 21, 's': 22, 't': 23, 'u': 24,
        'v': 25, 'w': 26, 'x': 27, 'y': 28, 'z': 29,
        'A': 4, 'B': 5, 'C': 6, 'D': 7, 'E': 8, 'F': 9, 'G': 10,
        'H': 11, 'I': 12, 'J': 13, 'K': 14, 'L': 15, 'M': 16, 'N': 17,
        'O': 18, 'P': 19, 'Q': 20, 'R': 21, 'S': 22, 'T': 23, 'U': 24,
        'V': 25, 'W': 26, 'X': 27, 'Y': 28, 'Z': 29,
        '1': 30, '2': 31, '3': 32, '4': 33, '5': 34, '6': 35, '7': 36,
        '8': 37, '9': 38, '0': 39,
        ' ': 44, '\n': 40, '\t': 43,
        '!': 30, '@': 31, '#': 32, '$': 33, '%': 34, '^': 35,
        '&': 36, '*': 37, '(': 38, ')': 39,
        '-': 45, '_': 45, '=': 46, '+': 46,
        '[': 47, '{': 47, ']': 48, '}': 48, '\\': 49, '|': 49,
        ';': 51, ':': 51, "'": 52, '"': 52, '`': 53, '~': 53,
        ',': 54, '<': 54, '.': 55, '>': 55, '/': 56, '?': 56,
        'Backspace': 42, 'Tab': 43, 'Enter': 40, 'Escape': 41,
        'Left': 80, 'Right': 79, 'Up': 82, 'Down': 81,
        'Insert': 82, 'Delete': 83, 'Home': 74, 'End': 77,
        'PageUp': 75, 'PageDown': 78, 'F1': 58, 'F2': 59, 'F3': 60,
        'F4': 61, 'F5': 62, 'F6': 63, 'F7': 64, 'F8': 65,
        'F9': 66, 'F10': 67, 'F11': 68, 'F12': 69,
        'PrintScreen': 70, 'ScrollLock': 71, 'Pause': 72,
        'NumLock': 83, 'NumPad0': 82, 'NumPad1': 79, 'NumPad2': 80,
        'NumPad3': 81, 'NumPad4': 75, 'NumPad5': 76,
        'NumPad6': 77, 'NumPad7': 71, 'NumPad8': 72,
        'NumPad9': 73, 'NumPadPlus': 78, 'NumPadMinus': 74,
        'NumPadMultiply': 55, 'NumPadDivide': 53,
        'NumPadEnter': 40, 'NumPadDecimal': 83,
        'NumPadComma': 83, 'NumPadPeriod': 83,
        'NumPadEqual': 83,
    }

    def send_keyboard_command(self, keyboard_command):
        """
        Send a keyboard command via serial to type a key.
        expected format: "X1 X2 X3 X4 X5 X6 X7 X8"
        Args:
            key (str): The key to be typed.
        """
        self.ser.write((keyboard_command + "\n").encode("utf-8"))
        time.sleep(0.1)

    def keyboard(self, text):
        """
        Sends keyboard commands to the BLE mouse device.

        Args:
            text (str): The text to be sent as keyboard commands.

        Returns:
            None
        """
        # Convert each character of the command string to HID report and send it

        for char in text:
            key_code = self.hid_key_codes.get(char)
            # Check if the character is uppercase
            if char.isupper():
                # Send the shift key press
                keyboard_command = f"2 0 {key_code} 0 0 0 0 0"  # Shift key press
                self.send_keyboard_command(keyboard_command)
            else:
                # Convert char to HID report format
                keyboard_command = f"0 0 {key_code} 0 0 0 0 0"
                self.send_keyboard_command(keyboard_command)

        keyboard_command = "0 0 0 0 0 0 0 0"
        self.send_keyboard_command(keyboard_command)

    def launch_app(self, event_name: str | None) -> None:
        """
        Launch an application using BLE.

        :param event_name: The event triggering the app launch.
        :type event_name: str
        """
        raise NotImplementedError("BLE driver does not support launching apps.")

    def launch_other_app(self, app_name, event_name):
        raise NotImplementedError("BLE driver does not support launching apps.")

    def get_app_version(self) -> str:
        raise NotImplementedError("BLE driver does not support getting app version.")

    def press_element(self, element: str, repeat: int, event_name: str) -> None:
        raise NotImplementedError("BLE driver does not support pressing elements.")

    def press_percentage_coordinates(
        self,
        percentage_x: float,
        percentage_y: float,
        repeat: int,
        event_name: Optional[str] = None,
    ) -> None:
        """
        Press an element by percentage coordinates using BLE.

        :param percentage_x: X coordinate of the press as a percentage.
        :type percentage_x: float
        :param percentage_y: Y coordinate of the press as a percentage.
        :type percentage_y: float
        :param repeat: Number of times to repeat the press.
        :type repeat: int
        :param event_name: The event triggering the press.
        :type event_name: str | None
        """
        internal_logger.debug(
            f"Pressing percentage coordinates ({percentage_x}, {percentage_y}) via BLE."
        )
        x_coor = int(percentage_x * self.pixel_width)
        y_coor = int(percentage_y * self.pixel_height)
        self.translate_coordinates_relative_pixel(0, x_coor, y_coor)
        timestamp: str | None = self.event_sdk.get_current_time_for_events()
        for _ in range(repeat):
            self.mouse_tap()
            time.sleep(0.1)
        if event_name:
            self.event_sdk.capture_event_with_time_input(event_name, timestamp)

        self.mouse_reset_position()

    def enter_text(self, text: str, event_name: Optional[str] = None) -> None:
        """
        Enter text using BLE.

        :param text: The text to be entered.
        :type text: str
        :param event_name: The event triggering the text entry.
        :type event_name: str | None
        """
        internal_logger.debug(f"Entering text '{text}' via BLE.")
        if event_name:
            self.event_sdk.capture_event(event_name)
        self.keyboard(utils.strip_sensitive_prefix(text))

    def press_keycode(self, keycode, event_name) -> None:
        """
        Press a keycode using BLE.

        :param keycode: The keycode to be pressed.
        :type keycode: str
        :param event_name: The event triggering the key press.
        :type event_name: str
        """
        internal_logger.debug(f"Pressing keycode '{keycode}' via BLE.")
        if event_name:
            self.event_sdk.capture_event(event_name)
        self.keyboard(utils.strip_sensitive_prefix(keycode))

    def enter_text_element(self, element, text, event_name) -> None:
        raise NotImplementedError(
            "BLE driver does not support entering text into elements."
        )

    def enter_text_using_keyboard(self, text, event_name) -> None:
        """
        Enter text using the keyboard via BLE.

        :param text: The text to be entered.
        :type text: str
        :param event_name: The event triggering the text entry.
        :type event_name: str
        """
        internal_logger.debug(f"Entering text '{text}' using keyboard via BLE.")
        if event_name:
            self.event_sdk.capture_event(event_name)
        self.keyboard(utils.strip_sensitive_prefix(text))

    def clear_text(self, event_name) -> None:
        """
        Clear text using BLE.

        :param event_name: The event triggering the text clearing.
        :type event_name: str
        """
        internal_logger.debug("Clearing text via BLE.")
        self.send_keyboard_command(
            "1 0 4 0 0 0 0 0"
        )  # Send a command to select all text
        self.send_keyboard_command(
            "0 0 42 0 0 0 0 0"
        )  # Send a command to backspace the selected text
        if event_name:
            self.event_sdk.capture_event(event_name)

    def clear_text_element(self, element, event_name) -> None:
        internal_logger.debug(f"Clearing text in element '{element}' via BLE.")
        self.send_keyboard_command("1 0 4 0 0 0 0 0")  # Select all text
        self.send_keyboard_command("0 0 42 0 0 0 0 0")  # Backspace to clear text
        if event_name:
            self.event_sdk.capture_event(event_name)

    def swipe(self, x_coor, y_coor, direction, swipe_length, event_name) -> None:
        """
        Perform a swipe action using BLE.

        :param x_coor: X coordinate of the swipe.
        :type x_coor: int
        :param y_coor: Y coordinate of the swipe.
        :type y_coor: int
        :param direction: Direction of the swipe (e.g., "up", "down", "left", "right").
        :type direction: str
        :param swipe_length: Length of the swipe.
        :type swipe_length: int
        :param event_name: The event triggering the swipe.
        :type event_name: str
        """
        internal_logger.debug(
            f"Swiping {direction} from ({x_coor}, {y_coor}) with length {swipe_length} via BLE."
        )
        self.translate_coordinates_relative_pixel(0, x_coor, y_coor)
        if event_name:
            self.event_sdk.capture_event(event_name)
        self.swipe_ble(direction, swipe_length)

    def swipe_percentage(
        self, x_percentage, y_percentage, direction, swipe_percentage, event_name
    ) -> None:
        """
        Perform a swipe action using percentage coordinates via BLE.

        :param x_percentage: X coordinate of the swipe as a percentage.
        :type x_percentage: float
        :param y_percentage: Y coordinate of the swipe as a percentage.
        :type y_percentage: float
        :param direction: Direction of the swipe (e.g., "up", "down", "left", "right").
        :type direction: str
        :param swipe_percentage: Length of the swipe as a percentage.
        :type swipe_percentage: float
        :param event_name: The event triggering the swipe.
        :type event_name: str
        """
        internal_logger.debug(
            f"Swiping {direction} from ({x_percentage}, {y_percentage}) with length {swipe_percentage} via BLE."
        )
        x_coor = int(x_percentage * self.pixel_width)
        y_coor = int(y_percentage * self.pixel_height)
        swipe_length = int(swipe_percentage * self.pixel_width)
        self.translate_coordinates_relative_pixel(0, x_coor, y_coor)
        if event_name:
            self.event_sdk.capture_event(event_name)
        self.swipe_ble(direction, swipe_length)

    def swipe_element(self, element, direction, swipe_length, event_name) -> None:
        raise NotImplementedError("BLE driver does not support swiping elements.")

    def scroll(self, direction, duration, event_name) -> None:
        """
        Perform a scroll action using BLE.

        :param direction: Direction of the scroll (e.g., "up", "down", "left", "right").
        :type direction: str
        :param duration: Duration of the scroll in milliseconds.
        :type duration: int
        :param event_name: The event triggering the scroll.
        :type event_name: str
        """
        internal_logger.debug(
            f"Scrolling {direction} with duration {duration} via BLE."
        )
        if direction.lower() == "up":
            for _ in range(duration):
                self.keyboard(
                    "PageUp"
                )  # HID code for PageUp (usually 0x4b, but here sending '\x0b' as placeholder)
        elif direction.lower() == "down":
            for _ in range(duration):
                self.keyboard(
                    "PageDown"
                )  # HID code for PageDown (usually 0x4e, but here sending '\x4e' as placeholder)
        else:
            internal_logger.warning(f"Scroll direction '{direction}' not supported.")
        if event_name:
            self.event_sdk.capture_event(event_name)
        # Implementation for scrolling has to be added first at firmware level(Mouse HID characteristics.
        # for now, we will use keyboard style page up and down.

    def get_text_element(self, element) -> str:
        raise NotImplementedError(
            "BLE driver does not support getting text from elements."
        )

    def terminate(self) -> None:
        """
        Terminate the BLE connection.

        :return: None
        :rtype: None
        """
        internal_logger.debug("Closing all applications.")
        # self.send_keyboard_command("4 0 43 0 0 0 0 0")  # press Alt + Tab
        self.send_keyboard_command(
            "4 0 61 0 0 0 0 0"
        )  # press Alt + F4 to close the active window
        self.send_keyboard_command(
            "0 0 0 0 0 0 0 0"
        )
        internal_logger.debug("Terminating the BLE connection.")
        if self.ser.is_open:
            self.ser.close()
        else:
            internal_logger.warning("Serial port is not open.")
