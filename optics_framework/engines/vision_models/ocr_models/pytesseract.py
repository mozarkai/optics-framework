from optics_framework.common.text_interface import TextInterface
from optics_framework.common import utils
from optics_framework.common.logging_config import internal_logger
import pytesseract
import cv2


class PytesseractHelper(TextInterface):
    """
    Helper class for Optical Character Recognition (OCR) using EasyOCR.

    This class uses EasyOCR to detect text in images and optionally locate
    specific reference text.
    """

    def __init__(self, language: str = "en"):
        """
        Initializes the EasyOCR reader.

        :param language: Language code for OCR (default: "en").
        :type language: str

        :raises RuntimeError: If EasyOCR fails to initialize.
        """
        self.pytesseract_config = "--oem 3 --psm 6"
        # internal_logger.debug(f"Pytesseract initialized with config: {self.pytesseract_config}")


    def locate(self, frame, text, index=0):
        """
        Find the location of text within the input data.
        """
        result, coor, bbox = self.find_element(frame, text, index)
        if not result:
            internal_logger.exception(f"Text '{text}' not found in the frame.")
            raise Exception(f"Text '{text}' not found in the frame.")
        # annotate the frame
        annotated_frame = utils.annotate_element(frame, coor, bbox)
        utils.save_screenshot(annotated_frame, name='annotated_frame')
        return coor


    def find_element(self, frame, text, index=None):
        """
        Locate a specific occurrence of a text in the given frame using OCR and return the center coordinates.

        Parameters:
            - frame (np.array): Image data of the frame.
            - text (str): The text to locate in the frame.
            - index (int): The occurrence index (0-based) to select if multiple matches exist.

        Returns:
            - tuple: (x, y) coordinates of the center of the selected text in the frame or (None, None) if not found.
            - tuple: Bounding box coordinates of the detected text.
            - frame (np.array): The frame with the annotated text bounding box and center dot.
        """
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, ocr_results = self.detect_text_pytesseract(gray_frame)

        # internal_logger.debug("Text Detection Results:", ocr_results)

        matches = []
        for detected_text, (x, y, w, h), confidence in ocr_results:
            if detected_text.strip().lower() == text.lower():  # Case-insensitive match
                center_x = x + w // 2
                center_y = y + h // 2
                bbox = ((x, y), (x + w, y + h))  # Top-left and bottom-right
                matches.append(((center_x, center_y), bbox))

        if not matches:
            return False, (None, None), None

        if index is not None:
            if 0 <= index < len(matches):
                selected_center, selected_bbox = matches[index]
            else:
                return False, (None, None), None
        else:
            selected_center, selected_bbox = matches[0]

        # Save the annotated frame
        utils.save_screenshot(frame, name='annotated_frame')

        return True, selected_center, selected_bbox



    def detect_text_pytesseract(self, image):
        _, binary_image = cv2.threshold(image, 150, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        data = pytesseract.image_to_data(binary_image, config=self.pytesseract_config, output_type=pytesseract.Output.DICT)
        detected_texts = []
        for i in range(len(data["text"])):
            text = data["text"][i].strip()
            if text:  # Filter out empty results
                x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
                conf = data["conf"][i]  # Get confidence score
                detected_texts.append((text, (x, y, w, h), conf))
        return binary_image, detected_texts

    def element_exist(self, input_data, reference_data):
        # dummy implementation
        return super().element_exist(input_data, reference_data)
