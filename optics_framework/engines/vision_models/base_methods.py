import numpy as np
import cv2
import os

def load_template(project_path: str, element: str) -> np.ndarray:
    """
    Load a template image from the input_templates folder.

    :param project_path: The path to the project directory.
    :type project_path: str
    :param element: The name of the template image file.
    :type element: str

    :return: The template image as a NumPy array.
    :rtype: np.ndarray

    :raises ValueError: If the project path is not set.
    """

    templates_folder = os.path.join(project_path, "input_templates")
    template_path = os.path.join(templates_folder, element)
    template = cv2.imread(template_path)

    return template

def match_and_annotate(
    ocr_results,
    target_texts,
    found_status,
    frame: np.ndarray
) -> None:
    """
    Check OCR results for matching text and annotate matched regions.

    :param ocr_results: OCR outputs (bbox, text, confidence).
    :param target_texts: List of expected strings.
    :param found_status: Mutable dict to track found targets.
    :param frame: Image to annotate in place.
    """
    for (bbox, detected_text, _) in ocr_results:
        clean_text = detected_text.strip().lower()
        for target in target_texts:
            if found_status[target]:
                continue

            if target.lower() in clean_text:
                top_left = tuple(map(int, bbox[0]))
                bottom_right = tuple(map(int, bbox[2]))
                center_x = (top_left[0] + bottom_right[0]) // 2
                center_y = (top_left[1] + bottom_right[1]) // 2

                found_status[target] = True
                cv2.rectangle(frame, top_left, bottom_right, (0, 255, 0), 2)
                cv2.circle(frame, (center_x, center_y), 5, (0, 0, 255), -1)
