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
from typing import Callable, List, Optional, Tuple, Any, Union, get_origin, get_args
import inspect
from skimage.metrics import structural_similarity as ssim
from optics_framework.common.logging_config import internal_logger

OUTPUT_PATH_NOT_SET_MSG = "output_dir is required. Pass it from the session's execution_output_path."
TEXT_ONLY_PREFIX = "text_only:"


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


# Placeholder used when unescaping so that \\\\ is processed before \\n/\\r/\\t.
_UNESCAPE_PLACEHOLDER = "\x00"


def unescape_csv_value(s: str) -> str:
    """
    Interpret backslash escape sequences in CSV-originated strings (e.g. element IDs,
    locators, XPaths) so newlines and other characters can be represented in one line.
    Inverse of escape_csv_value: for CSV-escaped str s, escape_csv_value(unescape_csv_value(s)) == s.

    \\\\ must be processed first, not last. Processing \\\\ last would incorrectly
    turn \\\\n into newline (because \\n would match first); the correct behavior
    is backslash followed by the letter n. Order of operations:
    1. Replace \\\\ with a placeholder.
    2. Replace \\n, \\r, \\t with newline, carriage return, tab.
    3. Replace the placeholder with a single backslash.
    """
    if not isinstance(s, str):
        raise TypeError(f"unescape_csv_value expects str, got {type(s).__name__}")
    if not s:
        return s
    s = s.replace("\\\\", _UNESCAPE_PLACEHOLDER)
    s = s.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
    s = s.replace(_UNESCAPE_PLACEHOLDER, "\\")
    return s


def escape_csv_value(s: str) -> str:
    """
    Convert a string to one-line, CSV-friendly form for output (e.g. XPaths from
    get_interactive_elements). Inverse of unescape_csv_value: for any str s,
    unescape_csv_value(escape_csv_value(s)) == s.

    Order of operations (backslash first so sequences are not double-escaped):
    1. Replace \\ with \\\\. 2. Replace newline with \\n, \\r with \\r, \\t with \\t.
    """
    if not isinstance(s, str):
        raise TypeError(f"escape_csv_value expects str, got {type(s).__name__}")
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def determine_element_type(element):
    element = element.strip()
    el = element.lower()

    if el.startswith(TEXT_ONLY_PREFIX) or el.startswith("text="):
        return "Text"
    if el.endswith((".jpg", ".jpeg", ".png", ".bmp")):
        return "Image"
    if el.startswith("css="):
        return "CSS"
    if el.startswith("xpath=") or element.startswith(("/", "//", "(")):
        return "XPath"
    if el.startswith("id:"):
        return "ID"
    if el.startswith(("android.", "xcui")):
        return "Class"

    # Per CSS spec, a valid #id must start with a letter, underscore, or hyphen after #.
    # Strings that begin with a digit after # are not valid CSS selectors and fall through to Text. E.g. #91
    _css_tags = {"input", "button", "div", "span", "a", "img", "select", "textarea",
                 "form", "label", "p", "h1", "h2", "h3", "h4", "h5", "h6"}
    is_css_id  = element.startswith("#") and len(element) > 1 and (element[1].isalpha() or element[1] in "_-")
    is_css_tag = any(el.startswith(tag + c) for tag in _css_tags for c in ("[", "#", "."))
    if ("[" in element and "]" in element) or element.startswith(".") or is_css_id or is_css_tag:
        return "CSS"

    return "Text"


def parse_text_only_prefix(element: str) -> Tuple[str, bool]:
    """Return (stripped_element, use_text_detection_only)."""
    if element.lower().startswith(TEXT_ONLY_PREFIX):
        return (element[len(TEXT_ONLY_PREFIX) :].lstrip(), True)
    return (element, False)


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

def encode_numpy_to_png_bytes(image: np.ndarray) -> bytes:
    """
    Encodes a NumPy image (OpenCV BGR format) to raw PNG bytes.

    :param image: The input image as a NumPy array (BGR format).
    :return: PNG-encoded bytes.
    :raises ValueError: If the image is not a valid, non-empty NumPy array.
    """
    if image is None or not isinstance(image, np.ndarray):
        raise ValueError("Input image must be a valid NumPy array")

    if image.size == 0 or image.shape[0] == 0 or image.shape[1] == 0:
        raise ValueError("Input image is empty or has invalid dimensions")

    _, buffer = cv2.imencode('.png', image)
    return buffer.tobytes()


def encode_numpy_to_base64(image: np.ndarray) -> str:
    """
    Encodes a NumPy image (OpenCV format) to a base64 string.

    :param image: The input image as a NumPy array (BGR format).
    :return: Base64 encoded string.
    """
    return base64.b64encode(encode_numpy_to_png_bytes(image)).decode('utf-8')

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
        return True

    # 2. Partial Match (substring, return immediately)
    if target_text in given_text:
        internal_logger.debug(f"Partial match found: '{target_text}' in '{given_text}'")
        return True

    # 3. Fuzzy Match (only if exact and partial checks fail)
    fuzzy_match_score = fuzz.ratio(given_text, target_text)
    internal_logger.debug(f"Fuzzy match score for '{given_text}' and '{target_text}': {fuzzy_match_score}")
    if fuzzy_match_score >= 80:  # Threshold for "close enough"
        internal_logger.debug(f"Fuzzy match found for text: {given_text}, matched text '{target_text}' with fuzzy score {fuzzy_match_score}")
        return True

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
        internal_logger.debug("Image is empty. Cannot save screenshot.")
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

# Genuine interactivity signals. NOTE: "enabled" is deliberately excluded —
# virtually every Android node carries enabled="true" (including pure layout
# wrappers), so treating it as "interesting" would defeat condensation. It
# remains a *shown* flag below when a node is kept for another reason.
_PS_INTERACTIVE_FLAGS = ("clickable", "long-clickable", "scrollable", "checkable")
_PS_SHOWN_FLAGS = ("clickable", "scrollable", "long-clickable", "checked", "selected",
                   "focused", "enabled")

# Text-bearing attribute names across platforms (Android, iOS, web).
_TEXT_ATTRS = ("text", "content-desc", "name", "label", "value", "title", "alt")
_ID_ATTRS = ("resource-id", "id", "accessibility-id")
_BOUNDS_ATTRS = ("bounds",)  # Android-style "[x1,y1][x2,y2]"
_RECT_ATTRS = ("x", "y", "width", "height")  # iOS-style separate attributes


def _short_class(el) -> str:
    """Short class/tag label — works for any XML-based UI hierarchy."""
    cls = el.get("class") or str(el.tag) or "node"
    return cls.rsplit(".", 1)[-1]


def _is_interesting(el) -> bool:
    """Return True for nodes that carry user-visible text or are interactive.

    Platform-agnostic: checks common text and interactivity attributes across
    Android (Appium), iOS (XCUITest), and web (Selenium/Playwright) hierarchies.
    """
    # Has meaningful text?
    for attr in _TEXT_ATTRS:
        if (el.get(attr) or "").strip():
            return True
    # Is interactive?
    if any(el.get(flag) == "true" for flag in _PS_INTERACTIVE_FLAGS):
        return True
    # Platform-specific class heuristics for editable / password fields.
    cls = el.get("class") or el.tag or ""
    if cls.endswith("EditText") or cls.endswith("TextField") or cls.endswith("SecureTextField"):
        return True
    if el.get("password") == "true":
        return True
    return False


def _descriptor_text(el) -> Optional[str]:
    """First non-empty text-bearing attribute as ``label="value"`` (one is enough)."""
    for attr in _TEXT_ATTRS:
        val = " ".join((el.get(attr) or "").split())
        if val:
            label = "desc" if attr == "content-desc" else attr
            return f'{label}="{val}"'
    return None


def _descriptor_id(el) -> Optional[str]:
    """First non-empty id attribute as ``id=<short>`` (package prefix stripped)."""
    for attr in _ID_ATTRS:
        rid = el.get(attr) or ""
        if rid:
            return f"id={rid.split('/', 1)[-1]}"
    return None


def _descriptor_bounds(el) -> Optional[str]:
    """Android-style ``bounds=...`` or, failing that, an iOS/web ``rect=(...)``."""
    bounds = el.get("bounds")
    if bounds:
        return f"bounds={bounds}"
    x, y, w, h = el.get("x"), el.get("y"), el.get("width"), el.get("height")
    if x is not None and y is not None:
        return f"rect=({x},{y},{w or '?'},{h or '?'})"
    return None


def _descriptor(el) -> str:
    """One-line human-readable description of a UI node (platform-agnostic)."""
    hint = " ".join((el.get("hint") or "").split())
    parts = [
        _short_class(el),
        _descriptor_text(el),
        _descriptor_id(el),
        _descriptor_bounds(el),
        *(flag for flag in _PS_SHOWN_FLAGS if el.get(flag) == "true"),
        f'hint="{hint}"' if hint else None,
    ]
    return " ".join(p for p in parts if p)


def _walk(el, depth: int, lines: List[str]) -> None:
    # lxml represents comments / processing instructions with a callable .tag
    # (not a str). Skip them — otherwise the attribute checks below raise
    # AttributeError, which would break the documented graceful-degradation.
    if not isinstance(el.tag, str):
        return
    kept = _is_interesting(el)
    if kept:
        lines.append("  " * min(depth, 12) + _descriptor(el))
    next_depth = depth + 1 if kept else depth
    for child in el:
        _walk(child, next_depth, lines)


def strip_page_source(page_source: str, max_chars: int = 12000) -> str:
    """Condense a UI-hierarchy XML dump into a compact, LLM-friendly outline.

    Keeps only signal-bearing nodes — those with visible text, a description, or
    that are interactive — and a minimal attribute set per node. Kept nodes are
    indented to preserve hierarchy. Pure layout wrappers are dropped.

    Works across platforms: Android (Appium UIAutomator2), iOS (XCUITest), and
    web (Selenium/Playwright) page sources all use XML-like hierarchies with
    different attribute names; the helper functions check common attributes from
    each platform.

    Returns ``""`` if empty or unparseable, and truncates to ``max_chars`` to
    bound prompt size.

    .. note::
       For structured element extraction with bounding boxes and XPaths, prefer
       the element-source ``get_interactive_elements()`` API instead. This
       function is a lightweight text-only summary for LLM prompt injection.
    """
    if not page_source:
        return ""
    try:
        from lxml import etree  # type: ignore[import-untyped]
        root = etree.fromstring(page_source.encode("utf-8"))
    except Exception:  # noqa: BLE001 - any malformed source degrades to "no page source"
        return ""

    lines: List[str] = []
    _walk(root, 0, lines)
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n… (truncated)"
    return out


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
                internal_logger.debug("Invalid log file: missing closing </logs> tag.")
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


def bbox_from_appium_attribute_fallback(element: Any) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """
    Parse bounding box from Appium element get_attribute results.
    Android: get_attribute("bounds") returns "[x1,y1][x2,y2]".
    iOS (XCUITest): get_attribute("rect") returns a dict or JSON string with x, y, width, height.

    :param element: WebElement-like object with get_attribute.
    :return: ((x1,y1), (x2,y2)) or None if not available.
    """
    get_attr = getattr(element, "get_attribute", None)
    if get_attr is None:
        return None
    # Android: bounds="[x1,y1][x2,y2]"
    try:
        bounds = get_attr("bounds")
        if bounds and isinstance(bounds, str):
            nums = re.findall(r"\d+", bounds)
            if len(nums) == 4:
                x1, y1, x2, y2 = map(int, nums)
                return ((x1, y1), (x2, y2))
    except (TypeError, ValueError, AttributeError):
        pass
    # iOS: rect as dict or JSON string
    try:
        rect = get_attr("rect")
        if rect is None:
            return None
        if isinstance(rect, dict):
            x, y = int(rect.get("x", 0)), int(rect.get("y", 0))
            w, h = int(rect.get("width", 0)), int(rect.get("height", 0))
            return ((x, y), (x + w, y + h))
        if isinstance(rect, str):
            parsed = json.loads(rect)
            if isinstance(parsed, dict):
                x = int(parsed.get("x", 0))
                y = int(parsed.get("y", 0))
                w = int(parsed.get("width", 0))
                h = int(parsed.get("height", 0))
                return ((x, y), (x + w, y + h))
    except (TypeError, ValueError, AttributeError):
        pass
    return None


def bbox_from_webelement_like(obj: Any) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """
    Return bounding box for a single WebElement-like object with .location/.size or .rect.

    Works for both Android (UIAutomator2) and iOS (XCUITest); both expose W3C-compatible
    location, size, and rect on the WebElement.

    :param obj: WebElement-like object with .location and .size dicts, or .rect (Selenium 4/Appium).
    :return: ((x1,y1), (x2,y2)) or None if not available.
    """
    if obj is None:
        return None
    try:
        loc = getattr(obj, "location", None)
        sz = getattr(obj, "size", None)
        if loc is not None and sz is not None:
            x1 = int(loc.get("x", 0))
            y1 = int(loc.get("y", 0))
            x2 = int(x1 + sz.get("width", 0))
            y2 = int(y1 + sz.get("height", 0))
            return ((x1, y1), (x2, y2))
    except (TypeError, ValueError, AttributeError):
        pass
    try:
        rect = getattr(obj, "rect", None)
        if rect is not None and isinstance(rect, dict):
            x1 = int(rect.get("x", 0))
            y1 = int(rect.get("y", 0))
            w = int(rect.get("width", 0))
            h = int(rect.get("height", 0))
            return ((x1, y1), (x1 + w, y1 + h))
    except (TypeError, ValueError, AttributeError):
        pass
    return None


def _window_size_from_source(element_source: Any) -> Optional[Tuple[int, int]]:
    """
    Best-effort fetch of the driver's reported window size from an element source.

    The window size defines the coordinate space that element bounding boxes are
    expressed in. On some platforms this differs from the screenshot's resolution
    (see scale_bboxes_for_screenshot), so callers need it to convert between the two.

    Duck-typed and defensive: returns None when the source has no driver, the driver
    does not expose ``get_window_size``, or the call fails, so callers can fall back
    to unscaled annotation rather than raising. The element source exposes its driver
    via ``.driver``, which may itself wrap the underlying WebDriver one level in.

    :param element_source: An element source instance (may be any object).
    :return: (width, height), or None if undeterminable.
    """
    try:
        driver = getattr(element_source, "driver", None)
        for candidate in (driver, getattr(driver, "driver", None)):
            get_window_size = getattr(candidate, "get_window_size", None)
            if callable(get_window_size):
                size = get_window_size()
                width = int(size["width"])
                height = int(size["height"])
                if width > 0 and height > 0:
                    return width, height
                return None
    except Exception:
        return None
    return None


def _scale_bbox(
    bbox: Optional[Tuple[Tuple[int, int], Tuple[int, int]]],
    window_size: Tuple[int, int],
    screenshot: np.ndarray,
) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """
    Scale a single bbox from window-coordinate space into the screenshot's
    pixel space.

    A bbox is reported in the driver's window coordinate space, while the
    screenshot may be captured at a different resolution. Multiply each corner by
    the per-axis scale (screenshot size / window size) to map it onto the image.
    When the window size equals the screenshot size the scale is 1.0 and this is a
    no-op (``int(x * 1.0) == x``). Returns the bbox unchanged on any failure.
    """
    if bbox is None:
        return bbox
    try:
        (x1, y1), (x2, y2) = bbox
        win_w, win_h = window_size
        sh_h, sh_w = screenshot.shape[:2]
        if win_w <= 0 or win_h <= 0:
            return bbox
        scale_x = sh_w / win_w
        scale_y = sh_h / win_h
        return (
            (int(x1 * scale_x), int(y1 * scale_y)),
            (int(x2 * scale_x), int(y2 * scale_y)),
        )
    except (TypeError, ValueError, AttributeError):
        return bbox


def scale_bboxes_for_screenshot(
    bboxes: List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]],
    element_source: Any,
    screenshot: Optional[np.ndarray],
) -> List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]]:
    """
    Scale element bounding boxes into the screenshot's pixel space before
    annotation, using the element source's driver window size.

    Bounding boxes obtained from a driver's element handles are expressed in the
    driver's window coordinate space. When that space differs in resolution from
    the captured screenshot, drawing the raw coordinates places the boxes at the
    wrong position and size; scaling each box by (screenshot size / window size)
    maps them correctly. When the two sizes match, the scale is 1.0 and the boxes
    are left unchanged.

    Best-effort and non-failing: if the window size can't be determined (e.g. the
    source has no driver, or the driver errors) or no screenshot is available, the
    bboxes are returned unchanged so annotation falls back to drawing them as-is.
    Window size is fetched once for the whole list. Do not use this for OCR /
    image-detection bboxes — those are already computed in the screenshot's pixel
    space and need no conversion.

    :param bboxes: List of ((x1,y1),(x2,y2)) bboxes (or None entries) in window space.
    :param element_source: The element source whose driver defines the window space.
    :param screenshot: The captured frame the bboxes will be drawn onto.
    :return: bboxes scaled to the screenshot's pixel space, or unchanged on any failure.
    """
    if screenshot is None or not bboxes:
        return bboxes
    window_size = _window_size_from_source(element_source)
    if window_size is None:
        return bboxes
    return [_scale_bbox(b, window_size, screenshot) for b in bboxes]


def bboxes_from_webelements(
    locate_fn: Callable[[str], Any],
    elements: List[str],
) -> List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]]:
    """
    Return bounding boxes for each element using located objects' location and size.

    :param locate_fn: Callable that takes an element id and returns a WebElement-like
        object with .location and .size dicts, or None. If it raises, that element
        is treated as None and processing continues for the rest.
    :param elements: List of element identifiers to locate.
    :return: For each element, ((x1,y1), (x2,y2)) or None if not available.
    """
    result: List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]] = []
    for element in elements:
        try:
            obj = locate_fn(element)
        except Exception:
            result.append(None)
            continue
        result.append(bbox_from_webelement_like(obj))
    return result
