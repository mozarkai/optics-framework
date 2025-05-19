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
        texts = [
            el for el in elements_list if utils.determine_element_type(el) == 'Text']
        xpaths = [
            el for el in elements_list if utils.determine_element_type(el) == 'XPath']
        images = [
            el for el in elements_list if utils.determine_element_type(el) == 'Image']
        result_parts = []

        if texts:
            result_parts.append(self.strategy_manager.assert_presence(texts, 'Text', timeout, rule))
        if xpaths:
            result_parts.append(self.strategy_manager.assert_presence(xpaths, 'XPath', timeout, rule))
        if images:
            result_parts.append(self.strategy_manager.assert_presence(images, 'Image', timeout, rule))
        if not result_parts:
            internal_logger.warning("No valid elements provided for assertion.")
            return False

        if rule == 'any':
            result = any(result_parts)

        else:  # rule == 'all'
            result = all(result_parts)

        if event_name and result:
            self.event_sdk.capture_event(event_name)

        if not result and fail:
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
