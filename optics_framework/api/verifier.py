from typing import Optional, Any, List
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from optics_framework.common.optics_builder import OpticsBuilder
import time


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

    def vision_search(self, elements: List[str], timeout: int, rule: str) -> bool:
        """
        Performs a vision-based search for elements.

        :param elements: List of elements to search for (Image templates or OCR templates).
        :param timeout: The time to wait for elements to appear in seconds.
        :param rule: The rule for verification ("any" or "all").
        :return: True if the rule is satisfied, False otherwise.
        """
        rule = rule.lower()
        timeout = int(timeout)
        found_text = False
        found_image = False
        # Group elements by type
        texts = [
            el for el in elements if utils.determine_element_type(el) == 'Text']
        images = [
            el for el in elements if utils.determine_element_type(el) == 'Image']

        # Shared resources
        element_status = {
            'texts': {text: {'found': False, 'bbox': None} for text in texts},
            'images': {image: {'found': False, 'bbox': None} for image in images}
        }
        start_time = time.time()

        while (time.time() - start_time) < timeout:
            # Capture a screenshot
            # Get timestamp when the screenshot is taken
            timestamp = utils.get_current_time_for_events()
            frame = self.element_source.capture()

            if frame is None:
                internal_logger.debug("Screenshot capture failed. Skipping iteration.")
                continue

            internal_logger.debug(f"Screenshot captured at {timestamp}.")

            # Search for text elements
            if texts:
                found_text = self.assert_texts_vision(
                    frame, texts, element_status, rule)

            # Search for image elements
            if images:
                found_image = self.assert_images_vision(
                    frame, images, element_status, rule)

            # If rule is 'any' and either text or image is found, stop early
            if rule == 'any' and (found_text or found_image):
                utils.annotate_and_save(frame, element_status)
                return True

            # If rule is 'all' and all elements are found, stop early
            if rule == 'all' and all(
                item['found'] for status in element_status.values() for item in status.values()
            ):
                utils.annotate_and_save(frame, element_status)
                return True

            time.sleep(0.5)

        # Final annotation before returning
        utils.annotate_and_save(frame, element_status)
        return any(item['found'] for status in element_status.values() for item in status.values())

    def assert_texts_vision(self, frame: Any, texts: List[str], element_status: dict, rule: str) -> bool:
        """
        Searches for the given texts in a single frame using OCR.

        :param frame: The image frame to search in (e.g., numpy.ndarray).
        :param texts: List of text elements to search for.
        :param element_status: Dictionary storing found element statuses.
        :param rule: The rule for verification ("any" or "all").
        :return: True if the rule is satisfied, False otherwise.
        """
        found_any = False

        for text in texts[:]:  # Iterate over a copy to avoid modification issues
            if element_status['texts'][text]['found']:
                continue  # Skip if already found
            found, _, bbox = self.text_detection.find_element(frame, text)

            if found:
                if not element_status['texts'][text]['found']:
                    element_status['texts'][text] = {
                        'found': True, 'bbox': bbox}

                internal_logger.debug(f"Text '{text}' found at bbox: {bbox}.")
                found_any = True

                if rule == 'any':
                    return True  # Stop processing if 'any' rule is met
            else:
                internal_logger.debug(f"Text '{text}' not found in screenshot.")

        return found_any if rule == 'any' else all(
            item['found'] for item in element_status['texts'].values()
        )

    def assert_images_vision(self, frame: Any, images: List[str], element_status: dict, rule: str) -> bool:
        """
        Searches for the given images in a single frame using template matching.

        :param frame: The image frame to search in (e.g., numpy.ndarray).
        :param images: List of image templates to search for.
        :param element_status: Dictionary storing found element statuses.
        :param rule: The rule for verification ("any" or "all").
        :return: True if the rule is satisfied, False otherwise.
        """
        found_any = False

        for image in images[:]:  # Iterate over a copy to avoid modification issues
            if element_status['images'][image]['found']:
                continue  # Skip if already found

            result, _, bbox = self.image_detection.find_element(frame, image)

            if result:
                element_status['images'][image] = {'found': True, 'bbox': bbox}

                internal_logger.debug(f"Image '{image}' found at bbox: {bbox}.")
                found_any = True

                if rule == 'any':
                    return True  # Stop processing if 'any' rule is met
            else:
                internal_logger.debug(f"Image '{image}' not found.")

        return found_any if rule == 'any' else all(
            item['found'] for item in element_status['images'].values()
        )

    def assert_presence(self, elements: str, timeout: int = 30, rule: str = 'any', event_name: Optional[str] = None) -> bool:
        """
        Asserts the presence of elements.

        :param elements: Comma-separated string of elements to check (Image templates, OCR templates, or XPaths).
        :param timeout: The time to wait for the elements in seconds.
        :param rule: The rule for verification ("any" or "all").
        :param event_name: The name of the event associated with the assertion, if any.
        :return: True if the rule is satisfied, False otherwise.
        """
        element_source_type = type(
            self.element_source.current_instance).__name__
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
        result = False  # Initialize result with a default value
        try:
            if 'appium' in element_source_type.lower() or 'selenium' in element_source_type.lower():
                # Calls assert presence from appium driver
                if images:
                    internal_logger.error(
                        "Image search is not supported for Appium based search")
                    return False
                texts_xpaths = texts + xpaths
                result = self.element_source.assert_elements(
                    texts_xpaths, timeout, rule)
        except Exception as e:
            internal_logger.error(f"Exception occurred: {e}")
            # Vision search
            if xpaths:
                internal_logger.error(
                    "XPath search is not supported for Vision based search")
                return False
            texts_images = texts + images
            result = self.vision_search(texts_images, timeout, rule)

        if event_name:
            # Trigger event (placeholder)
            pass

        return bool(result)

    def validate_screen(self, elements: str, timeout: int = 30, rule: str = 'any', event_name: Optional[str] = None) -> None:
        """
        Verifies the specified screen by checking element presence.

        :param elements: Comma-separated string of elements to verify (Image templates, OCR templates, or XPaths).
        :param timeout: The time to wait for verification in seconds.
        :param rule: The rule for verification ("any" or "all").
        :param event_name: The name of the event associated with the verification, if any.
        """
        self.assert_presence(elements, timeout, rule, event_name)
