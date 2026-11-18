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


class _DirectSource:
    REQUIRED_DRIVER_TYPE = "appium"

    def __init__(self, wd):
        self.driver = wd


def _pixel_screenshot(width=1080, height=2340):
    return np.zeros((height, width, 3), dtype=np.uint8)


class TestScaleBboxesForScreenshot:
    BBOX = ((20, 96), (355, 140))

    def test_driver_error_falls_back_unchanged(self):
        src = _DirectSource(_FakeWebDriver(375, 812, raises=True))
        result = utils.scale_bboxes_for_screenshot([self.BBOX], src, _pixel_screenshot())
        assert result == [self.BBOX]

    def test_none_bbox_entries_preserved(self):
        src = _DirectSource(_FakeWebDriver(375, 812))
        result = utils.scale_bboxes_for_screenshot([None, self.BBOX], src, _pixel_screenshot())
        assert result == [None, ((57, 276), (1022, 403))]
