from optics_framework.common.text_interface import TextInterface
from optics_framework.common.logging_config import internal_logger
import pytesseract
import cv2


class PytesseractHelper(TextInterface):
    """
    Helper class for Optical Character Recognition (OCR) using EasyOCR.

    This class uses EasyOCR to detect text in images and optionally locate
    specific reference text.
    """

    def __init__(self, config=None):
        """
        Initializes the Pytesseract OCR reader.

        :param config: Configuration dict containing language and execution_output_path.
        :type config: dict

        :raises RuntimeError: If Pytesseract fails to initialize.
        """
        self.pytesseract_config = "--oem 3 --psm 6"
        # internal_logger.debug(f"Pytesseract initialized with config: {self.pytesseract_config}")

    def detect_text(self, image):
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        _, binary_image = cv2.threshold(image, 150, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        data = pytesseract.image_to_data(binary_image, config=self.pytesseract_config, output_type=pytesseract.Output.DICT)
        detected_texts = []
        for i in range(len(data["text"])):
            text = data["text"][i].strip()
            if text:
                x = data["left"][i]
                y = data["top"][i]
                w = data["width"][i]
                h = data["height"][i]
                conf = float(data["conf"][i]) if data["conf"][i] != '-1' else 0.0

                # Build 4-point bounding box
                top_left = (x, y)
                top_right = (x + w, y)
                bottom_right = (x + w, y + h)
                bottom_left = (x, y + h)
                bbox = [top_left, top_right, bottom_right, bottom_left]

                detected_texts.append((bbox, text, conf))

        internal_logger.debug(f"Pytesseract detected texts: {detected_texts}")
        return binary_image, detected_texts
