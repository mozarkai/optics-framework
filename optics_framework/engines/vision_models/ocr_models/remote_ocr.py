from typing import Dict, Any, Optional, List, Tuple, Union
import requests
import json
import cv2
import numpy as np
import base64
from optics_framework.common.text_interface import TextInterface
from optics_framework.common import utils
from optics_framework.common.config_handler import Config
from optics_framework.common.logging_config import internal_logger


class RemoteOCR(TextInterface):
    DEPENDENCY_TYPE = "text_detection"
    NAME = "remote_ocr"

    def __init__(self, config: Union[Config, Dict[str, Any]]):
        """Initialize the Remote OCR client with configuration.

        Accept either a `Config` Pydantic object or a plain dict-like config for
        convenience in tests and REPL usage.
        """
        if not config:
            internal_logger.error(
                f"No configuration found for {self.DEPENDENCY_TYPE}: {self.NAME}")
            raise ValueError("Remote OCR is not enabled in config")
        internal_logger.debug(f"Using configuration for {self.DEPENDENCY_TYPE}: {self.NAME}")
        internal_logger.debug(f"Remote OCR config: {config}")
        # Provide a unified getter for both dicts and Config objects
        self.ocr_url = config.get("url", None)
        caps = config.get("capabilities", {}) or {}
        # ensure capabilities is a plain dict
        self.capabilities: Dict[str, Any] = dict(caps)
        self.timeout: int = int(self.capabilities.get("timeout", 30))
        self.method: str = str(self.capabilities.get("method", "easyocr"))
        self.language: str = str(self.capabilities.get("language", "en"))

    def detect_text(self, input_data: Union[str, "np.ndarray"]) -> Optional[Tuple[str, List[Tuple[List[Tuple[int, int]], str, float]]]]:
        """
        Detect text in an image via REST API and return text with bounding boxes.

        Args:
            input_data (str | np.ndarray): Base64 encoded image string or an image as a numpy array.

        Returns:
            Optional[Tuple[str, List[Tuple[List[Tuple[int, int]], str, float]]]]: Detected text and bounding boxes.
        """
        try:
            image_b64 = self._encode_image(input_data)
            payload = {
                "method": self.method,
                "image": image_b64,
                "language": self.language
            }
            response = requests.post(
                f"{self.ocr_url}/detect-text",
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            result = response.json()
            detected_text, formatted_results = self._parse_ocr_results(result)
            return detected_text, formatted_results
        except requests.exceptions.RequestException as e:
            internal_logger.error(f"Failed to detect text via API: {str(e)}")
            raise RuntimeError(
                f"Text detection API request failed: {str(e)}") from e
        except json.JSONDecodeError as e:
            internal_logger.error(f"Failed to parse API response: {str(e)}")
            raise RuntimeError(
                "Invalid response format from text detection API") from e
        except Exception as e:
            internal_logger.error(f"Unexpected error occurred: {str(e)}")
            raise RuntimeError(
                "Unexpected error occurred during text detection") from e

    def _encode_image(self, input_data: Union[str, "np.ndarray"]) -> str:
        """Helper to encode image input to base64 string."""
        if np is not None and isinstance(input_data, np.ndarray):
            success, enc = cv2.imencode('.png', input_data)
            if not success:
                raise RuntimeError("Failed to encode numpy image to PNG")
            return base64.b64encode(enc.tobytes()).decode()
        elif isinstance(input_data, (bytes, bytearray)):
            return base64.b64encode(bytes(input_data)).decode()
        elif isinstance(input_data, str):
            return input_data
        else:
            raise TypeError("Unsupported input_data type for detect_text")

    def _parse_ocr_results(self, result: Dict[str, Any]) -> Tuple[str, List[Tuple[List[Tuple[int, int]], str, float]]]:
        """Helper to parse OCR API results into expected format."""
        formatted_results: List[Tuple[List[Tuple[int, int]], str, float]] = []
        texts: List[str] = []
        for item in result.get("results", []):
            text = item.get("text", "")
            bbox = item.get("bbox", [])
            confidence = float(item.get("confidence", 0.0))

            # Normalize bbox coords to a list of (int, int) tuples
            if isinstance(bbox, list) and len(bbox) >= 4:
                clean_bbox: List[Tuple[int, int]] = []
                for pt in bbox:
                    try:
                        x = int(float(pt[0]))
                        y = int(float(pt[1]))
                        clean_bbox.append((x, y))
                    except (TypeError, ValueError, IndexError) as ex:
                        internal_logger.debug("Skipping malformed bbox point %r: %s", pt, ex)
                        # skip this point and continue parsing remaining points
                        continue

                if len(clean_bbox) >= 4:
                    formatted_results.append((clean_bbox, text, confidence))
                    texts.append(text)

        detected_text = " ".join(t for t in texts if t)
        return detected_text, formatted_results

    def find_element(self, input_data: Union[str, "np.ndarray"], text: str, index: Optional[int] = None) -> Optional[Tuple[bool, Tuple[int, int], Tuple[Tuple[int, int], Tuple[int, int]]]]:
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
        # Prepare image for annotation and decide what to pass to detect_text
        img = None
        detect_input = input_data
        # If input_data is a base64 string, decode to img for annotation
        if isinstance(input_data, str):
            try:
                img_data = base64.b64decode(input_data)
                nparr = np.frombuffer(img_data, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            except Exception as e:
                internal_logger.error(f"Failed to decode input image: {str(e)}")
                return None
        else:
            # assume numpy array
            img = input_data

        # Get all detected texts (detect_text returns (detected_text_str, results))
        detect_result = self.detect_text(detect_input)
        if not detect_result:
            return None
        _, detections = detect_result

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

        if result is not None and img is not None:
            _, center, (top_left, bottom_right) = result
            # Draw bounding box
            try:
                cv2.rectangle(img, top_left, bottom_right, (0, 255, 0), 2)  # pylint: disable=no-member
                # Draw center point
                cv2.circle(img, center, 5, (0, 0, 255), -1)  # pylint: disable=no-member
                # Save the annotated screenshot
                utils.save_screenshot(img, "detected_text")
            except Exception as ex:
                # Log drawing/saving errors for easier debugging but don't fail the flow
                try:
                    # cv2.error may exist if cv2 is available
                    _cv2_error = getattr(cv2, 'error', None)
                    if _cv2_error is not None and isinstance(ex, _cv2_error):
                        internal_logger.debug("OpenCV drawing failed: %s", ex)
                    else:
                        internal_logger.debug("Failed to save annotated screenshot: %s", ex)
                except Exception:
                    internal_logger.debug("Failed to save annotated screenshot: %s", ex)

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
