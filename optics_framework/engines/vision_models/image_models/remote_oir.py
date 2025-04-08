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
            success, center, (top_left, bottom_right) = result
            # Draw bounding box
            cv2.rectangle(img, top_left, bottom_right, (0, 255, 0), 2)
            # Draw center point
            cv2.circle(img, center, 5, (0, 0, 255), -1)
            # Save the annotated screenshot
            utils.save_screenshot(img, "detected_template")

        return result

    def element_exist(self, frame, reference_data: str) -> Tuple[int, int] | None:
        """
        Check if reference image exists in the input frame.

        Args:
            frame: Image data to search in
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
