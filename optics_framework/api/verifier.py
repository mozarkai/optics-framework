from typing import Optional, Any, List
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.strategies import StrategyManager
from optics_framework.common.eventSDK import EventSDK

class Verifier:
    """
    Provides methods to verify elements, screens, and data integrity.
    """

    def __init__(self, builder: OpticsBuilder):
        self.driver: InstanceFallback = builder.get_driver()
        self.element_source: InstanceFallback = builder.get_element_source()
        self.image_detection: Optional[InstanceFallback] = builder.get_image_detection()
        self.text_detection: Optional[InstanceFallback] = builder.get_text_detection()
        self.strategy_manager = StrategyManager(
            self.element_source, self.text_detection, self.image_detection
        )
        self.event_sdk: EventSDK = builder.event_sdk
        self.execution_dir = builder.session_config.execution_output_path

    def validate_element(
        self,
        element: str,
        timeout: str = "10",
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


    def assert_presence(self, elements: str, timeout_str: str = "30", rule: str = 'any', event_name: Optional[str] = None, fail=True) -> bool:
        """
        Asserts the presence of elements.

        :param elements: Comma-separated string of elements to check (Image templates, OCR templates, or XPaths).
        :param timeout: The time to wait for the elements in seconds.
        :param rule: The rule for verification ("any" or "all").
        :param event_name: The name of the event associated with the assertion, if any.
        :return: True if the rule is satisfied, False otherwise.
        """
        rule = rule.lower()
        timeout = int(timeout_str)
        elements_list = elements.split('|')

        grouped_elements = self._group_elements_by_type(elements_list)
        result_parts, timestamps = self._process_element_groups(grouped_elements, timeout, rule)

        if not result_parts:
            internal_logger.warning("No valid elements provided for assertion.")
            return False

        result = self._evaluate_rule(result_parts, rule)
        self._handle_result(result, timestamps, event_name, fail, rule)
        return result

    def _group_elements_by_type(self, elements_list: list) -> dict:
        """Group elements by their type (Text, XPath, Image)."""
        return {
            'Text': [el for el in elements_list if utils.determine_element_type(el) == 'Text'],
            'XPath': [el for el in elements_list if utils.determine_element_type(el) == 'XPath'],
            'Image': [el for el in elements_list if utils.determine_element_type(el) == 'Image']
        }

    def _process_element_groups(self, grouped_elements: dict, timeout: int, rule: str) -> tuple:
        """Process each group of elements and collect results."""
        result_parts = []
        timestamps = []

        for elem_type, elem_group in grouped_elements.items():
            if elem_group:
                status, timestamp, annotated_frame = self.strategy_manager.assert_presence(elem_group, elem_type, timeout, rule)
                result_parts.append(status)

                if timestamp:
                    timestamps.append(timestamp)

                if annotated_frame is not None:
                    self._save_annotated_screenshot(annotated_frame, timestamp)

        return result_parts, timestamps

    def _save_annotated_screenshot(self, annotated_frame, timestamp):
        """Save annotated screenshot with timestamp."""
        utils.save_screenshot(
            annotated_frame,
            "assert_elements_image_detection_result",
            time_stamp=timestamp,
            output_dir=self.execution_dir
        )

    def _evaluate_rule(self, result_parts: list, rule: str) -> bool:
        """Evaluate the rule against the result parts."""
        return any(result_parts) if rule == 'any' else all(result_parts)

    def _handle_result(self, result: bool, timestamps: list, event_name: Optional[str], fail: bool, rule: str):
        """Handle the final result, including event capture and error raising."""
        if result:
            self._capture_success_event(timestamps, event_name)
        elif fail:
            raise AssertionError(f"Presence assertion failed based on rule: {rule}")

    def _capture_success_event(self, timestamps: list, event_name: Optional[str]):
        """Capture success event with the earliest timestamp."""
        if event_name and timestamps:
            earliest_timestamp = min(timestamps)
            self.event_sdk.capture_event_with_time_input(event_name, earliest_timestamp)


    def validate_screen(self, elements: str, timeout: str = "30", rule: str = 'any', event_name: Optional[str] = None) -> bool:
        """
        Verifies the specified screen by checking element presence.

        :param elements: Comma-separated string of elements to verify (Image templates, OCR templates, or XPaths).
        :param timeout: The time to wait for verification in seconds.
        :param rule: The rule for verification ("any" or "all").
        :param event_name: The name of the event associated with the verification, if any.
        """
        return self.assert_presence(elements, timeout, rule, event_name, fail=False)

    def capture_screenshot(self, event_name: Optional[str] = None) -> str:
        """
        Captures a screenshot of the current screen.

        :param event_name: The name of the event associated with the screenshot capture, if any.
        :return: The path to the captured screenshot.
        """
        screenshot = self.strategy_manager.capture_screenshot()
        if screenshot is not None:
            screenshot = utils.encode_numpy_to_base64(screenshot)
        else:
            internal_logger.warning("Screenshot capture returned None.")
            screenshot = ""
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
        if page_source is not None:
            return page_source
        else:
            raise ValueError("Page source capture returned None.")

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None) -> list:
        """
        Retrieves a list of interactive elements on the current screen.

        :param filter_config: Optional list of filter types (e.g., ["buttons", "inputs"]).
        :type filter_config: Optional[List[str]]
        :return: A list of interactive elements.
        """
        elements = self.strategy_manager.get_interactive_elements(filter_config)
        utils.save_interactable_elements(elements, output_dir=self.execution_dir)
        return elements

    def get_screen_elements(self) -> dict:
        """
        Captures a screenshot and retrieves interactive elements for API response.

        :return: Dict with base64-encoded screenshot and list of elements.
        """
        base64_screenshot = self.capture_screenshot()
        elements = self.get_interactive_elements()

        return {
            "screenshot": base64_screenshot,
            "elements": elements
        }
