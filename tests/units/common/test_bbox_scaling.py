"""Unit tests for bbox -> screenshot pixel-space scaling.

Regression coverage for annotation boxes drawn in the wrong position/size when
the driver's window coordinate space differs in resolution from the captured
screenshot. See utils.scale_bboxes_for_screenshot / _window_size_from_source /
_scale_bbox.
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

    def __init__(self, wd):
        self.driver = type("Wrapper", (), {"driver": wd})()


class _DirectSource:
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
        src = type("NoWindowSource", (), {"driver": object()})()
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

    def test_none_bbox_entries_preserved(self):
        src = _DirectSource(_FakeWebDriver(375, 812))
        result = utils.scale_bboxes_for_screenshot([None, self.BBOX], src, _pixel_screenshot())
        assert result == [None, ((57, 276), (1022, 403))]

    def test_empty_list_returns_empty(self):
        src = _DirectSource(_FakeWebDriver(375, 812))
        assert utils.scale_bboxes_for_screenshot([], src, _pixel_screenshot()) == []
