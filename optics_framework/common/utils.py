from datetime import datetime
import hashlib
from fuzzywuzzy import fuzz
import re
import os
import ast
import cv2
import json
import base64
import numpy as np
from enum import Enum
from datetime import timezone, timedelta
from skimage.metrics import structural_similarity as ssim
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.config_handler import ConfigHandler


class SpecialKey(Enum):
    ENTER = 'enter'
    TAB = 'tab'
    BACKSPACE = 'backspace'
    SPACE = 'space'
    ESCAPE = 'escape'

def determine_element_type(element):
    # Check if the input is an Image path
    if element.split(".")[-1] in ["jpg", "jpeg", "png", "bmp"]:
        return "Image"
    # Check if the input is an XPath
    if element.startswith("/") or element.startswith("//") or element.startswith("("):
        return "XPath"
    # Check if it looks like an ID (heuristic: no slashes, no dots, usually alphanumeric/underscores)
    if element.lower().startswith("id:"):
        return "ID"
    # Default case: consider the input as Text
    return "Text"

def get_timestamp():
    try:
        current_utc_time = datetime.now(timezone.utc)
        desired_timezone = timezone(timedelta(hours=5, minutes=30))
        current_time_in_desired_timezone = current_utc_time.astimezone(desired_timezone)
        formatted_time = current_time_in_desired_timezone.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
        return formatted_time[:-2] + ":" + formatted_time[-2:]
    except Exception as e:
        internal_logger.error('Unable to get current time', exc_info=e)
        return None

def encode_numpy_to_base64(image: np.ndarray) -> str:
    """
    Encodes a NumPy image (OpenCV format) to a base64 string.

    :param image: The input image as a NumPy array (BGR format).
    :return: Base64 encoded string.
    """
    if image is None or not isinstance(image, np.ndarray):
        raise ValueError("Input image must be a valid NumPy array")

    if image.size == 0 or image.shape[0] == 0 or image.shape[1] == 0:
        raise ValueError("Input image is empty or has invalid dimensions")

    _, buffer = cv2.imencode('.png', image)
    encoded_string = base64.b64encode(buffer).decode('utf-8')
    return encoded_string

def compute_hash(xml_string):
    """Computes the SHA-256 hash of the XML string."""
    return hashlib.sha256(xml_string.encode('utf-8')).hexdigest()

def detect_change(frame1, frame2, threshold=0.95):
    """
    Returns True if the 2 frames have differences above threshold.
    Can be used to detect screen transitions.
    """
    if frame1 is None or frame2 is None:
        return True
    gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)
    score, _ = ssim(gray1, gray2, full=True)
    return score < threshold

def compare_text(given_text, target_text):
    """
    Compare two text values using exact, partial, and fuzzy matching.
    Returns True if the texts match closely enough, otherwise False.
    """
    # Normalize both texts (case insensitive, strip whitespace)
    given_text = given_text.strip().lower()
    target_text = target_text.strip().lower()

    # Check if either of the strings is empty and return False if so
    if not given_text or not target_text:
        internal_logger.debug(f"One or both texts are empty: given_text='{given_text}', target_text='{target_text}'")
        return False

    # 1. Exact Match (return immediately)
    if given_text == target_text:
        internal_logger.debug(f"Exact match found: '{given_text}' == '{target_text}'")
        internal_logger.debug(f'Exact match found for text: {given_text}')
        return True

    # 2. Partial Match (substring, return immediately)
    if target_text in given_text:
        internal_logger.debug(f"Partial match found: '{target_text}' in '{given_text}'")
        internal_logger.debug(f'Partial match found for text: {target_text}')
        return True

    # 3. Fuzzy Match (only if exact and partial checks fail)
    fuzzy_match_score = fuzz.ratio(given_text, target_text)
    internal_logger.debug(f"Fuzzy match score for '{given_text}' and '{target_text}': {fuzzy_match_score}")
    if fuzzy_match_score >= 80:  # Threshold for "close enough"
        internal_logger.debug(f"Fuzzy match found: score {fuzzy_match_score}")
        internal_logger.debug(f"Fuzzy match found for text: {given_text}, matched text '{target_text}' with fuzzy score {fuzzy_match_score} ")
        return True

    # If no matches found, return False
    internal_logger.debug(f"No match found for '{given_text}' and '{target_text}' using all matching algorithms.")
    return False

def get_execution_output_dir():
    config_handler = ConfigHandler.get_instance()
    config = config_handler.load()
    output_dir = config.execution_output_path
    if output_dir is None:
        internal_logger.error("Execution output path is not set in the configuration.")
        return None
    os.makedirs(output_dir, exist_ok=True)
    return output_dir

def save_screenshot(img, name, time_stamp = None):
    """
    Save the screenshot with a timestamp and keyword in the filename.
    """
    if img is None:
        internal_logger.error("Image is empty. Cannot save screenshot.")
        return
    name = re.sub(r'[^a-zA-Z0-9\s_]', '', name)
    if time_stamp is None:
        time_stamp = str(datetime.now().astimezone().strftime('%Y-%m-%dT%H-%M-%S-%f'))
    output_dir = get_execution_output_dir()
    if output_dir is None:
        internal_logger.error("Failed to get execution output directory. Cannot save screenshot.")
        return
    screenshot_file_path = os.path.join(output_dir, f"{time_stamp}-{name}.jpg")
    try:
        cv2.imwrite(screenshot_file_path, img)
        internal_logger.debug(f'Screenshot saved as : {time_stamp}-{name}.jpg')
        internal_logger.debug(f"Screenshot saved to :{screenshot_file_path}")

    except Exception as e:
        internal_logger.debug(f"Error writing screenshot to file : {e}")


def annotate(annotation_detail):
    screenshot,bboxes,screenshot_name = annotation_detail
    # Iterate over each bounding box and annotate it on the image
    for bbox in bboxes:
        if bbox is None or len(bbox) != 2:
            internal_logger.debug(f"Invalid bounding box: {bbox}")
            continue

        top_left, bottom_right = bbox
        if top_left is None or bottom_right is None:
            internal_logger.debug(f"Invalid coordinates in bounding box: {bbox}")
            continue

        # Draw a rectangle around the bounding box
        cv2.rectangle(screenshot, tuple(top_left), tuple(
            bottom_right), color=(0, 255, 0), thickness=3)
        internal_logger.debug(f"Bounding box {top_left} to {bottom_right} annotated.")
    # Save the annotated screenshot
    internal_logger.debug(f'annnotated image: {len(screenshot)}')
    save_screenshot(screenshot,screenshot_name)


def is_black_screen(image):
    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    average_colour = np.mean(gray_image)
    black_threshold = 10
    return average_colour < black_threshold



def annotate_element(frame, centre_coor, bbox):
    # Annotation: Draw the bounding box around the text
    cv2.rectangle(frame, bbox[0], bbox[1], (0, 255, 0), 2)

    # Draw a small circle at the center of the bounding box (optional)
    cv2.circle(frame, centre_coor, 5, (0, 0, 255), -1)
    return frame

def annotate_and_save(frame, element_status):
    """
    Draw bounding boxes on the frame for found elements and save the annotated image.

    Args:
        frame (numpy.ndarray): Image to annotate.
        element_status (dict): Dictionary containing found elements and their bounding boxes.
    """
    if frame is None:
        return

    # Annotate detected texts and images with GREEN color (no labels)
    for category, items in element_status.items():
        for item_name, status in items.items():
            if status["found"] and status["bbox"]:
                (x1, y1), (x2, y2) = status["bbox"]
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)  # Green for both text and images

    # Save annotated frame
    save_screenshot(frame, name="annotated_frame")




def save_page_source(tree, time_stamp):
    output_dir = get_execution_output_dir()
    if output_dir is None:
        internal_logger.error("Failed to get execution output directory. Cannot save page source.")
        return
    page_source_file_path = os.path.join(output_dir, "page_sources_log.xml")

    # Remove any XML declaration
    cleaned_tree = re.sub(r'<\?xml[^>]+\?>', '', tree, flags=re.IGNORECASE).strip()

    # Wrap in <entry> tag
    entry_block = f'\n  <entry timestamp="{time_stamp}">\n{cleaned_tree}\n  </entry>\n'

    if not os.path.exists(page_source_file_path):
        with open(page_source_file_path, 'w', encoding='utf-8') as f:
            f.write(f"<logs>\n{entry_block}</logs>\n")
        internal_logger.debug(f"Created new page source log file with first entry at: {time_stamp}")
    else:
        with open(page_source_file_path, 'r+', encoding='utf-8') as f:
            content = f.read()
            if not content.strip().endswith("</logs>"):
                internal_logger.error("Invalid log file: missing closing </logs> tag.")
                return

            f.seek(0)
            updated_content = content.strip()[:-7] + entry_block + "</logs>\n"
            f.write(updated_content)
        internal_logger.debug(f"Page source appended at: {time_stamp}")

    internal_logger.debug(f"Page source saved to: {page_source_file_path}")


def save_page_source_html(html: str, time_stamp):
    output_dir = get_execution_output_dir()
    if output_dir is None:
        internal_logger.error("Failed to get execution output directory. Cannot save HTML page source.")
        return
    page_source_file_path = os.path.join(output_dir, "page_sources_log.html")
    # Prepare entry block with timestamp comment
    entry_block = f'\n<!-- timestamp: {time_stamp} -->\n{html}\n'

    if not os.path.exists(page_source_file_path):
        with open(page_source_file_path, 'w', encoding='utf-8') as f:
            f.write(f"<!-- HTML Page Source Logs -->\n{entry_block}")
        internal_logger.debug(f"Created new HTML page source log file at: {time_stamp}")
    else:
        with open(page_source_file_path, 'a', encoding='utf-8') as f:
            f.write(entry_block)
        internal_logger.debug(f"Appended new page source entry at: {time_stamp}")

    internal_logger.debug(f"HTML page source saved to: {page_source_file_path}")

def strip_sensitive_prefix(value: str) -> str:
    if isinstance(value, str) and value.startswith("@:"):
        return value[len("@:"):]
    return value


def save_interactable_elements(elements):
    output_dir = get_execution_output_dir()
    if output_dir is None:
        internal_logger.error("Failed to get execution output directory. Cannot save interactable elements.")
        return
    output_path = os.path.join(output_dir, "interactable_elements.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(elements, f, indent=2, ensure_ascii=False)

def load_config(default_config: dict) -> dict:
    """Load config from environment variable and override the default config."""
    env_config = os.environ.get("TEST_SESSION_ENV_VARIABLES")

    if not env_config:
        return default_config  # No override


    try:
        config = json.loads(env_config)
        for key, value in config.items():
            if isinstance(value, str):
                try:
                    value = re.sub(r"'(\w+?)'\s*:", r'"\1":', value)
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = ast.literal_eval(
                        value.replace("true", "True")
                             .replace("false", "False")
                             .replace("null", "None")
                    )
            default_config[key] = value  # Overwrite
        return default_config
    except Exception as e:
        print(f"Failed to load config from env: {e}")
        return default_config
