from optics_framework.common.text_interface import TextInterface
from optics_framework.common import utils
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
        _, ocr_results = self.detect_text(frame)

        # internal_logger.debug("Text Detection Results:", ocr_results)

        matches = []
        for bbox, detected_text, _ in ocr_results:
            if detected_text.strip().lower() == text.lower():
                top_left = tuple(map(int, bbox[0]))
                bottom_right = tuple(map(int, bbox[2]))
                center_x = (top_left[0] + bottom_right[0]) // 2
                center_y = (top_left[1] + bottom_right[1]) // 2
                matches.append(((center_x, center_y), (top_left, bottom_right)))

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
        cv2.rectangle(frame, selected_bbox[0], selected_bbox[1], (0, 255, 0), 2)
        cv2.circle(frame, selected_center, 5, (0, 0, 255), -1)
        utils.save_screenshot(frame, name='annotated_frame')

        return True, selected_center, selected_bbox

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

        return binary_image, detected_texts

    def element_exist(self, input_data, reference_data):
        # dummy implementation
        return super().element_exist(input_data, reference_data)
