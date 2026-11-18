"""OCR text locators match the same way for every engine, on locate and on assert: whole words when strict, parts of words too otherwise."""
import functools
import os
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from optics_framework.common.strategies import TextDetectionStrategy
from optics_framework.common.text_interface import TextInterface

pytestmark = pytest.mark.white_box

SCREEN = [
    ((40, 40), "Sign in to your account"),
    ((40, 140), "Payment"),
    ((40, 240), "Home"), ((340, 240), "Search"), ((640, 240), "Profile"),
    ((40, 340), "Continue with Google"),
    ((40, 440), "Forgot password?"),
    ((40, 540), "Sign in"),
]


def _quad(left, top, right, bottom):
    return [(left, top), (right, top), (right, bottom), (left, bottom)]


# Real detections of SCREEN: Tesseract and Google Vision return words, EasyOCR returns lines.
CAPTURED = {
    "pytesseract": [
        ("Sign", (42, 46, 107, 78)), ("in", (120, 46, 143, 71)), ("to", (155, 47, 181, 71)),
        ("your", (192, 53, 257, 78)), ("account", (267, 47, 385, 71)), ("Payment", (43, 146, 174, 178)),
        ("Home", (43, 246, 130, 271)), ("Search", (342, 246, 446, 271)), ("Profile", (643, 246, 736, 271)),
        ("Continue", (42, 346, 176, 371)), ("with", (186, 346, 245, 371)), ("Google", (258, 346, 365, 378)),
        ("Forgot", (43, 446, 138, 478)), ("password?", (149, 446, 310, 478)),
        ("Sign", (42, 546, 107, 578)), ("in", (120, 546, 143, 571)),
    ],
    "easyocr": [
        ("Sign in to your account", (32, 37, 394, 86)), ("Payment", (38, 140, 181, 184)),
        ("Home", (37, 239, 136, 282)), ("Search", (336, 240, 453, 280)), ("Profile", (637, 241, 741, 279)),
        ("Continue with Google", (36, 339, 372, 387)), ("Forgot password?", (35, 437, 316, 486)),
        ("Sign in", (32, 537, 150, 585)),
    ],
    "googlevision": [
        ("Sign", (39, 46, 106, 77)), ("in", (118, 46, 142, 76)), ("to", (152, 46, 182, 76)),
        ("your", (189, 46, 259, 76)), ("account", (264, 44, 388, 76)), ("Payment", (41, 145, 175, 176)),
        ("Home", (41, 246, 130, 271)), ("Search", (340, 247, 446, 270)), ("Profile", (641, 247, 735, 271)),
        ("Continue", (40, 342, 177, 374)), ("with", (185, 344, 245, 375)), ("Google", (256, 345, 368, 376)),
        ("Forgot", (42, 446, 138, 476)), ("password", (148, 446, 293, 476)), ("?", (293, 446, 311, 476)),
        ("Sign", (38, 544, 107, 576)), ("in", (116, 543, 144, 573)),
    ],
}

NOT_FOUND = ["Pay", "Sign i", "ign in", "Googl", "Home Search", "Search Profile", "account Payment",
             "Forgot password.", "Log in", "", "   "]

# Google Vision on a Galaxy S22: a Play Store dialog, and app drawer labels and an icon moved below it.
DEVICE = [
    ("Introducing", (244, 535, 592, 594)), ("Google", (612, 538, 841, 596)),
    ("Play", (380, 626, 504, 687)), ("Points", (516, 621, 703, 683)),
    ("Earn", (194, 1159, 275, 1199)), ("points", (290, 1159, 408, 1199)), ("on", (419, 1159, 464, 1199)),
    ("purchases", (480, 1159, 675, 1199)), (",", (675, 1159, 684, 1199)), ("including", (699, 1159, 872, 1199)),
    ("in", (192, 1224, 224, 1265)), ("-", (229, 1224, 245, 1265)), ("app", (246, 1224, 318, 1265)),
    ("items", (330, 1224, 432, 1265)), (".", (433, 1224, 443, 1265)),
    ("Learn", (426, 1536, 537, 1567)), ("more", (553, 1539, 656, 1569)),
    ("Not", (457, 1691, 531, 1722)), ("now", (542, 1694, 621, 1725)),
    ("Clock", (122, 1885, 206, 1908)), ("Contacts", (350, 1884, 479, 1909)),
    ("G", (90, 2050, 235, 2190)), ("Google", (111, 2230, 215, 2258)), ("Maps", (378, 2232, 454, 2258)),
]
DEVICE_NOT_FOUND = ["Learn more Not now", "more Not", "Clock Google", "G Google", "Points Earn", "Clock Contacts"]


class Replay(TextInterface):
    def __init__(self, detections):
        self.detections = [(_quad(*box), text, 0.9) for text, box in detections]

    def detect_text(self, input_data):
        return " ".join(text for _, text, _ in self.detections), self.detections

FRAME = np.zeros((640, 900, 3), dtype=np.uint8)
ENGINES = sorted(CAPTURED)
TOLERANCE = 8


def _strict_source():
    config = SimpleNamespace(strict_element_match=True)
    return SimpleNamespace(driver=SimpleNamespace(event_sdk=SimpleNamespace(config_handler=SimpleNamespace(config=config))))


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("text", NOT_FOUND)
def test_strict_finds_no_part_of_a_word_or_words_of_separate_controls(engine, text):
    assert Replay(CAPTURED[engine]).find_element(FRAME, text, strict=True) is None


@pytest.mark.parametrize("text", DEVICE_NOT_FOUND)
def test_lines_of_separate_controls_do_not_join(text):
    assert Replay(DEVICE).find_element(FRAME, text, strict=True) is None


@pytest.mark.parametrize(("line", "text", "left", "right"), [
    ("מספר 12 34", "12", 60, 100), ("מספר 12 34", "34", 0, 40), ("abc שלום 12", "12", 80, 120),
])
def test_numbers_are_placed_where_bidirectional_text_draws_them(line, text, left, right):
    center = Replay([_line(line, 0, 0, 20 * len(line), 20)]).find_element(FRAME, text, strict=True)[1]
    assert left <= center[0] <= right, center


def test_extra_spaces_inside_one_detection_do_not_split_it():
    assert Replay([_line("Sign      in", 0, 0, 120, 20)]).find_element(FRAME, "Sign in", strict=True)[0]


def _line(text, left, top, right, bottom):
    return (text, (left, top, right, bottom))


def _rendered_screen():
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 640), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=34)
    for xy, text in SCREEN:
        draw.text(xy, text, fill="black", font=font)
    return np.array(image)[:, :, ::-1].copy()


def _real_engines():
    engines = []
    if shutil.which("tesseract"):
        engines.append(pytest.param("pytesseract", id="pytesseract"))
    if _easyocr_models_downloaded():
        engines.append(pytest.param("easyocr", id="easyocr"))
    return engines


def _easyocr_models_downloaded():
    try:
        from easyocr.config import MODULE_PATH, detection_models, recognition_models
        from easyocr.utils import calculate_md5
    except (ImportError, OSError):
        return False
    models = (detection_models["craft"], recognition_models["gen2"]["english_g2"])
    paths = [os.path.join(MODULE_PATH, "model", model["filename"]) for model in models]
    return all(os.path.isfile(path) and calculate_md5(path) == model["md5sum"] for path, model in zip(paths, models))


@pytest.mark.parametrize("name", _real_engines())
def test_real_engines_find_multi_word_text_and_reject_partial_words(name, monkeypatch):
    if name == "pytesseract":
        pytest.importorskip("pytesseract")
        from optics_framework.engines.vision_models.ocr_models.pytesseract import PytesseractHelper as Engine
    else:
        import easyocr

        monkeypatch.setattr(easyocr, "Reader", functools.partial(easyocr.Reader, download_enabled=False))
        from optics_framework.engines.vision_models.ocr_models.easyocr import EasyOCRHelper as Engine
    ocr, frame = Engine({}), _rendered_screen()
    for text in ["Sign in", "sign in to your account", "Continue with Google", "Forgot password"]:
        assert (ocr.find_element(frame, text) or [False])[0], text
    google = ocr.find_element(frame, "Google")
    assert google[1][0] > 230, f"tapped {google[1]} instead of the word Google"
    for text in ["Pay", "Home Search", "Sign i"]:
        assert ocr.find_element(frame, text, strict=True) is None, text
    strategy = TextDetectionStrategy(element_source=_strict_source(), text_detection=ocr, strategy_manager=None)
    assert strategy.check_elements(["Sign in to your account", "Pay"], True, frame)[0] == [True, False]
