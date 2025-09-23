from functools import wraps
import time
from typing import Callable, Optional, Any
from optics_framework.common.logging_config import internal_logger, execution_logger
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.strategies import StrategyManager
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common import utils
from optics_framework.common.error import OpticsError, Code
from .verifier import Verifier

# Action Executor Decorator
def with_self_healing(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(self, element, *args, **kwargs):
        screenshot_np = self.strategy_manager.capture_screenshot()

        # Extract AOI parameters from kwargs if present and convert to float
        def parse_aoi_param(param, default_value):
            if param is None or str(param).strip() in ('', 'None', 'none'):
                return default_value
            return float(param)

        aoi_x = parse_aoi_param(kwargs.pop('aoi_x', '0'), 0)
        aoi_y = parse_aoi_param(kwargs.pop('aoi_y', '0'), 0)
        aoi_width = parse_aoi_param(kwargs.pop('aoi_width', '100'), 100)
        aoi_height = parse_aoi_param(kwargs.pop('aoi_height', '100'), 100)

        # Extract index parameter from kwargs if present
        index = int(kwargs.pop('index', 0))

        # Check if AOI is being used (i.e., AOI parameters after parsing are not the default float values: 0, 0, 100, 100)
        is_aoi_used = not (aoi_x == 0 and aoi_y == 0 and aoi_width == 100 and aoi_height == 100)

        # Save screenshot with AOI annotation if AOI is used
        if is_aoi_used:
            annotated_screenshot = utils.annotate_aoi_region(screenshot_np, aoi_x, aoi_y, aoi_width, aoi_height)
            utils.save_screenshot(annotated_screenshot, f"{func.__name__}_with_aoi", output_dir=self.execution_dir)
        else:
            utils.save_screenshot(screenshot_np, func.__name__, output_dir=self.execution_dir)

        # Pass AOI parameters to locate if provided
        if is_aoi_used:
            results = self.strategy_manager.locate(element, aoi_x, aoi_y, aoi_width, aoi_height, index=index)
        else:
            results = self.strategy_manager.locate(element, index=index)

        last_exception = None
        result_count = 0
        for result in results:
            result_count += 1
            try:
                return func(self, element, located=result.value, *args, **kwargs)
            except Exception as e:
                internal_logger.error(
                    f"Action '{func.__name__}' failed with {result.strategy.__class__.__name__}: {e}")
                last_exception = e

        if result_count == 0:
            # No strategies yielded a result
            raise OpticsError(Code.E0201, message=f"No valid strategies found for '{element}' in '{func.__name__}'")
        if last_exception:
            raise OpticsError(Code.X0201, message=f"All strategies failed for '{element}' in '{func.__name__}': {last_exception}", cause=last_exception)
        raise OpticsError(Code.E0801, message=f"Unexpected failure: No results or exceptions for '{element}' in '{func.__name__}'")
    return wrapper


class ActionKeyword:
    """
    High-Level API for Action Keywords

    This class provides functionality for managing action keywords related to applications,
    including pressing elements, scrolling, swiping, and text input.
    """
    SCREENSHOT_DISABLED_MSG = "Screenshot taking is disabled, not possible to locate element."
    XPAHT_NOT_SUPPORTED_MSG = "XPath is not supported for vision based search."

    def __init__(self, builder: OpticsBuilder):
        self.driver: InstanceFallback = builder.get_driver()
        self.element_source: InstanceFallback = builder.get_element_source()
        self.image_detection: Optional[InstanceFallback] = builder.get_image_detection()
        self.text_detection: Optional[InstanceFallback] = builder.get_text_detection()
        self.verifier = Verifier(builder)
        # Unwrap InstanceFallback to pass current_instance to StrategyManager

        self.strategy_manager = StrategyManager(
            self.element_source, self.text_detection, self.image_detection
        )
        self.execution_dir = builder.session_config.execution_output_path

    # Click actions
    @with_self_healing
    def press_element(
        self, element: str, repeat: str = "1", offset_x: str = "0", offset_y: str = "0", index: str = "0", aoi_x: str = "0",
        aoi_y: str = "0", aoi_width: str = "100", aoi_height: str = "100", event_name: Optional[str] = None,
        *, located: Any = None
        ) -> None:
        """
        Press a specified element.

        :param element: The element to be pressed (text, xpath or image).
        :param repeat: Number of times to repeat the press.
        :param offset_x: X offset of the press.
        :param offset_y: Y offset of the press.
        :param index: Index of the element if multiple matches are found.
        :param event_name: The event triggering the press.
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        """
        if isinstance(located, tuple):
            x, y = located
            execution_logger.info(
                f"Pressing at coordinates ({x + int(offset_x)}, {y + int(offset_y)}) with offset ({offset_x}, {offset_y})")
            self.driver.press_coordinates(
                x + int(offset_x), y + int(offset_y), event_name)
        else:
            execution_logger.info(f"Pressing element '{element}'")
            self.driver.press_element(located, int(repeat), event_name)

    def press_by_percentage(self, percent_x: str, percent_y: str, repeat: str = "1", event_name: Optional[str] = None) -> None:
        """
        Press an element by percentage coordinates.

        :param percent_x: X percentage of the press (as string, will be converted to float).
        :param percent_y: Y percentage of the press (as string, will be converted to float).
        :param repeat: Number of times to repeat the press.
        :param event_name: The event triggering the press.
        """
        screenshot_np = self.strategy_manager.capture_screenshot()
        utils.save_screenshot(screenshot_np, "press_by_percentage", output_dir=self.execution_dir)
        self.driver.press_percentage_coordinates(
            float(percent_x), float(percent_y), int(repeat), event_name
        )

    def press_by_coordinates(self, coor_x: str, coor_y: str, repeat: str = "1", event_name: Optional[str] = None) -> None:
        """
        Press an element by absolute coordinates.

        :param coor_x: X coordinate of the press.
        :param coor_y: Y coordinate of the press.
        :param repeat: Number of times to repeat the press.
        :param event_name: The event triggering the press.
        """
        screenshot_np = self.strategy_manager.capture_screenshot()
        utils.save_screenshot(screenshot_np, "press_by_coordinates", output_dir=self.execution_dir)
        execution_logger.info(f'Pressing by coordinates: ({coor_x}, {coor_y})')
        self.driver.press_coordinates(int(coor_x), int(coor_y), event_name)


    def detect_and_press(self, element: str, timeout: str = "30", event_name: Optional[str] = None) -> None:
        """
        Detect and press a specified element.

        :param element: The element to be detected and pressed (Image template, OCR template, or XPath).
        :param timeout: Timeout for the detection operation.
        :param event_name: The event triggering the press.
        """
        try:
            result = self.verifier.validate_screen(
                element, timeout, rule="any")
        except Exception as e:
            execution_logger.error(f"Error in detect_and_press: {e}")
            result = False
        if result:
            execution_logger.info(f'Element {element} detected. Performing Press ... ')
            self.press_element(element, event_name=event_name)
        else:
            execution_logger.info(f'Element {element} not found. Press is not performed.')

    @DeprecationWarning
    @with_self_healing
    def press_checkbox(self, element: str, aoi_x: str = "0", aoi_y: str = "0", aoi_width: str = "100",
                       aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Press a specified checkbox element.

        :param element: The checkbox element (Image template, OCR template, or XPath).
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the press.
        """
        self.press_element(element, aoi_x=aoi_x, aoi_y=aoi_y, aoi_width=aoi_width,
                          aoi_height=aoi_height, event_name=event_name, located=located)

    @DeprecationWarning
    @with_self_healing
    def press_radio_button(self, element: str, aoi_x: str = "0", aoi_y: str = "0", aoi_width: str = "100",
                           aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Press a specified radio button.

        :param element: The radio button element (Image template, OCR template, or XPath).
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the press.
        """
        self.press_element(element, aoi_x=aoi_x, aoi_y=aoi_y, aoi_width=aoi_width,
                          aoi_height=aoi_height, event_name=event_name, located=located)

    def select_dropdown_option(self, element: str, option: str, event_name: Optional[str] = None) -> None:
        """
        Select a specified dropdown option.

        :param element: The dropdown element (Image template, OCR template, or XPath).
        :param option: The option to be selected.
        :param event_name: The event triggering the selection.
        """
        pass

    # Swipe and Scroll actions
    def swipe(self, coor_x: str, coor_y: str, direction: str = 'right', swipe_length: str = "50", event_name: Optional[str] = None) -> None:
        """
        Perform a swipe action in a specified direction.

        :param coor_x: X coordinate of the swipe.
        :param coor_y: Y coordinate of the swipe.
        :param direction: The swipe direction (up, down, left, right).
        :param swipe_length: The length of the swipe.
        :param event_name: The event triggering the swipe.
        """
        screenshot_np = self.strategy_manager.capture_screenshot()
        utils.save_screenshot(screenshot_np, "swipe", output_dir=self.execution_dir)
        execution_logger.info(f'Swiping from ({coor_x}, {coor_y}) to the {direction} with length {swipe_length}')
        self.driver.swipe(int(coor_x), int(coor_y), direction, int(swipe_length), event_name)

    @DeprecationWarning
    def swipe_seekbar_to_right_android(self, element: str, event_name: Optional[str] = None) -> None:
        """
        Swipe a seekbar to the right.

        :param element: The seekbar element (Image template, OCR template, or XPath).
        """
        screenshot_np = self.strategy_manager.capture_screenshot()
        utils.save_screenshot(screenshot_np, "swipe_seekbar_to_right_android", output_dir=self.execution_dir)
        execution_logger.info(f'Swiping seekbar element: {element} to the right')
        self.driver.swipe_element(element, 'right', 50, event_name)

    def swipe_until_element_appears(self, element: str, direction: str, timeout: str, event_name: Optional[str] = None) -> None:
        """
        Swipe in a specified direction until an element appears.

        :param element: The target element (Image template, OCR template, or XPath).
        :param direction: The swipe direction (up, down, left, right).
        :param timeout: Timeout until element search is performed.
        :param event_name: The event triggering the swipe.
        """
        screenshot_np = self.strategy_manager.capture_screenshot()
        utils.save_screenshot(screenshot_np, "swipe_until_element_appears", output_dir=self.execution_dir)
        start_time = time.time()
        while time.time() - start_time < int(timeout):
            result = self.verifier.assert_presence(
                element, timeout_str="3", rule="any")
            if result:
                break
            self.driver.swipe_percentage(10, 50, direction, 25, event_name)
            time.sleep(3)

    @with_self_healing
    def swipe_from_element(self, element: str, direction: str, swipe_length: str, aoi_x: str = "0", aoi_y: str = "0",
                          aoi_width: str = "100", aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Perform a swipe action starting from a specified element.

        :param element: The element to swipe from (Image template, OCR template, or XPath).
        :param direction: The swipe direction (up, down, left, right).
        :param swipe_length: The length of the swipe.
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the swipe.
        """
        if isinstance(located, tuple):
            x, y = located
            execution_logger.debug(f"Swiping from coordinates ({x}, {y})")
            self.driver.swipe(x, y, direction, int(swipe_length), event_name)
        else:
            execution_logger.debug(f"Swiping from element '{element}'")
            self.driver.swipe_element(
                located, direction, int(swipe_length), event_name)

    def scroll(self, direction: str, event_name: Optional[str] = None) -> None:
        """
        Perform a scroll action in a specified direction.

        :param direction: The scroll direction (up, down, left, right).
        :param event_name: The event triggering the scroll.
        """
        screenshot_np = self.strategy_manager.capture_screenshot()
        utils.save_screenshot(screenshot_np, "scroll", output_dir=self.execution_dir)
        execution_logger.info(f"Scrolling {direction} with event {event_name}")
        self.driver.scroll(direction, 1000, event_name)

    def scroll_until_element_appears(self, element: str, direction: str, timeout: str, event_name: Optional[str] = None) -> None:
        """
        Scroll in a specified direction until an element appears.

        :param element: The target element (Image template, OCR template, or XPath).
        :param direction: The scroll direction (up, down, left, right).
        :param timeout: Timeout for the scroll operation.
        :param event_name: The event triggering the scroll.
        """
        screenshot_np = self.strategy_manager.capture_screenshot()
        utils.save_screenshot(screenshot_np, "scroll_until_element_appears", output_dir=self.execution_dir)
        start_time = time.time()
        while time.time() - start_time < int(timeout):
            result = self.verifier.assert_presence(
                element, timeout_str="3", rule="any")
            if result:
                break
            self.driver.scroll(direction, 1000, event_name)
            time.sleep(3)

    @with_self_healing
    def scroll_from_element(self, element: str, direction: str, scroll_length: str, aoi_x: str = "0", aoi_y: str = "0",
                           aoi_width: str = "100", aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Perform a scroll action starting from a specified element.

        :param element: The element to scroll from (Image template, OCR template, or XPath).
        :param direction: The scroll direction (up, down, left, right).
        :param scroll_length: The length of the scroll.
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the scroll.
        """
        if isinstance(located, tuple):
            x, y = located
            execution_logger.debug(f"Swiping from coordinates ({x}, {y})")
            self.driver.swipe(x, y, direction, int(scroll_length), event_name)
        else:
            execution_logger.debug(f"Swiping from element '{element}'")
            self.driver.swipe_element(
                located, direction, int(scroll_length), event_name)

    # Text input actions
    @with_self_healing
    def enter_text(self, element: str, text: str, aoi_x: str = "0", aoi_y: str = "0", aoi_width: str = "100",
                   aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Enter text into a specified element.

        :param element: The target element (Image template, OCR template, or XPath).
        :param text: The text to be entered.
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the input.
        """

        special_key = utils.parse_special_key(text)
        if special_key:
            text = special_key

        if isinstance(located, tuple):
            x, y = located
            execution_logger.debug(f"Entering text '{text}' at coordinates ({x}, {y})")
            self.driver.press_coordinates(x, y, event_name=event_name)
            self.driver.enter_text(text, event_name)
        else:
            execution_logger.debug(f"Entering text '{text}' into element '{element}'")
            self.driver.enter_text_element(located, text, event_name)

    def enter_text_direct(self, text: str, event_name: Optional[str] = None) -> None:
        """
        Enter text using the keyboard.

        :param text: The text to be entered.
        :param event_name: The event triggering the input.
        """
        try:
            screenshot_np = self.strategy_manager.capture_screenshot()
            utils.save_screenshot(screenshot_np, "enter_text_keyboard", output_dir=self.execution_dir)
        except Exception as e:
            execution_logger.error(f"Error capturing screenshot: {e}")
        execution_logger.info(f'Entering text directly: {text}')
        self.driver.enter_text(text, event_name)

    def enter_text_using_keyboard(self, text_input: str, event_name: Optional[str] = None) -> None:
        """
        Enter text or press a special key using the keyboard.

        If the input is a string that includes angle brackets (e.g., '<enter>'),
        the text between the brackets will be interpreted as a special key name and mapped accordingly.

        :param input: The text or special key identifier to send.
        :param event_name: Optional event label for logging.
        """

        special_key = utils.parse_special_key(text_input)
        if special_key:
            text_input = special_key
        try:
            screenshot_np = self.strategy_manager.capture_screenshot()
            utils.save_screenshot(screenshot_np, "enter_text_using_keyboard", output_dir=self.execution_dir)
        except Exception as e:
            execution_logger.error(f"Error capturing screenshot: {e}")
        execution_logger.info(f'Entering text using keyboard: {text_input}')
        self.driver.enter_text_using_keyboard(text_input, event_name)

    @with_self_healing
    def enter_number(self, element: str, number: str, aoi_x: str = "0", aoi_y: str = "0", aoi_width: str = "100",
                     aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Enter a specified number into an element.

        :param element: The target element (Image template, OCR template, or XPath).
        :param number: The number to be entered.
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the input.
        """
        if isinstance(located, tuple):
            x, y = located
            execution_logger.debug(f"Entering number '{number}' at coordinates ({x}, {y})")
            self.driver.press_coordinates(x, y, event_name=event_name)
            self.driver.enter_text(str(number), event_name)
        elif isinstance(located, str):
            self.driver.enter_text_element(located, str(number), event_name)
        else:
            execution_logger.error(
                "Element location %s is not provided correctly for entering number.", element)
            raise ValueError(
                f"Element location {element} is not provided correctly for entering number."
            )

    def press_keycode(self, keycode: str, event_name: Optional[str] = None) -> None:
        """
        Press a specified keycode.

        :param keycode: The keycode to be pressed.
        :param event_name: The event triggering the press.
        """
        try:
            screenshot_np = self.strategy_manager.capture_screenshot()
            utils.save_screenshot(screenshot_np, "press_keycode", output_dir=self.execution_dir)
        except Exception as e:
            execution_logger.error(f"Error capturing screenshot: {e}")

        execution_logger.info(f"Pressing keycode: {keycode}")
        self.driver.press_keycode(keycode, event_name)


    @with_self_healing
    def clear_element_text(self, element: str, aoi_x: str = "0", aoi_y: str = "0", aoi_width: str = "100",
                          aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Clear text from a specified element.

        :param element: The target element (Image template, OCR template, or XPath).
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the action.
        """
        if isinstance(located, tuple):
            x, y = located
            execution_logger.debug(f"Clearing text at coordinates ({x}, {y})")
            self.driver.press_coordinates(
                x, y, event_name=event_name)
            self.driver.clear_text(event_name)
        else:
            execution_logger.debug(f"Clearing text from element '{element}'")
            self.driver.clear_text_element(located, event_name)

    def get_text(self, element: str) -> Optional[str]:
        """
        Get the text from a specified element.

        :param element: The target element (Image template, OCR template, or XPath).
        :return: The text from the element or None if not supported.
        """
        screenshot_np = self.strategy_manager.capture_screenshot()
        utils.save_screenshot(screenshot_np, "get_text", output_dir=self.execution_dir)
        element_source_type = type(
            self.element_source.current_instance).__name__
        element_type = utils.determine_element_type(element)
        if element_type in ["Text", "XPath"]:
            if 'appium' in element_source_type.lower():
                result = self.element_source.locate(element)
                if result is not None:
                    return self.driver.get_text_element(result)
                else:
                    internal_logger.error('Locate returned None for get_text.')
                    return None
            else:
                internal_logger.error(
                    'Get Text is not supported for vision based search yet.')
                return None
        else:
            internal_logger.error(
                'Get Text is not supported for image based search yet.')
            return None

    def sleep(self, duration: str) -> None:
        """
        Sleep for a specified duration.

        :param duration: The duration of the sleep in seconds.
        """
        time.sleep(int(duration))
