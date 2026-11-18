"""Unit tests for scaling element coordinates into screenshot pixel space.

Two consumers share one gate/ratio helper (``utils._pixel_scale_for_source``):

* ``scale_interactive_element_bounds`` — scales the dict ``bounds`` on elements from
  get_interactive_elements / get_screen_elements / the serve workspace stream, in place.
* ``scale_bboxes_for_screenshot`` — returns scaled ``((x1,y1),(x2,y2))`` bboxes for
  annotated screenshots.

Both are gated to Appium sources; other platforms (and missing windows/screenshots)
are left unchanged. These tests pin that shared behavior for both shapes.
"""
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


# ---------------------------------------------------------------------------
# scale_interactive_element_bounds — dict bounds (API / workspace path).
# Mutates the passed list in place and returns None.
# ---------------------------------------------------------------------------
class TestScaleInteractiveElementBounds:


    def test_malformed_bounds_skipped(self):
        src = _AppiumSource(_FakeWebDriver(375, 812))
        els = [{"bounds": {"x1": 1, "y1": 2}}]  # missing x2/y2
        utils.scale_interactive_element_bounds(els, src, _screenshot())
        assert els[0]["bounds"] == {"x1": 1, "y1": 2}


# ---------------------------------------------------------------------------
# A window that does not cover the screenshot: a legacy iOS app in compatibility
# mode gets a 320x568-point window, scaled to fit the width of a 1170x2532-pixel
# screen and centred, so the screenshot keeps a 227px black bar above and below.
# Numbers below are from a real BitbarIOSSample session on an iPhone 14.
# ---------------------------------------------------------------------------
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
