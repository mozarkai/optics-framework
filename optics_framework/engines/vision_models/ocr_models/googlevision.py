from optics_framework.common.text_interface import TextInterface
import cv2
import numpy as np
from google.cloud import vision
from google.cloud.vision_v1 import ImageAnnotatorClient


class GoogleVisionHelper(TextInterface):
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
        pass

    def find_element(self, frame, text, index=None):
        """
        Locate a specific text in the given frame using OCR and return the center coordinates with an optional offset.

        Parameters:
        - frame (np.array): Image data of the frame.
        - text (str): The text to locate in the frame.

        Returns:
        - bool: True if the text is found in the frame, False otherwise.
        - tuple: (x, y) coordinates of the center of the text in the frame or (None, None) if no match is found.
        - tuple: Bounding box coordinates of the detected text.
        - frame (np.array): The frame with the text bounding box and adjusted center dot annotated.
        """
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, ocr_results = self.detect_text(gray_frame)

        matches = []
        # Iterate over each detected text
        for (bbox, detected_text, _) in ocr_results:
            detected_text = detected_text.strip()
            if text in detected_text:

                # Get the bounding box coordinates of the detected text
                (top_left, top_right, bottom_right, bottom_left) = bbox
                x, y = int(top_left[0]), int(top_left[1])
                w = int(bottom_right[0] - top_left[0])
                h = int(bottom_right[1] - top_left[1])

                # Calculate the center coordinates
                center_x = x + w // 2
                center_y = y + h // 2

                matches.append(((center_x, center_y), (top_left, bottom_right)))

        if not matches:
            return False, (None, None), None

        if index is not None:
            if 0 <= index < len(matches):
                selected_centre, selected_bbox = matches[index]
            else:
                return False, (None, None), None
        else:
            selected_centre, selected_bbox = matches[0]
        return True, selected_centre, selected_bbox


    def element_exist(self, input_data, reference_data):
        return super().element_exist(input_data, reference_data)


    def detect_text(self,frame: np.ndarray):
        """
        Detects text in a given NumPy array using Google Vision API and returns standardized OCR format.

        Returns:
            Tuple[None, List[Tuple[bbox, text, confidence]]]
            bbox = List[Tuple[int, int]] with 4 points
        """
        if frame is None or not isinstance(frame, np.ndarray):
            raise ValueError("Invalid frame provided. Ensure it's a valid NumPy array.")

        _, encoded_image = cv2.imencode('.jpg', frame)
        image_bytes = encoded_image.tobytes()

        client = ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)

        response = client.text_detection(image=image)
        texts = response.text_annotations

        results = []
        for text in texts[1:]:  # Skip full block (index 0)
            text_str = text.description
            vertices = text.bounding_poly.vertices
            if len(vertices) >= 4:
                bbox = [(v.x, v.y) for v in vertices]
                results.append((bbox, text_str, None)) # None for confidence placeholder as it's not provided by Google Vision API
            else:
                continue

        return None, results
