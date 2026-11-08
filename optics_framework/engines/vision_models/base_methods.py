import base64
import numpy as np
import cv2
from typing import Optional
from optics_framework.common import utils
from optics_framework.common.error import OpticsError
from optics_framework.common.image_decode import decode_image, read_image
from optics_framework.common.logging_config import internal_logger
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

    :raises ValueError: If the template is not found or cannot be read.
    :raises OpticsError: E0304 when the template is too large or not a valid image.
    """
    if template_data is None:
        raise ValueError("Template data is required. Pass it from the session.templates.")

    template_path = template_data.get_template_path(element)
    if not template_path:
        raise ValueError(f"Template '{element}' not found in template data")

    try:
        return read_image(template_path)
    except OSError as e:
        raise ValueError(f"Failed to load template from path: {template_path}") from e


def _as_frame(image: np.ndarray | str | bytes) -> np.ndarray:
    if isinstance(image, np.ndarray):
        return image.copy()
    return decode_image(base64.b64decode(image) if isinstance(image, str) else bytes(image))


def save_match_annotation(
    image: np.ndarray | str | bytes, matches, name: str, output_dir: Optional[str]
) -> None:
    """Save ``image`` (array, base64 or encoded bytes) with each ``(center, (top_left, bottom_right))`` drawn.

    Does nothing, not even decode, without ``output_dir``; a failure is logged, never raised.
    """
    if not output_dir:
        return
    try:
        annotated = _as_frame(image)
        for center, (top_left, bottom_right) in matches:
            cv2.rectangle(annotated, top_left, bottom_right, (0, 255, 0), 2)
            cv2.circle(annotated, center, 5, (0, 0, 255), -1)
        utils.save_screenshot(annotated, name, output_dir=output_dir)
    except (OpticsError, ValueError, cv2.error) as e:
        internal_logger.debug("Annotated screenshot not saved: %s", e)


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
