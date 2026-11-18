from typing import List, Tuple, Optional
import easyocr
import cv2
from optics_framework.common.text_interface import TextInterface
from optics_framework.common.logging_config import internal_logger


class EasyOCRHelper(TextInterface):
    """
    Helper class for Optical Character Recognition (OCR) using EasyOCR.

    This class uses EasyOCR to detect text in images and optionally locate
    specific reference text.
    """

    def __init__(self, config=None):
        """
        Initializes the EasyOCR reader.

        :param config: Configuration dict containing language and execution_output_path.
        :type config: dict

        :raises RuntimeError: If EasyOCR fails to initialize.
        """
        # Extract parameters from config or use defaults
        language = config.get("language", "en") if config else "en"

        try:
            self.reader = easyocr.Reader([language])
            # internal_logger.debug(f"EasyOCR initialized with language: {language}")
        except Exception as e:
            internal_logger.error(f"Failed to initialize EasyOCR: {e}")
            raise RuntimeError("EasyOCR initialization failed.") from e

    def detect_text(self, input_data) -> Optional[Tuple[str, List[Tuple[List[List[int]], str, float]]]]:
        """
        Detects text in the given image using EasyOCR.

        :param input_data: Image data (numpy array).
        :return: List of tuples (bounding box, text, confidence) or None.
        """
        gray_image = cv2.cvtColor(input_data, cv2.COLOR_BGR2GRAY)
        raw_results = self.reader.readtext(gray_image)
        # Ensure results are List[Tuple[List[List[int]], str, float]]
        results: List[Tuple[List[List[int]], str, float]] = []
        for item in raw_results:
            if (
                isinstance(item, tuple)
                and len(item) == 3
                and isinstance(item[0], list)
                and isinstance(item[1], str)
                and isinstance(item[2], float)
            ):
                results.append((item[0], item[1], item[2]))
        detected_text = ' '.join(result[1] for result in results)
        internal_logger.debug(f"Detected texts using easyocr: {detected_text}")
        return detected_text, results
