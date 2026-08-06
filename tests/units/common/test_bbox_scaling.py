"""Unit tests for bbox -> screenshot pixel-space scaling.

Regression coverage for annotation boxes drawn in the wrong position/size when
the driver's window coordinate space differs in resolution from the captured
screenshot. See utils.scale_bboxes_for_screenshot / _pixel_scale_for_source /
_window_size_from_source / _apply_scale_to_bbox.

Scaling is gated to Appium sources (their window and element coordinates share one
space), so the fixtures here declare REQUIRED_DRIVER_TYPE = "appium"; a non-Appium
regression guard lives in test_non_appium_annotation_is_noop.
"""
import numpy as np

from optics_framework.common import utils


class _FakeWebDriver:
    def __init__(self, width, height, raises=False):
        self._size = {"width": width, "height": height}
        self._raises = raises

    def get_window_size(self):
        if self._raises:
            raise RuntimeError("driver boom")
        return self._size


class _WrappedSource:
    """Element source whose .driver wraps the real WebDriver one level in (.driver.driver)."""

    REQUIRED_DRIVER_TYPE = "appium"

    def __init__(self, wd):
        self.driver = type("Wrapper", (), {"driver": wd})()


class _DirectSource:
    REQUIRED_DRIVER_TYPE = "appium"

    def __init__(self, wd):
        self.driver = wd


def _pixel_screenshot(width=1080, height=2340):
    return np.zeros((height, width, 3), dtype=np.uint8)


class TestScaleBboxesForScreenshot:
    BBOX = ((20, 96), (355, 140))

    def test_window_smaller_than_screenshot_scales_up(self):
        # Window 375x812, screenshot 1080x2340 -> ~2.88x per axis.
        src = _DirectSource(_FakeWebDriver(375, 812))
        result = utils.scale_bboxes_for_screenshot([self.BBOX], src, _pixel_screenshot())
        assert result == [((57, 276), (1022, 403))]

    def test_unwraps_nested_driver(self):
        src = _WrappedSource(_FakeWebDriver(375, 812))
        result = utils.scale_bboxes_for_screenshot([self.BBOX], src, _pixel_screenshot())
        assert result == [((57, 276), (1022, 403))]

    def test_window_equals_screenshot_is_noop(self):
        # window size == screenshot size -> scale 1.0, bboxes untouched.
        src = _DirectSource(_FakeWebDriver(1080, 2340))
        result = utils.scale_bboxes_for_screenshot([self.BBOX], src, _pixel_screenshot())
        assert result == [self.BBOX]

    def test_source_without_window_size_falls_back_unchanged(self):
        src = type("NoWindowSource", (), {"REQUIRED_DRIVER_TYPE": "appium", "driver": object()})()
        result = utils.scale_bboxes_for_screenshot([self.BBOX], src, _pixel_screenshot())
        assert result == [self.BBOX]

    def test_non_appium_annotation_is_noop(self):
        # Regression guard: the annotation path shares the Appium gate with the API
        # path, so a web source's window/element spaces (unrelated) are never scaled.
        src = type("SeleniumSource", (), {"REQUIRED_DRIVER_TYPE": "selenium"})()
        src.driver = _FakeWebDriver(375, 812)
        result = utils.scale_bboxes_for_screenshot([self.BBOX], src, _pixel_screenshot())
        assert result == [self.BBOX]

    def test_missing_screenshot_falls_back_unchanged(self):
        src = _DirectSource(_FakeWebDriver(375, 812))
        result = utils.scale_bboxes_for_screenshot([self.BBOX], src, None)
        assert result == [self.BBOX]

    def test_driver_error_falls_back_unchanged(self):
        src = _DirectSource(_FakeWebDriver(375, 812, raises=True))
        result = utils.scale_bboxes_for_screenshot([self.BBOX], src, _pixel_screenshot())
        assert result == [self.BBOX]

    def test_zero_window_size_falls_back_unchanged(self):
        src = _DirectSource(_FakeWebDriver(0, 0))
        result = utils.scale_bboxes_for_screenshot([self.BBOX], src, _pixel_screenshot())
        assert result == [self.BBOX]

    def test_malformed_bbox_preserved_unchanged(self):
        # A bbox that isn't ((x1,y1),(x2,y2)) shaped can't be unpacked;
        # _apply_scale_to_bbox returns it unchanged rather than raising.
        src = _DirectSource(_FakeWebDriver(375, 812))
        malformed = (1, 2, 3, 4)
        result = utils.scale_bboxes_for_screenshot([malformed], src, _pixel_screenshot())
        assert result == [malformed]

    def test_none_bbox_entries_preserved(self):
        src = _DirectSource(_FakeWebDriver(375, 812))
        result = utils.scale_bboxes_for_screenshot([None, self.BBOX], src, _pixel_screenshot())
        assert result == [None, ((57, 276), (1022, 403))]

    def test_empty_list_returns_empty(self):
        src = _DirectSource(_FakeWebDriver(375, 812))
        assert utils.scale_bboxes_for_screenshot([], src, _pixel_screenshot()) == []

    def test_letterboxed_window_is_centred_not_stretched(self):
        # window 100x200, screenshot 300x200: the window already fills the height, so
        # it is a 100px-wide column centred in the frame, not something to stretch 3x
        # horizontally. Both axes scale by 1.0 and x gains a 100px offset — distinct
        # per-axis offsets still catch an x/y swap (which would yield ((10,110),(20,120))).
        src = _DirectSource(_FakeWebDriver(100, 200))
        result = utils.scale_bboxes_for_screenshot(
            [((10, 10), (20, 20))], src, _pixel_screenshot(width=300, height=200)
        )
        assert result == [((110, 10), (120, 20))]
