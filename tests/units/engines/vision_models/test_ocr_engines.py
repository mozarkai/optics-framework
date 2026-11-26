"""Real OCR engines read a rendered screen and match text locators the same way as every other engine."""
import functools
import os
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from optics_framework.common.strategies import TextDetectionStrategy

SCREEN = [
    ((40, 40), "Sign in to your account"),
    ((40, 140), "Payment"),
    ((40, 240), "Home"), ((340, 240), "Search"), ((640, 240), "Profile"),
    ((40, 340), "Continue with Google"),
    ((40, 440), "Forgot password?"),
    ((40, 540), "Sign in"),
]


def _strict_source():
    config = SimpleNamespace(strict_element_match=True)
    return SimpleNamespace(driver=SimpleNamespace(event_sdk=SimpleNamespace(config_handler=SimpleNamespace(config=config))))


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
