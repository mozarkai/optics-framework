from typing import Dict, Any, Optional, List, Tuple
import requests
import json
import cv2
import numpy as np
import base64
from optics_framework.common.text_interface import TextInterface
from optics_framework.common.config_handler import Config
from optics_framework.common.logging_config import internal_logger


class RemoteOCR(TextInterface):
    DEPENDENCY_TYPE = "text_detection"
    NAME = "remote_ocr"

    def __init__(self, config: Config | Dict[str, Any]):
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

    def detect_text(self, input_data: str | np.ndarray) -> Optional[Tuple[str, List[Tuple[List[Tuple[int, int]], str, float]]]]:
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

    def _encode_image(self, input_data: str | np.ndarray) -> str:
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
