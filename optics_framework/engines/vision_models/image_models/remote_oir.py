from typing import Dict, Any, Optional, List, Tuple
import base64
import json
import cv2
import numpy as np
import requests
from optics_framework.common.image_interface import ImageInterface
from optics_framework.common import utils
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.engines.vision_models.base_methods import load_template

class RemoteImageDetection(ImageInterface):
    DEPENDENCY_TYPE = "image_detection"
    NAME = "remote_oir"

    def __init__(self):
        """Initialize the Remote Image Detection client with configuration."""
        config_handler: ConfigHandler = ConfigHandler().get_instance()
        config = config_handler.get_dependency_config(
            self.DEPENDENCY_TYPE, self.NAME)

        if not config:
            internal_logger.error(
                f"No configuration found for {self.DEPENDENCY_TYPE}: {self.NAME}")
            raise ValueError("Remote Image Detection is not enabled in config")

        self.detection_url: str = config.get("url", "http://127.0.0.1:8080")
        self.capabilities: Dict[str, Any] = config.get("capabilities", {})
        self.timeout: int = self.capabilities.get("timeout", 30)
        self.method: str = self.capabilities.get("method", "template_matching")

    def detect_images(self, image_base64: str, template_base64: str, detection_method: str) -> List[Dict[str, Any]]:
        """
        Detect template images in a source image via REST API.

        Args:
            image_base64 (str): Base64 encoded source image string
            template_base64 (str): Base64 encoded template image string
            detection_method (str): Name of the image detection method

        Returns:
            List[Dict[str, Any]]: List of detected image matches with their coordinates and bounding boxes
                                 Each dict contains: {'center': Tuple[int, int], 'bbox': List[Tuple[int, int]], 'confidence': float}

        Raises:
            RuntimeError: If API request fails
        """
        try:
            payload = {
                "method": detection_method,
                "image": image_base64,
                "template": template_base64
            }
            response = requests.post(
                f"{self.detection_url}/detect-image",
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            result = response.json()

            formatted_results = []
            for item in result.get("results", []):
                formatted_results.append({
                    "center": tuple(item.get("center", (0, 0))),
                    # Expected: [(x1,y1), (x2,y2)]
                    "bbox": item.get("bbox", []),
                    "confidence": item.get("confidence", 0.0)
                })

            internal_logger.debug(
                f"Successfully detected {len(formatted_results)} image instances")
            return formatted_results

        except requests.exceptions.RequestException as e:
            internal_logger.error(f"Failed to detect image via API: {str(e)}")
            raise RuntimeError(
                f"Image detection API request failed: {str(e)}") from e
        except json.JSONDecodeError as e:
            internal_logger.error(f"Failed to parse API response: {str(e)}")
            raise RuntimeError(
                "Invalid response format from image detection API") from e

    def find_element(self, input_data: str, image: str, index: Optional[int] = None) -> Optional[Tuple[bool, Tuple[int, int], Tuple[Tuple[int, int], Tuple[int, int]]]]:
        """
        Locate multiple instances of a template image in the given source image using remote detection
        and return the center coordinates and bounding box of the match at the given index.

        Parameters:
        - input_data (str): Base64 encoded source image string
        - template_data (str): Base64 encoded template image string
        - index (int): The index of the match to retrieve (default: None, returns first match)

        Returns:
        - Optional[Tuple[bool, Tuple[int, int], Tuple[Tuple[int, int], Tuple[int, int]]]]:
            Tuple of (success, center coordinates, bounding box) if found, None if not found or index out of bounds
        """
        # Decode base64 input to image
        try:
            img_data = base64.b64decode(input_data)
            nparr = np.frombuffer(img_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        except Exception as e:
            internal_logger.error(f"Failed to decode input image: {str(e)}")
            return None

        # fetch template image and decode to base64
        template_image = load_template(image)
        image = utils.encode_numpy_to_base64(template_image)

        # Get all detected images
        detected_images = self.detect_images(
            input_data, image, self.method)

        matching_elements = []

        # Process detected images
        for detection in detected_images:
            center = detection["center"]
            bbox = detection["bbox"]
            if len(bbox) >= 2:  # Ensure we have valid bounding box coordinates
                top_left = (int(bbox[0][0]), int(bbox[0][1]))
                bottom_right = (int(bbox[1][0]), int(bbox[1][1]))
                matching_elements.append(
                    (True, center, (top_left, bottom_right)))

        # Determine the result
        if not matching_elements:
            internal_logger.debug(
                "Template image not found in the source image")
            result = None
        elif index is not None:
            if 0 <= index < len(matching_elements):
                result = matching_elements[index]
            else:
                internal_logger.debug(
                    f"Index {index} out of bounds for {len(matching_elements)} matches")
                result = None
        else:
            result = matching_elements[0]

        # Annotate and save screenshot if a match was found
        if result is not None:
            _, center, (top_left, bottom_right) = result
            # Draw bounding box
            cv2.rectangle(img, top_left, bottom_right, (0, 255, 0), 2)
            # Draw center point
            cv2.circle(img, center, 5, (0, 0, 255), -1)
            # Save the annotated screenshot
            utils.save_screenshot(img, "detected_template")

        return result

    def element_exist(self, input_data, reference_data: str) -> Tuple[int, int] | None:
        """
        Check if reference image exists in the input frame.

        Args:
            input_data: Image data to search in
            reference_data: Template image to search for

        Returns:
            Tuple[int, int] | None: Coordinates if found, None otherwise
        """
        raise NotImplementedError(
            "The 'element_exist' method is not implemented for RemoteImageDetection. Use 'find_element' instead.")

    def locate(self, frame, element, index=None) -> Tuple[int, int] | None:
        """
        Locate an element in the frame.

        Args:
            frame: Image data
            element: Element to locate
            index: Index of match to return

        Returns:
            Tuple[int, int] | None: Coordinates if found, None otherwise
        """
        raise NotImplementedError(
            "The 'locate' method is not implemented for RemoteImageDetection. Use 'find_element' instead.")

    def assert_elements(self, input_data, elements, rule="any") -> Tuple[bool, np.ndarray]:
        """
        Assert that one or more template images are present in the given frame using remote detection.

        Args:
            input_data (np.ndarray): The source image/frame to search in.
            elements (list): List of template image names or paths to locate.
            rule (str, optional): Rule to apply for matching; "any" (default) returns True if any template matches,
                      "all" returns True only if all templates match.

        Returns:
            None
        """
        annotated_frame = input_data.copy()
        found_status = []

        # encode frame to base64
        frame_base64 = utils.encode_numpy_to_base64(input_data)
        encoded_templates = self._prepare_encoded_templates(elements)

        found_status = []

        for template_name, encoded_template in encoded_templates.items():
            match_found = self._detect_and_match_template(
                frame_base64, template_name, encoded_template, annotated_frame)
            found_status.append(match_found)

        match_rule = any(found_status) if rule == "any" else all(found_status)

        if match_rule:
            return True, annotated_frame
        internal_logger.warning("Remote template matching failed.")
        return False, annotated_frame

    def _prepare_encoded_templates(self, templates: list) -> Dict[str, Optional[str]]:
        encoded = {}
        for template in templates:
            try:
                img = load_template(template)
                encoded[template] = utils.encode_numpy_to_base64(img)
            except Exception as e:
                internal_logger.error(f"Failed to load or encode template '{template}': {e}")
                encoded[template] = None
        return encoded

    def _detect_and_match_template(self, frame_base64: str, template_name: str, encoded_template: Optional[str], frame: np.ndarray) -> bool:
        if not encoded_template:
            internal_logger.error(f"Template image '{template_name}' could not be loaded or encoded.")
            return False

        try:
            detections = self.detect_images(frame_base64, encoded_template, self.method)
        except Exception as e:
            internal_logger.error(f"Detection failed for '{template_name}': {e}")
            return False

        match_found = False
        for detection in detections:
            center = detection["center"]
            bbox = detection["bbox"]
            if len(bbox) >= 2:
                top_left = (int(bbox[0][0]), int(bbox[0][1]))
                bottom_right = (int(bbox[1][0]), int(bbox[1][1]))
                cv2.rectangle(frame, top_left, bottom_right, (0, 255, 0), 2)
                cv2.circle(frame, center, 5, (0, 0, 255), -1)
                match_found = True

        return match_found
