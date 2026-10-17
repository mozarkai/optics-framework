"""OCR and image matches are found in screenshot pixels but tapped in window coordinates."""
from types import SimpleNamespace

import numpy as np
import pytest

from optics_framework.common import utils
from optics_framework.common.strategies import ImageDetectionStrategy, TextDetectionStrategy


class _AppiumScreenshotSource:
    REQUIRED_DRIVER_TYPE = "appium"

    def __init__(self, window, screenshot_size):
        width, height = screenshot_size
        self.driver = SimpleNamespace(get_window_size=lambda: {"width": window[0], "height": window[1]})
        self._frame = np.zeros((height, width, 3), dtype=np.uint8)

    def capture(self):
        return self._frame


def _frame(width, height):
    return np.zeros((height, width, 3), dtype=np.uint8)


@pytest.mark.parametrize(
    ("window", "screenshot", "point", "expected"),
    [
        pytest.param((390, 844), (1170, 2532), (600, 1200), (200, 400), id="ios-3x-retina"),
        pytest.param((375, 812), (750, 1624), (101, 99), (50, 50), id="ios-2x-retina-rounds"),
        pytest.param((1080, 2400), (1080, 2400), (540, 1200), (540, 1200), id="android-same-size"),
        pytest.param((1080, 2131), (1080, 2220), (540, 2190), (540, 2190), id="android-window-without-nav-bar"),
        pytest.param((320, 568), (1170, 2532), (585, 1266), (160, 284), id="ios-legacy-letterbox"),
    ],
)
def test_screenshot_point_to_window(window, screenshot, point, expected):
    source = _AppiumScreenshotSource(window, screenshot)
    assert utils.screenshot_point_to_window(source, _frame(*screenshot), point) == expected


def test_non_appium_sources_keep_screenshot_coordinates():
    source = _AppiumScreenshotSource((390, 844), (1170, 2532))
    source.REQUIRED_DRIVER_TYPE = "playwright"
    assert utils.screenshot_point_to_window(source, _frame(1170, 2532), (600, 1200)) == (600, 1200)


def test_unknown_window_keeps_screenshot_coordinates():
    source = _AppiumScreenshotSource((390, 844), (1170, 2532))
    source.driver = None
    assert utils.screenshot_point_to_window(source, _frame(1170, 2532), (600, 1200)) == (600, 1200)


def _ocr(centre, bbox):
    return SimpleNamespace(find_element=lambda *_args, **_kwargs: ("Login", centre, bbox))


def _template_matcher(centre, bbox):
    return SimpleNamespace(find_element=lambda *_args, **_kwargs: (True, centre, bbox))


def test_text_detection_returns_ios_points():
    source = _AppiumScreenshotSource((390, 844), (1170, 2532))
    strategy = TextDetectionStrategy(source, _ocr((600, 1200), ((550, 1170), (650, 1230))), None)
    assert strategy.locate("Login").value == (200, 400)


def test_text_detection_with_aoi_returns_ios_points():
    source = _AppiumScreenshotSource((390, 844), (1170, 2532))
    # The match sits at (300, 600) inside the bottom-right quarter, which starts at (585, 1266).
    strategy = TextDetectionStrategy(source, _ocr((300, 600), ((250, 570), (350, 630))), None)
    assert strategy.locate_with_aoi("Login", 50, 50, 50, 50).value == (295, 622)


def test_image_detection_returns_ios_points():
    source = _AppiumScreenshotSource((390, 844), (1170, 2532))
    strategy = ImageDetectionStrategy(source, _template_matcher((600, 1200), ((550, 1170), (650, 1230))), None)
    assert strategy.locate("login.png").value == (200, 400)


def test_image_detection_with_aoi_returns_ios_points():
    source = _AppiumScreenshotSource((390, 844), (1170, 2532))
    strategy = ImageDetectionStrategy(source, _template_matcher((300, 600), ((250, 570), (350, 630))), None)
    assert strategy.locate_with_aoi("login.png", 50, 50, 50, 50).value == (295, 622)


def test_annotated_frame_stays_in_screenshot_pixels():
    source = _AppiumScreenshotSource((390, 844), (1170, 2532))
    strategy = TextDetectionStrategy(source, _ocr((600, 1200), ((550, 1170), (650, 1230))), None)
    assert strategy.locate("Login").annotated_frame.shape == (2532, 1170, 3)
