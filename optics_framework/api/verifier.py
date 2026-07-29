from typing import Optional, Any, List

from optics_framework.common.error import OpticsError, Code
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
        self.capture_output_dir = self.execution_dir if builder.session_config.save_captures else None
        self.session = builder.session

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
        state = element_state.strip().lower()

        if state in ("visible", "invisible"):
            try:
                present = self.assert_presence(element, str(timeout), "any", event_name=None, fail=False)
            except OpticsError:
                present = False
            if state == "visible" and not present:
                raise OpticsError(Code.E0401, message=f"Element '{element}' is not visible.")
            if state == "invisible" and present:
                raise OpticsError(Code.E0401, message=f"Element '{element}' is visible but expected invisible.")
            if event_name:
                self.event_sdk.capture_event(event_name)

        elif state in ("enabled", "disabled"):
            located = None
            try:
                for result in self.strategy_manager.locate(element):
                    located = result.value
                    break
            except OpticsError:
                pass

            if located is None:
                raise OpticsError(Code.E0201, message=f"Element '{element}' not found.")
            if isinstance(located, tuple):
                raise OpticsError(Code.E0401, message="Cannot check enabled/disabled state for coordinate-based elements.")

            is_enabled = located.is_enabled()
            if state == "enabled" and not is_enabled:
                raise OpticsError(Code.E0401, message=f"Element '{element}' is not enabled.")
            if state == "disabled" and is_enabled:
                raise OpticsError(Code.E0401, message=f"Element '{element}' is enabled but expected disabled.")
            if event_name:
                self.event_sdk.capture_event(event_name)

        else:
            raise OpticsError(
                Code.E0403,
                message=f"Unknown element_state '{element_state}'. Expected: visible, invisible, enabled, disabled."
            )

    def _resolve_param(self, param: str) -> str:
        """Resolve a `${variable}` reference from session elements, returning its first value."""
        return utils.resolve_scalar_param(self.session, param)

    def assert_equality(self, output: Any, expression: Any, event_name: Optional[str] = None) -> bool:
        """
        Compares two values for equality. Both values are resolved from session
        elements if they are ``${variable}`` references before comparison.

        :param output: The actual value or ``${variable}`` reference.
        :param expression: The expected value or ``${variable}`` reference.
        :param event_name: The name of the event associated with the comparison, if any.
        :return: True if equal, False otherwise.
        """
        actual = self._resolve_param(str(output)).strip()
        expected = self._resolve_param(str(expression)).strip()
        result = actual == expected
        if result and event_name:
            self.event_sdk.capture_event(event_name)
        return result


    def assert_presence(self, elements: str, timeout_str: str = "30", rule: str = 'any', event_name: Optional[str] = None, fail=True) -> bool:
        """
        Asserts the presence of elements -- anywhere in the page/DOM, visible or not.

        :param elements: Comma-separated string of elements to check (Image templates, OCR templates, or XPaths).
        :param timeout: The time to wait for the elements in seconds.
        :param rule: The rule for verification ("any" or "all").
        :param event_name: The name of the event associated with the assertion, if any.
        :return: True if the rule is satisfied, False otherwise.
        """
        return self._assert_common(elements, timeout_str, rule, event_name, fail, method_name="assert_presence")

    def assert_visibility(self, elements: str, timeout_str: str = "30", rule: str = 'any', event_name: Optional[str] = None, fail=True) -> bool:
        """
        Asserts that elements are actually rendered/visible on screen right now -- distinct
        from :meth:`assert_presence`, which reports found even for elements that exist in
        the page/DOM but are off-screen (e.g. not yet scrolled into view).

        :param elements: Comma-separated string of elements to check (Image templates, OCR templates, or XPaths).
        :param timeout_str: The time to wait for the elements to become visible, in seconds.
        :param rule: The rule for verification ("any" or "all").
        :param event_name: The name of the event associated with the assertion, if any.
        :return: True if the rule is satisfied, False otherwise.
        """
        return self._assert_common(elements, timeout_str, rule, event_name, fail, method_name="assert_visibility")

    def _assert_common(
        self, elements: str, timeout_str: str, rule: str,
        event_name: Optional[str], fail: bool, method_name: str,
    ) -> bool:
        rule = rule.lower()
        timeout = int(timeout_str)
        elements_list = elements.split('|')

        grouped_elements = self._group_elements_by_type(elements_list)
        result_parts, timestamps = self._process_element_groups(grouped_elements, timeout, rule, method_name)

        if not result_parts:
            internal_logger.warning("No valid elements provided for assertion.")
            return False

        result = self._evaluate_rule(result_parts, rule)
        self._handle_result(result, timestamps, event_name, fail, rule, method_name)
        return result

    def _group_elements_by_type(self, elements_list: list) -> dict:
        """Group elements by their type (Text, XPath, Image)."""
        return {
            'Text': [el for el in elements_list if utils.determine_element_type(el) == 'Text'],
            'XPath': [el for el in elements_list if utils.determine_element_type(el) == 'XPath'],
            'Image': [el for el in elements_list if utils.determine_element_type(el) == 'Image']
        }

    def _process_element_groups(self, grouped_elements: dict, timeout: int, rule: str, method_name: str = "assert_presence") -> tuple:
        """Process each group of elements and collect results."""
        result_parts = []
        timestamps = []

        for elem_type, elem_group in grouped_elements.items():
            if elem_group:
                status, timestamp, annotated_frame = getattr(self.strategy_manager, method_name)(elem_group, elem_type, timeout, rule)
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

    def _handle_result(self, result: bool, timestamps: list, event_name: Optional[str], fail: bool, rule: str, method_name: str):
        """Handle the final result, including event capture and error raising."""
        if result:
            self._capture_success_event(timestamps, event_name)
        elif fail:
            assertion_label = method_name.replace("assert_", "").capitalize()
            raise AssertionError(f"{assertion_label} assertion failed based on rule: {rule}")

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
        internal_logger.debug(f"Validating screen for elements: {elements}")
        internal_logger.debug(f"Timeout: {timeout} and Rule: {rule}")
        try:
            self.assert_presence(elements, timeout, rule, event_name, fail=False)
            return True
        except OpticsError as e:
            internal_logger.info(f"Validate Screen: Elements not found. Error: {e}")
            return False
        except Exception as e:
            internal_logger.error(f"Failed to Validate Screen: {e}")
            return False

    def capture_screenshot(self, event_name: Optional[str] = None) -> str:
        """
        Captures a screenshot of the current screen.

        :param event_name: The name of the event associated with the screenshot capture, if any.
        :return: The path to the captured screenshot.
        """
        internal_logger.debug("Capturing screenshot")
        screenshot = self.strategy_manager.capture_screenshot()
        if screenshot is not None:
            internal_logger.debug("Screenshot captured successfully")
            utils.save_screenshot(screenshot, "capture_screenshot", output_dir=self.capture_output_dir)
            screenshot = utils.encode_numpy_to_base64(screenshot)
        else:
            internal_logger.warning("Screenshot capture returned None.")
            screenshot = ""
        if event_name:
            self.event_sdk.capture_event(event_name)
        return screenshot


    def capture_pagesource(self, event_name: Optional[str] = None) -> dict:
        """
        Captures the page source and timestamp of the current screen.

        :param event_name: The name of the event associated with the page source capture, if any.
        :return: Dict with "page_source" and "timestamp" keys.
        """
        internal_logger.debug("Capturing page source")
        result = self.strategy_manager.capture_pagesource()
        if result is not None:
            page_source, timestamp = result
            internal_logger.debug(f"Page source captured at timestamp: {timestamp}")
            utils.save_page_source(page_source, timestamp, self.capture_output_dir)
            if event_name:
                self.event_sdk.capture_event(event_name)
            return {"page_source": page_source, "timestamp": timestamp}
        raise ValueError("Page source capture returned None.")

    def _safe_capture_screenshot_np(self) -> Optional[Any]:
        """Capture a screenshot as a NumPy frame, returning None on failure.

        Bounds scaling and the API image are both derived from this frame; a capture
        failure must degrade to unscaled bounds rather than break element retrieval.
        """
        try:
            return self.strategy_manager.capture_screenshot()
        except Exception as e:
            internal_logger.warning("Failed to capture screenshot for element bounds scaling: %s", e)
            return None

    def _bounds_need_screenshot(self) -> bool:
        """Whether bounds scaling needs a screenshot for the current element source.

        Only Appium sources are rescaled (see ``utils.scale_interactive_element_bounds``),
        so for any other backend we skip the screenshot capture entirely rather than pay
        for a capture whose result would be discarded by a no-op scale.
        """
        source = self.element_source.active_instance
        return getattr(source, "REQUIRED_DRIVER_TYPE", None) == "appium"

    def _collect_interactive_elements(
        self, filter_config: Optional[List[str]], screenshot_np: Optional[Any]
    ) -> list:
        """Fetch interactive elements, CSV-escape their text/xpath, and scale their
        bounds into the given screenshot's pixel space.

        Shared by ``get_interactive_elements``, ``get_screen_elements``, and the serve
        workspace stream so all three return consistent, screenshot-aligned bounds from
        a single capture. Does not persist anything — callers decide whether to save.
        """
        elements = self.strategy_manager.get_interactive_elements(filter_config)
        for el in elements:
            if isinstance(el, dict):
                if "xpath" in el and el["xpath"] is not None:
                    el["xpath"] = utils.escape_csv_value(str(el["xpath"]))
                if "text" in el and el["text"] is not None:
                    el["text"] = utils.escape_csv_value(str(el["text"]))
        # Bounds come from the page source in the driver's window coordinate space;
        # scale them to the screenshot's pixel space so consumers can overlay them
        # directly (no-op on Android / non-Appium / when sizes already match).
        utils.scale_interactive_element_bounds(elements, self.element_source, screenshot_np)
        return elements

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None) -> list:
        """
        Retrieves a list of interactive elements on the current screen.

        XPath and text fields are converted to one-line, CSV-friendly form (newlines
        as \\n, tabs as \\t, etc.) so output can be pasted into elements.csv or similar.
        On Appium sources, element bounds are returned in the screenshot's pixel space.

        :param filter_config: Optional list of filter types (e.g., ["buttons", "inputs"]).
        :type filter_config: Optional[List[str]]
        :return: A list of interactive elements.
        """
        screenshot_np = self._safe_capture_screenshot_np() if self._bounds_need_screenshot() else None
        elements = self._collect_interactive_elements(filter_config, screenshot_np)
        utils.save_interactable_elements(elements, output_dir=self.execution_dir)
        return elements

    def get_screen_elements(self) -> dict:
        """
        Captures a screenshot and retrieves interactive elements for API response.

        Bounds are scaled to the returned screenshot's pixel space using a single
        capture shared between the image and the bounds, so callers can overlay the
        bounds on the screenshot without any scaling of their own.

        :return: Dict with base64-encoded screenshot and list of elements.
        """
        screenshot_np = self._safe_capture_screenshot_np()
        elements = self._collect_interactive_elements(None, screenshot_np)
        utils.save_interactable_elements(elements, output_dir=self.execution_dir)
        if screenshot_np is not None and self.capture_output_dir is not None:
            utils.save_screenshot(screenshot_np, "capture_screenshot", output_dir=self.capture_output_dir)
        base64_screenshot = (
            utils.encode_numpy_to_base64(screenshot_np) if screenshot_np is not None else ""
        )
        return {
            "screenshot": base64_screenshot,
            "elements": elements
        }
