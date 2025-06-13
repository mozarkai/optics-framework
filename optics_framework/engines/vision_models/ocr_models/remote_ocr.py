from typing import Dict, Any, Optional, List, Tuple
import requests
import json
import cv2
import numpy as np
import base64
from optics_framework.common.text_interface import TextInterface
from optics_framework.common import utils
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.config_handler import ConfigHandler


class RemoteOCR(TextInterface):
    DEPENDENCY_TYPE = "text_detection"
    NAME = "remote_ocr"

    def __init__(self):
        """Initialize the Remote OCR client with configuration."""
        config_handler: ConfigHandler = ConfigHandler().get_instance()
        config = config_handler.get_dependency_config(
            self.DEPENDENCY_TYPE, self.NAME)

        if not config:
            internal_logger.error(
                f"No configuration found for {self.DEPENDENCY_TYPE}: {self.NAME}")
            raise ValueError("Remote OCR is not enabled in config")

        self.ocr_url: str = config.get("url", "http://127.0.0.1:8080")
        self.capabilities: Dict[str, Any] = config.get("capabilities", {})
        self.timeout: int = self.capabilities.get("timeout", 30)
        self.method: str = self.capabilities.get("method", "easyocr")
        self.language: str = self.capabilities.get("language", "en")

    def detect_text(self, input_data: str) -> List[Tuple[List[Tuple[int, int]], str, float]]:
        """
        Detect text in an image via REST API and return text with bounding boxes.

        Args:
            input_data (str): Base64 encoded image string

        Returns:
            List[Tuple[List[Tuple[int, int]], str, float]]: List of detected texts with their bounding boxes
                                 Each tuple contains: (bbox, text, confidence)

        Raises:
            RuntimeError: If API request fails
        """
        try:
            payload = {
                "method": self.method,
                "image": input_data,
                "language": self.language
            }
            response = requests.post(
                f"{self.ocr_url}/detect-text",
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            result = response.json()

            formatted_results = []
            for item in result.get("results", []):
                text = item.get("text", "")
                bbox = item.get("bbox", [])
                confidence = item.get("confidence", 0.0)
                if len(bbox) >= 4:
                    formatted_results.append((bbox, text, confidence))
            return formatted_results

        except requests.exceptions.RequestException as e:
            internal_logger.error(f"Failed to detect text via API: {str(e)}")
            raise RuntimeError(
                f"Text detection API request failed: {str(e)}") from e
        except json.JSONDecodeError as e:
            internal_logger.error(f"Failed to parse API response: {str(e)}")
            raise RuntimeError(
                "Invalid response format from text detection API") from e

    def find_element(self, input_data: str, text: str, index: Optional[int] = None) -> Optional[Tuple[bool, Tuple[int, int], Tuple[Tuple[int, int], Tuple[int, int]]]]:
        """
        Locate multiple instances of a specific text in the given image using OCR and return the center coordinates
        of the text at the given index with bounding box coordinates.

        Parameters:
        - input_data (str): Base64 encoded image string
        - text (str): The text to locate in the image
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

        # Get all detected texts
        detections = self.detect_text(input_data)

        matching_elements = []

        for bbox, detected_text, _ in detections:
            if text.lower() in detected_text.lower() and len(bbox) >= 4:
                top_left = (int(bbox[0][0]), int(bbox[0][1]))
                bottom_right = (int(bbox[2][0]), int(bbox[2][1]))

                center_x = (top_left[0] + bottom_right[0]) // 2
                center_y = (top_left[1] + bottom_right[1]) // 2

                matching_elements.append(
                    (True, (center_x, center_y), (top_left, bottom_right))
                )
        # Determine the result
        if not matching_elements:
            internal_logger.debug(f"Text '{text}' not found in the image")
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

            cv2.rectangle(img, top_left, bottom_right, (0, 255, 0), 2)  # pylint: disable=no-member

            # Draw center point
            cv2.circle(img, center, 5, (0, 0, 255), -1)  # pylint: disable=no-member

            # Save the annotated screenshot
            utils.save_screenshot(img, "detected_text")

        return result

    def element_exist(self, input_data: str, reference_data: str) -> Tuple[int, int] | None:
        """
        Check if reference text exists in the input data and return its coordinates.

        Args:
            input_data (str): Text to search in
            reference_data (str): Text to search for

        Returns:
            Tuple[int, int] | None: Coordinates of the reference text if found, otherwise None
        """
        return super().element_exist(input_data, reference_data)
