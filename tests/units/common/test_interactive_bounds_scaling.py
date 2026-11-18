import numpy as np

from optics_framework.common import utils


class _FakeWebDriver:
    def __init__(self, width, height):
        self._size = {"width": width, "height": height}

    def get_window_size(self):
        return self._size


class _AppiumSource:
    REQUIRED_DRIVER_TYPE = "appium"

    def __init__(self, wd):
        self.driver = wd


def _screenshot(width=1080, height=2340):
    return np.zeros((height, width, 3), dtype=np.uint8)


# A window that does not cover the screenshot: a legacy iOS app in compatibility
# mode gets a 320x568-point window, scaled to fit the width of a 1170x2532-pixel
# screen and centred, so the screenshot keeps a 227px black bar above and below.
# Numbers below are from a real BitbarIOSSample session on an iPhone 14.
class TestLetterboxedWindow:
    SRC = _AppiumSource(_FakeWebDriver(320, 568))

    def _scaled(self, bounds):
        els = [{"bounds": dict(bounds)}]
        utils.scale_interactive_element_bounds(els, self.SRC, _screenshot(1170, 2532))
        return els[0]["bounds"]

    def test_uses_the_fitting_ratio_on_both_axes(self):
        # 3.65625 (1170/320) on both, not 4.4577 (2532/568) vertically.
        assert self._scaled({"x1": 0, "y1": 0, "x2": 320, "y2": 568}) == {
            "x1": 0, "y1": 227, "x2": 1170, "y2": 2304,
        }
