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
from optics_framework.common.base_factory import InstanceFallback


class _FakeWebDriver:
    def __init__(self, width, height):
        self._size = {"width": width, "height": height}

    def get_window_size(self):
        return self._size


class _AppiumSource:
    REQUIRED_DRIVER_TYPE = "appium"

    def __init__(self, wd):
        self.driver = wd


class _SeleniumSource:
    REQUIRED_DRIVER_TYPE = "selenium"

    def __init__(self, wd):
        self.driver = wd


def _elements():
    return [
        {"text": "A", "bounds": {"x1": 20, "y1": 96, "x2": 355, "y2": 140}, "xpath": "//a"},
        {"text": "B", "bounds": {"x1": 0, "y1": 0, "x2": 100, "y2": 50}, "xpath": "//b"},
    ]


def _screenshot(width=1080, height=2340):
    return np.zeros((height, width, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# scale_interactive_element_bounds — dict bounds (API / workspace path).
# Mutates the passed list in place and returns None.
# ---------------------------------------------------------------------------
class TestScaleInteractiveElementBounds:
    def test_appium_window_smaller_than_screenshot_scales(self):
        src = _AppiumSource(_FakeWebDriver(375, 812))
        els = _elements()
        utils.scale_interactive_element_bounds(els, src, _screenshot())
        # ~2.88x / ~2.881x
        assert els[0]["bounds"] == {"x1": 57, "y1": 276, "x2": 1022, "y2": 403}
        assert els[1]["bounds"] == {"x1": 0, "y1": 0, "x2": 288, "y2": 144}

    def test_resolves_instance_fallback(self):
        src = InstanceFallback([_AppiumSource(_FakeWebDriver(375, 812))])
        els = _elements()
        utils.scale_interactive_element_bounds(els, src, _screenshot())
        assert els[0]["bounds"] == {"x1": 57, "y1": 276, "x2": 1022, "y2": 403}

    def test_prefers_current_instance_over_first(self):
        # current_instance is what actually served the request; it must win.
        first = _SeleniumSource(_FakeWebDriver(1, 1))
        current = _AppiumSource(_FakeWebDriver(375, 812))
        fb = InstanceFallback([first, current])
        fb.current_instance = current
        els = _elements()
        utils.scale_interactive_element_bounds(els, fb, _screenshot())
        assert els[0]["bounds"] == {"x1": 57, "y1": 276, "x2": 1022, "y2": 403}

    def test_android_window_equals_screenshot_is_noop(self):
        src = _AppiumSource(_FakeWebDriver(1080, 2340))
        els = _elements()
        utils.scale_interactive_element_bounds(els, src, _screenshot())
        assert els[0]["bounds"] == {"x1": 20, "y1": 96, "x2": 355, "y2": 140}
        assert els[1]["bounds"] == {"x1": 0, "y1": 0, "x2": 100, "y2": 50}

    def test_non_appium_source_is_left_unchanged(self):
        # Selenium driver exposes get_window_size, but its space is unrelated to the
        # screenshot's; scaling must NOT touch it.
        src = _SeleniumSource(_FakeWebDriver(375, 812))
        els = _elements()
        utils.scale_interactive_element_bounds(els, src, _screenshot())
        assert els[0]["bounds"] == {"x1": 20, "y1": 96, "x2": 355, "y2": 140}

    def test_no_screenshot_is_noop(self):
        src = _AppiumSource(_FakeWebDriver(375, 812))
        els = _elements()
        utils.scale_interactive_element_bounds(els, src, None)
        assert els[0]["bounds"] == {"x1": 20, "y1": 96, "x2": 355, "y2": 140}

    def test_missing_window_size_is_noop(self):
        class _NoWinSource:
            REQUIRED_DRIVER_TYPE = "appium"
            driver = object()

        els = _elements()
        utils.scale_interactive_element_bounds(els, _NoWinSource(), _screenshot())
        assert els[0]["bounds"] == {"x1": 20, "y1": 96, "x2": 355, "y2": 140}

    def test_elements_without_bounds_preserved(self):
        src = _AppiumSource(_FakeWebDriver(375, 812))
        els = [{"text": "x", "xpath": "//x"}, {"text": "y", "bounds": None}]
        utils.scale_interactive_element_bounds(els, src, _screenshot())
        assert els[0] == {"text": "x", "xpath": "//x"}
        assert els[1]["bounds"] is None

    def test_malformed_bounds_skipped(self):
        src = _AppiumSource(_FakeWebDriver(375, 812))
        els = [{"bounds": {"x1": 1, "y1": 2}}]  # missing x2/y2
        utils.scale_interactive_element_bounds(els, src, _screenshot())
        assert els[0]["bounds"] == {"x1": 1, "y1": 2}

    def test_empty_list_is_noop(self):
        src = _AppiumSource(_FakeWebDriver(375, 812))
        els = []
        utils.scale_interactive_element_bounds(els, src, _screenshot())
        assert els == []


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

    def test_element_above_the_middle_is_not_lifted(self):
        # "Buy 101 devices": the old per-axis stretch put y1 at 1016, 45px too high.
        assert self._scaled({"x1": 52, "y1": 228, "x2": 181, "y2": 250}) == {
            "x1": 190, "y1": 1061, "x2": 661, "y2": 1141,
        }

    def test_element_below_the_middle_is_not_dropped(self):
        # "Answer": the old stretch put y1 at 1885, 111px too low.
        assert self._scaled({"x1": 224, "y1": 423, "x2": 276, "y2": 442}) == {
            "x1": 819, "y1": 1774, "x2": 1009, "y2": 1843,
        }

    def test_window_height_maps_inside_the_screenshot(self):
        # The old stretch mapped the window's last row onto the frame's last row,
        # hiding the letterbox entirely.
        b = self._scaled({"x1": 0, "y1": 568, "x2": 1, "y2": 568})
        assert b["y1"] == 2304 and b["y1"] < 2532

    def test_near_uniform_ratios_keep_per_axis_scaling(self):
        # 1080/375 = 2.8800 vs 2340/812 = 2.8818 — integer rounding of the window
        # size, not a letterbox: no offset is introduced.
        els = [{"bounds": {"x1": 20, "y1": 96, "x2": 355, "y2": 140}}]
        utils.scale_interactive_element_bounds(
            els, _AppiumSource(_FakeWebDriver(375, 812)), _screenshot()
        )
        assert els[0]["bounds"] == {"x1": 57, "y1": 276, "x2": 1022, "y2": 403}


# ---------------------------------------------------------------------------
# Both consumers share one gate/ratio helper — this cross-checks that the dict
# path and the tuple path land on the same pixel geometry (tuple-path breadth
# lives in test_bbox_scaling.py).
# ---------------------------------------------------------------------------
class TestSharedGateConsistency:
    def test_both_paths_produce_matching_geometry(self):
        src = _AppiumSource(_FakeWebDriver(375, 812))
        els = _elements()
        utils.scale_interactive_element_bounds(els, src, _screenshot())
        b = els[0]["bounds"]
        tuple_out = utils.scale_bboxes_for_screenshot([((20, 96), (355, 140))], src, _screenshot())[0]
        # Both paths must land on the same pixel geometry.
        assert (b["x1"], b["y1"], b["x2"], b["y2"]) == (57, 276, 1022, 403)
        assert tuple_out == ((57, 276), (1022, 403))

    def test_both_paths_agree_on_letterboxed_window(self):
        src = _AppiumSource(_FakeWebDriver(320, 568))
        els = [{"bounds": {"x1": 52, "y1": 228, "x2": 181, "y2": 250}}]
        utils.scale_interactive_element_bounds(els, src, _screenshot(1170, 2532))
        b = els[0]["bounds"]
        tuple_out = utils.scale_bboxes_for_screenshot(
            [((52, 228), (181, 250))], src, _screenshot(1170, 2532)
        )[0]
        assert (b["x1"], b["y1"], b["x2"], b["y2"]) == (190, 1061, 661, 1141)
        assert tuple_out == ((190, 1061), (661, 1141))

    def test_both_paths_skip_non_appium(self):
        src = _SeleniumSource(_FakeWebDriver(375, 812))
        els = _elements()
        utils.scale_interactive_element_bounds(els, src, _screenshot())
        assert els[0]["bounds"] == {"x1": 20, "y1": 96, "x2": 355, "y2": 140}
        assert utils.scale_bboxes_for_screenshot([((20, 96), (355, 140))], src, _screenshot()) == [
            ((20, 96), (355, 140))
        ]
