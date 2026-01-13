from datetime import datetime
import hashlib
from fuzzywuzzy import fuzz
import re
import os
import cv2
import json
import base64
import numpy as np
from enum import Enum
from datetime import timezone, timedelta
from typing import Optional, Any, Union, get_origin, get_args
import inspect
from skimage.metrics import structural_similarity as ssim
from optics_framework.common.logging_config import internal_logger

OUTPUT_PATH_NOT_SET_MSG = "output_dir is required. Pass it from the session's execution_output_path."

class SpecialKey(Enum):
    # Basic keys (supported by both BLE and Appium)
    ENTER = 'enter'
    TAB = 'tab'
    BACKSPACE = 'backspace'
    SPACE = 'space'
    ESCAPE = 'escape'

    # Arrow keys (BLE only)
    LEFT = 'left'
    RIGHT = 'right'
    UP = 'up'
    DOWN = 'down'

    # Navigation keys (BLE only)
    INSERT = 'insert'
    DELETE = 'delete'
    HOME = 'home'  # Supported by both BLE and Appium
    END = 'end'
    PAGE_UP = 'pageup'
    PAGE_DOWN = 'pagedown'

    # Function keys (BLE only)
    F1 = 'f1'
    F2 = 'f2'
    F3 = 'f3'
    F4 = 'f4'
    F5 = 'f5'
    F6 = 'f6'
    F7 = 'f7'
    F8 = 'f8'
    F9 = 'f9'
    F10 = 'f10'
    F11 = 'f11'
    F12 = 'f12'

    # System keys (BLE only)
    PRINT_SCREEN = 'printscreen'
    SCROLL_LOCK = 'scrolllock'
    PAUSE = 'pause'

    # Numpad keys (BLE only)
    NUM_LOCK = 'numlock'
    NUM_PAD_0 = 'numpad0'
    NUM_PAD_1 = 'numpad1'
    NUM_PAD_2 = 'numpad2'
    NUM_PAD_3 = 'numpad3'
    NUM_PAD_4 = 'numpad4'
    NUM_PAD_5 = 'numpad5'
    NUM_PAD_6 = 'numpad6'
    NUM_PAD_7 = 'numpad7'
    NUM_PAD_8 = 'numpad8'
    NUM_PAD_9 = 'numpad9'
    NUM_PAD_PLUS = 'numpadplus'
    NUM_PAD_MINUS = 'numpadminus'
    NUM_PAD_MULTIPLY = 'numpadmultiply'
    NUM_PAD_DIVIDE = 'numpaddivide'
    NUM_PAD_ENTER = 'numpadenter'
    NUM_PAD_DECIMAL = 'numpaddecimal'
    NUM_PAD_COMMA = 'numpadcomma'
    NUM_PAD_PERIOD = 'numpadperiod'
    NUM_PAD_EQUAL = 'numpadequal'

    # Mobile/System specific keys (Appium only)
    BACK = 'back'
    MENU = 'menu'
    VOLUME_UP = 'volumeup'
    VOLUME_DOWN = 'volumedown'
    POWER = 'power'
    CAMERA = 'camera'
    SEARCH = 'search'

def determine_element_type(element):
    # Check if the input is an Image path
    if element.split(".")[-1] in ["jpg", "jpeg", "png", "bmp"]:
        return "Image"
    # Check for Playwright-specific prefixes first
    if element.lower().startswith("text="):
        return "Text"
    if element.lower().startswith("css="):
        return "CSS"
    if element.lower().startswith("xpath="):
        return "XPath"
    # Check if the input is an XPath
    if element.startswith("/") or element.startswith("//") or element.startswith("("):
        return "XPath"
    # Check if it looks like an ID (heuristic: no slashes, no dots, usually alphanumeric/underscores)
    if element.lower().startswith("id:"):
        return "ID"
    # Check if it's a CSS selector (has brackets, starts with # or ., or contains CSS selector patterns)
    # CSS selectors can have: tag[attribute="value"], #id, .class, tag.class, etc.
    if ("[" in element and "]" in element) or element.startswith("#") or element.startswith("."):
        return "CSS"
    # Check if it starts with a tag name followed by CSS selector characters
    # Common HTML tags that might be CSS selectors
    common_tags = ["input", "button", "div", "span", "a", "img", "select", "textarea", "form", "label", "p", "h1", "h2", "h3", "h4", "h5", "h6"]
    if any(element.startswith(tag + "[") or element.startswith(tag + "#") or element.startswith(tag + ".") for tag in common_tags):
        return "CSS"
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

def save_screenshot(img, name, output_dir, time_stamp=None):
    """
    Save the screenshot with a timestamp and keyword in the filename.

    Args:
        img: The image to save
        name: Name for the screenshot file
        output_dir: Directory where to save the screenshot (required)
        time_stamp: Optional timestamp, will be generated if not provided
    """
    if img is None:
        internal_logger.error("Image is empty. Cannot save screenshot.")
        raise ValueError("Image is empty. Cannot save screenshot.")
    if output_dir is None:
        internal_logger.info(OUTPUT_PATH_NOT_SET_MSG)
        return
    name = re.sub(r'[^a-zA-Z0-9\s_]', '', name)
    if time_stamp is None:
        time_stamp = str(datetime.now().astimezone().strftime('%Y-%m-%dT%H-%M-%S-%f'))
    screenshot_file_path = os.path.join(output_dir, f"{time_stamp}-{name}.jpg")
    try:
        cv2.imwrite(screenshot_file_path, img)
        internal_logger.debug(f'Screenshot saved as : {time_stamp}-{name}.jpg')
        internal_logger.debug(f"Screenshot saved to :{screenshot_file_path}")

    except Exception as e:
        internal_logger.debug(f"Error writing screenshot to file : {e}")


def annotate(screenshot, bboxes):
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
    return screenshot

def is_black_screen(image):
    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    average_colour = np.mean(gray_image)
    black_threshold = 1
    return average_colour < black_threshold

def annotate_element(frame, centre_coor, bbox):
    # Annotation: Draw the bounding box around the text
    cv2.rectangle(frame, bbox[0], bbox[1], (0, 255, 0), 2)

    # Draw a small circle at the center of the bounding box (optional)
    cv2.circle(frame, centre_coor, 5, (0, 0, 255), -1)
    return frame

def save_page_source(tree, time_stamp, output_dir):
    """
    Save page source to XML log file.

    Args:
        tree: The page source tree/content
        time_stamp: Timestamp for the entry
        output_dir: Directory where to save the page source (required)
    """
    if output_dir is None:
        internal_logger.info(OUTPUT_PATH_NOT_SET_MSG)
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


def save_page_source_html(html: str, time_stamp, output_dir):
    """
    Save HTML page source to log file.

    Args:
        html: The HTML content to save
        time_stamp: Timestamp for the entry
        output_dir: Directory where to save the HTML (required)
    """
    if output_dir is None:
        internal_logger.info(OUTPUT_PATH_NOT_SET_MSG)
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


def save_interactable_elements(elements, output_dir):
    """
    Save interactable elements to JSON file.

    Args:
        elements: The elements data to save
        output_dir: Directory where to save the elements (required)
    """
    if output_dir is None:
        internal_logger.info(OUTPUT_PATH_NOT_SET_MSG)
        return
    output_path = os.path.join(output_dir, "interactable_elements.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(elements, f, indent=2, ensure_ascii=False)

def load_config(default_config: dict) -> dict:
    """Load config from environment variable and override the default config."""
    env_config = os.environ.get("TEST_SESSION_ENV_VARIABLES")
    if not env_config:
        return default_config

    try:
        # Expect proper JSON in the env var
        incoming = json.loads(env_config)
        if not isinstance(incoming, dict):
            internal_logger.info("Failed to load config from env: top-level JSON must be an object")
            return default_config
        merged = {**default_config, **incoming}
        return merged
    except Exception as e:
        internal_logger.info(f"Failed to load config from env: {e}")
        return default_config

def parse_special_key(text: str) -> Optional[SpecialKey]:
    """
    Check if the input text represents a single special key in <key> format.

    :param text: Input text to check
    :return: SpecialKey instance if text is a single special key, None otherwise
    """
    if isinstance(text, str) and "<" in text and ">" in text:
        # Check if the entire text is just a single special key
        text = text.strip()
        if text.startswith("<") and text.endswith(">") and text.count("<") == 1 and text.count(">") == 1:
            key_input = text[1:-1].lower()  # Extract content between < and >
            try:
                return SpecialKey(key_input)
            except ValueError:
                # If the key is not recognized, return None
                return None
    return None


def calculate_aoi_bounds(screenshot_shape, aoi_x, aoi_y, aoi_width, aoi_height):
    """
    Calculate pixel bounds for Area of Interest (AOI) from percentage coordinates.

    :param screenshot_shape: Shape of the screenshot (height, width, channels)
    :param aoi_x: X percentage of AOI top-left corner (0-100)
    :param aoi_y: Y percentage of AOI top-left corner (0-100)
    :param aoi_width: Width percentage of AOI (0-100)
    :param aoi_height: Height percentage of AOI (0-100)
    :return: Tuple of (x1, y1, x2, y2) pixel coordinates
    :raises ValueError: If AOI parameters are invalid or exceed bounds
    """
    if not all(isinstance(param, (int, float)) for param in [aoi_x, aoi_y, aoi_width, aoi_height]):
        raise ValueError("All AOI parameters must be numeric")

    if not all(0 <= param <= 100 for param in [aoi_x, aoi_y, aoi_width, aoi_height]):
        raise ValueError("All AOI parameters must be between 0 and 100")

    if aoi_x + aoi_width > 100:
        raise ValueError(f"AOI exceeds screen width: {aoi_x}% + {aoi_width}% = {aoi_x + aoi_width}% > 100%")

    if aoi_y + aoi_height > 100:
        raise ValueError(f"AOI exceeds screen height: {aoi_y}% + {aoi_height}% = {aoi_y + aoi_height}% > 100%")

    if aoi_width <= 0 or aoi_height <= 0:
        raise ValueError("AOI width and height must be greater than 0")

    height, width = screenshot_shape[:2]

    x1 = int(width * (aoi_x / 100))
    y1 = int(height * (aoi_y / 100))
    x2 = int(width * ((aoi_x + aoi_width) / 100))
    y2 = int(height * ((aoi_y + aoi_height) / 100))

    # Ensure bounds are within image dimensions
    x1 = max(0, min(x1, width - 1))
    y1 = max(0, min(y1, height - 1))
    x2 = max(x1 + 1, min(x2, width))
    y2 = max(y1 + 1, min(y2, height))

    internal_logger.debug(f"AOI bounds calculated: ({x1}, {y1}, {x2}, {y2}) for {aoi_x}%,{aoi_y}% + {aoi_width}%x{aoi_height}%")
    return x1, y1, x2, y2


def crop_screenshot_to_aoi(screenshot, aoi_x, aoi_y, aoi_width, aoi_height):
    """
    Crop screenshot to the specified Area of Interest (AOI).

    :param screenshot: NumPy array of the screenshot
    :param aoi_x: X percentage of AOI top-left corner (0-100)
    :param aoi_y: Y percentage of AOI top-left corner (0-100)
    :param aoi_width: Width percentage of AOI (0-100)
    :param aoi_height: Height percentage of AOI (0-100)
    :return: Tuple of (cropped_screenshot, (x1, y1, x2, y2)) where bounds are pixel coordinates
    :raises ValueError: If screenshot is invalid or AOI parameters are invalid
    """
    if screenshot is None or not isinstance(screenshot, np.ndarray):
        raise ValueError("Screenshot must be a valid NumPy array")

    if screenshot.size == 0 or len(screenshot.shape) < 2:
        raise ValueError("Screenshot must be a 2D or 3D array with valid dimensions")

    x1, y1, x2, y2 = calculate_aoi_bounds(screenshot.shape, aoi_x, aoi_y, aoi_width, aoi_height)

    cropped = screenshot[y1:y2, x1:x2]

    if cropped.size == 0:
        raise ValueError(f"Cropped AOI is empty - bounds ({x1}, {y1}, {x2}, {y2}) invalid for image shape {screenshot.shape}")

    internal_logger.debug(f"Screenshot cropped from {screenshot.shape} to {cropped.shape} using AOI bounds ({x1}, {y1}, {x2}, {y2})")
    return cropped, (x1, y1, x2, y2)


def adjust_coordinates_for_aoi(coordinates, aoi_bounds):
    """
    Adjust coordinates found in cropped AOI back to full screenshot coordinates.

    :param coordinates: Coordinates found in the cropped region (x, y) tuple
    :param aoi_bounds: AOI pixel bounds (x1, y1, x2, y2) returned from crop_screenshot_to_aoi
    :return: Adjusted coordinates (x, y) relative to full screenshot
    :raises ValueError: If coordinates or bounds are invalid
    """
    if coordinates is None or not isinstance(coordinates, (tuple, list)) or len(coordinates) != 2:
        raise ValueError("Coordinates must be a tuple or list of (x, y)")

    if aoi_bounds is None or not isinstance(aoi_bounds, (tuple, list)) or len(aoi_bounds) != 4:
        raise ValueError("AOI bounds must be a tuple or list of (x1, y1, x2, y2)")

    x, y = coordinates
    x1, y1, x2, y2 = aoi_bounds

    if not all(isinstance(coord, (int, float)) for coord in [x, y, x1, y1, x2, y2]):
        raise ValueError("All coordinates must be numeric")

    adjusted_x = x + x1
    adjusted_y = y + y1

    internal_logger.debug(f"Coordinates adjusted from AOI ({x}, {y}) to full screenshot ({adjusted_x}, {adjusted_y}) using bounds {aoi_bounds}")
    return adjusted_x, adjusted_y


def annotate_aoi_region(screenshot, aoi_x, aoi_y, aoi_width, aoi_height):
    """
    Annotate screenshot with AOI region rectangle for visual debugging.

    :param screenshot: NumPy array of the screenshot
    :param aoi_x: X percentage of AOI top-left corner (0-100)
    :param aoi_y: Y percentage of AOI top-left corner (0-100)
    :param aoi_width: Width percentage of AOI (0-100)
    :param aoi_height: Height percentage of AOI (0-100)
    :return: Annotated screenshot
    """
    if screenshot is None or not isinstance(screenshot, np.ndarray):
        return screenshot

    try:
        x1, y1, x2, y2 = calculate_aoi_bounds(screenshot.shape, aoi_x, aoi_y, aoi_width, aoi_height)

        # Create a copy for annotation
        annotated = screenshot.copy()

        # AOI rectangle color and thickness
        aoi_color = (255, 0, 0)  # Blue in BGR
        aoi_thickness = 3

        # Draw AOI rectangle
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color=aoi_color, thickness=aoi_thickness)

        # AOI label properties
        label = f"AOI: {aoi_x}%,{aoi_y}% ({aoi_width}%x{aoi_height}%)"
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        font_thickness = 2

        label_size = cv2.getTextSize(label, font, font_scale, font_thickness)[0]
        label_y_offset = 10
        label_y = y1 - label_y_offset if y1 - label_y_offset > label_size[1] else y1 + label_size[1] + label_y_offset

        cv2.putText(annotated, label, (x1, label_y), font, font_scale, aoi_color, font_thickness)

        internal_logger.debug(f"AOI region annotated on screenshot at bounds ({x1}, {y1}, {x2}, {y2})")
        return annotated

    except ValueError as e:
        internal_logger.debug(f"Failed to annotate AOI region: {e}")
        return screenshot


def _is_list_type(param_type: Any) -> bool:
    """
    Check if a parameter type annotation indicates it's a list type.

    Args:
        param_type: The type annotation from inspect.Parameter

    Returns:
        bool: True if the type is a list type (List[str], Optional[List[str]], etc.)
    """
    def _is_list_like(t: Any) -> bool:
        origin = get_origin(t)
        return origin is list or (hasattr(t, '__origin__') and t.__origin__ is list)

    if param_type is None or param_type == inspect.Parameter.empty:
        return False

    # Handle Optional[List[str]] -> Union[List[str], None]
    origin = get_origin(param_type)
    if origin is Union:
        args = get_args(param_type)
        # Check if any of the union types is a list
        for arg in args:
            if _is_list_like(arg):
                return True

    # Handle List[str] directly
    return _is_list_like(param_type)
