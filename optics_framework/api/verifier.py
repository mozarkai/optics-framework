from typing import Optional, Any
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.strategies import StrategyManager
from optics_framework.common.eventSDK import EventSDK

class Verifier:
    """
    Provides methods to verify elements, screens, and data integrity.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(Verifier, cls).__new__(cls)
        return cls._instance

    def __init__(self, builder: OpticsBuilder):
        self.element_source = builder.get_element_source()
        self.image_detection = builder.get_image_detection()
        self.text_detection = builder.get_text_detection()
        self.strategy_manager = StrategyManager(
            self.element_source, self.text_detection, self.image_detection)
        self.event_sdk = EventSDK().get_instance()

    def validate_element(
        self,
        element: str,
        timeout: int = 10,
        rule: str = "all",
        event_name: Optional[str] = None,
    ) -> None:
        """
        Verifies the specified element.

        :param element: The element to be verified (Image template, OCR template, or XPath).
        :param timeout: The time to wait for verification in seconds.
        :param rule: The rule used for verification ("all" or "any").
        :param event_name: The name of the event associated with the verification, if any.
        """
        internal_logger.debug(f"Validating element: {element}")
        internal_logger.debug(f"Timeout: {timeout} and Rule: {rule}")
        self.assert_presence(element, timeout, rule, event_name)

    def is_element(
        self,
        element: str,
        element_state: str,
        timeout: int,
        event_name: Optional[str] = None,
    ) -> None:
        """
        Checks if the specified element is in a given state (e.g., Enabled/Disabled/Visible/Invisible).

        :param element: The element to be checked (Image template, OCR template, or XPath).
        :param element_state: The state to verify (visible, invisible, enabled, disabled).
        :param timeout: The time to wait for the element in seconds.
        :param event_name: The name of the event associated with the check, if any.
        """
        pass

    def assert_equality(self, output: Any, expression: Any, event_name: Optional[str] = None) -> None:
        """
        Compares two values for equality.

        :param output: The first value to be compared.
        :param expression: The second value to be compared.
        :param event_name: The name of the event associated with the comparison, if any.
        """
        pass


    def assert_presence(self, elements: str, timeout: int = 30, rule: str = 'any', event_name: Optional[str] = None, fail=True) -> bool:
        """
        Asserts the presence of elements.

        :param elements: Comma-separated string of elements to check (Image templates, OCR templates, or XPaths).
        :param timeout: The time to wait for the elements in seconds.
        :param rule: The rule for verification ("any" or "all").
        :param event_name: The name of the event associated with the assertion, if any.
        :return: True if the rule is satisfied, False otherwise.
        """

        rule = rule.lower()
        timeout = int(timeout)
        elements_list = elements.split(',')
        # Group elements by type
        grouped_elements = {
            'Text': [el for el in elements_list if utils.determine_element_type(el) == 'Text'],
            'XPath': [el for el in elements_list if utils.determine_element_type(el) == 'XPath'],
            'Image': [el for el in elements_list if utils.determine_element_type(el) == 'Image']
        }
        result_parts = []
        timestamps = []

        for elem_type, elem_group in grouped_elements.items():
            if elem_group:
                status, timestamp = self.strategy_manager.assert_presence(elem_group, elem_type, timeout, rule)
                result_parts.append(status)
                if timestamp:
                    timestamps.append(timestamp)

        if not result_parts:
            internal_logger.warning("No valid elements provided for assertion.")
            return False

        result = any(result_parts) if rule == 'any' else all(result_parts)
        if result:
            earliest_timestamp = min(timestamps) if timestamps else None
            if event_name and earliest_timestamp:
                self.event_sdk.capture_event_with_time_input(event_name, earliest_timestamp)

        elif fail:
            raise AssertionError("Presence assertion failed based on rule: " + rule)

        return result


    def validate_screen(self, elements: str, timeout: int = 30, rule: str = 'any', event_name: Optional[str] = None) -> None:
        """
        Verifies the specified screen by checking element presence.

        :param elements: Comma-separated string of elements to verify (Image templates, OCR templates, or XPaths).
        :param timeout: The time to wait for verification in seconds.
        :param rule: The rule for verification ("any" or "all").
        :param event_name: The name of the event associated with the verification, if any.
        """
        self.assert_presence(elements, timeout, rule, event_name, fail=False)

    def capture_screenshot(self, event_name: Optional[str] = None) -> str:
        """
        Captures a screenshot of the current screen.

        :param event_name: The name of the event associated with the screenshot capture, if any.
        :return: The path to the captured screenshot.
        """
        screenshot = self.strategy_manager.capture_screenshot()
        screenshot = utils.encode_numpy_to_base64(screenshot)
        if event_name:
            self.event_sdk.capture_event(event_name)
        return screenshot


    def capture_pagesource(self, event_name: Optional[str] = None) -> str:
        """
        Captures the page source of the current screen.

        :param event_name: The name of the event associated with the page source capture, if any.
        :return: The page source as a string.
        """
        page_source = self.strategy_manager.capture_pagesource()
        if event_name:
            self.event_sdk.capture_event(event_name)
        return page_source

    def get_interactive_elements(self) -> list:
        """
        Retrieves a list of interactive elements on the current screen.

        :return: A list of interactive elements.
        """
        elements = self.strategy_manager.get_interactive_elements()
        utils.save_interactable_elements(elements)
        return elements

    def get_screen_elements(self) -> dict:
        """
        Captures a screenshot and retrieves interactive elements for API response.

        :return: Dict with base64-encoded screenshot and list of elements.
        """
        screenshot_path = self.capture_screenshot()
        elements = self.get_interactive_elements()

        return {
            "screenshot": utils.encode_numpy_to_base64(screenshot_path),
            "elements": elements
        }
