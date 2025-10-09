import numpy as np
import cv2
from typing import Optional
from optics_framework.common.models import TemplateData

def load_template(element: str, template_data: Optional[TemplateData] = None) -> np.ndarray:
    """
    Load a template image using dynamic template mapping.

    :param element: The name of the template image file.
    :type element: str
    :param template_data: TemplateData containing image mappings. Must be provided.
    :type template_data: Optional[TemplateData]

    :return: The template image as a NumPy array.
    :rtype: np.ndarray

    :raises ValueError: If the template is not found.
    """
    if template_data is None:
        raise ValueError("Template data is required. Pass it from the session.templates.")

    template_path = template_data.get_template_path(element)
    if not template_path:
        raise ValueError(f"Template '{element}' not found in template data")

    template = cv2.imread(template_path)
    if template is None:
        raise ValueError(f"Failed to load template from path: {template_path}")

    return template

def match_and_annotate(
    ocr_results,
    target_texts,
    found_status,
    frame: np.ndarray
) -> np.ndarray:
    """
    Check OCR results for matching text and annotate matched regions.

    :param ocr_results: OCR outputs (bbox, text, confidence).
    :param target_texts: List of expected strings.
    :param found_status: Mutable dict to track found targets.
    :param frame: Image to annotate in place.
    :return: Annotated image frame.
    :rtype: np.ndarray
    """
    annotated_frame = frame.copy()
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
                cv2.rectangle(annotated_frame, top_left, bottom_right, (0, 255, 0), 2)
                cv2.circle(annotated_frame, (center_x, center_y), 5, (0, 0, 255), -1)
    return annotated_frame
