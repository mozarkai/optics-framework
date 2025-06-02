from optics_framework.common.text_interface import TextInterface
from optics_framework.common import utils
from optics_framework.common.logging_config import internal_logger
import easyocr
import cv2



class EasyOCRHelper(TextInterface):
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
        try:
            self.reader = easyocr.Reader([language])
            # internal_logger.debug(f"EasyOCR initialized with language: {language}")
        except Exception as e:
            internal_logger.error(f"Failed to initialize EasyOCR: {e}")
            raise RuntimeError("EasyOCR initialization failed.") from e


    def find_element(self, frame, text, index=None):
        """
        Locate multiple instances of a specific text in the given frame using OCR and return the center coordinates
        of the text at the given index with bounding box coordinates.

        Parameters:
        - frame (np.array): Image data of the frame.
        - text (str): The text to locate in the frame.
        - index (int): The index of the match to retrieve.

        Returns:
        - bool: True if the text is found, False otherwise.
        - tuple: (x, y) coordinates of the center of the indexed text in the frame or (None, None) if out of bounds.
        - tuple: Bounding box coordinates of the detected text.
        """
        _, ocr_results = self.detect_text(frame)

        detected_texts = []

        # Iterate over each detected text
        for (bbox, detected_text, confidence) in ocr_results:
            detected_text = detected_text.strip()
            if text in detected_text:
                (top_left, top_right, bottom_right, bottom_left) = bbox
                x, y = int(top_left[0]), int(top_left[1])
                w = int(bottom_right[0] - top_left[0])
                h = int(bottom_right[1] - top_left[1])

                # Calculate the center coordinates
                center_x = x + w // 2
                center_y = y + h // 2

                detected_texts.append((True, (center_x, center_y), (top_left, bottom_right)))

                # Draw bounding box around the detected text
                cv2.rectangle(frame, top_left, bottom_right, (0, 255, 0), 2)
                cv2.circle(frame, (center_x, center_y), 5, (0, 0, 255), -1)

        if not detected_texts:
            return False, (None, None), None
        if index is not None:
            # Return the requested index
            if 0 <= index < len(detected_texts):
                return detected_texts[index]
            return False, (None, None), None

        utils.save_screenshot(frame, "annotated_frame")
        return detected_texts[0]


    def detect_text(self, image):
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        reader = easyocr.Reader(['en'])
        results = reader.readtext(image)
        detected_text = ' '.join(result[1] for result in results)
        return detected_text, results


    def element_exist(self, input_data, reference_data):
        return super().element_exist(input_data, reference_data)
