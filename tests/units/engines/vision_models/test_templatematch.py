"""Unit tests for TemplateMatchingHelper on synthetic frames (no device)."""

import cv2
import numpy as np
import pytest

from optics_framework.common.models import TemplateData
from optics_framework.engines.vision_models.image_models.templatematch import (
    _INDEX_MISS,
    TemplateMatchingHelper,
)

pytestmark = pytest.mark.white_box


def make_helper(**capabilities) -> TemplateMatchingHelper:
    config: dict = {"templates": TemplateData()}
    if capabilities:
        config["capabilities"] = capabilities
    return TemplateMatchingHelper(config=config)


def noise(rng: np.random.Generator, *shape: int) -> np.ndarray:
    return rng.integers(0, 255, shape, dtype=np.uint8)


def paste(frame: np.ndarray, template: np.ndarray, y: int, x: int) -> None:
    h, w = template.shape[:2]
    frame[y : y + h, x : x + w] = template


def frame_with(frame: np.ndarray, template: np.ndarray, spots: list) -> np.ndarray:
    for y, x in spots:
        paste(frame, template, y, x)
    return frame


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(1234)


@pytest.fixture
def helper() -> TemplateMatchingHelper:
    return make_helper()


class TestHelperConstruction:
    def test_none_config_raises(self):
        with pytest.raises(ValueError, match="Configuration must be provided"):
            TemplateMatchingHelper(config=None)

    def test_sift_nfeatures_default(self, helper):
        assert helper._sift_nfeatures == 2000

    def test_sift_nfeatures_overridable_and_coerced(self):
        assert make_helper(sift_nfeatures="500")._sift_nfeatures == 500


class TestMatchTemplateFast:
    def test_exact_match_returns_center_and_bbox(self, rng, helper):
        template = noise(rng, 20, 30, 3)
        frame = noise(rng, 120, 150, 3)
        y, x = 40, 60
        frame_with(frame, template, [(y, x)])

        result = helper._match_template_fast(frame, template, 0.85, None)

        found, center, bbox = result
        assert found is True
        assert center == (x + 15, y + 10)
        assert bbox == ((x, y), (x + 30, y + 20))

    def test_absent_template_returns_none(self, rng, helper):
        template = noise(rng, 20, 30, 3)
        frame = noise(rng, 120, 150, 3)

        assert helper._match_template_fast(frame, template, 0.85, 0) is None

    def test_oversized_template_returns_none(self, rng, helper):
        template = noise(rng, 50, 200, 3)
        frame = noise(rng, 120, 150, 3)

        assert helper._match_template_fast(frame, template, 0.85, None) is None

    def test_zero_sized_template_returns_none(self, rng, helper):
        template = np.zeros((0, 10, 3), dtype=np.uint8)
        frame = noise(rng, 120, 150, 3)

        assert helper._match_template_fast(frame, template, 0.85, None) is None

    def test_index_none_returns_a_qualifying_match(self, rng, helper):
        template = noise(rng, 16, 24, 3)
        frame = noise(rng, 150, 200, 3)
        spots = [(20, 30), (90, 120)]
        frame_with(frame, template, spots)
        expected = {(x + 12, y + 8) for y, x in spots}

        _, center, _ = helper._match_template_fast(frame, template, 0.85, None)

        assert center in expected

    def test_index_selects_reading_order(self, rng, helper):
        template = noise(rng, 16, 24, 3)
        frame = noise(rng, 150, 200, 3)
        top, bottom = (20, 30), (90, 120)
        frame_with(frame, template, [top, bottom])

        _, center0, _ = helper._match_template_fast(frame, template, 0.85, 0)
        _, center1, _ = helper._match_template_fast(frame, template, 0.85, 1)

        assert center0 == (30 + 12, 20 + 8)
        assert center1 == (120 + 12, 90 + 8)

    def test_index_beyond_last_is_index_miss(self, rng, helper):
        template = noise(rng, 16, 24, 3)
        frame = noise(rng, 150, 200, 3)
        frame_with(frame, template, [(20, 30), (90, 120)])

        result = helper._match_template_fast(frame, template, 0.85, 2)

        assert result is _INDEX_MISS

    def test_negative_index_is_rejected(self, rng, helper):
        template = noise(rng, 16, 24, 3)
        frame = noise(rng, 150, 200, 3)
        frame_with(frame, template, [(20, 30)])

        result = helper._match_template_fast(frame, template, 0.85, -1)

        assert result is _INDEX_MISS


class TestDistinctPeaks:
    def test_two_single_pixel_peaks_in_reading_order(self, helper):
        res = np.zeros((40, 50), dtype=np.float32)
        res[30, 5] = 0.90
        res[10, 40] = 0.99

        peaks = TemplateMatchingHelper._distinct_peaks(res, 0.85, tw=4, th=4)

        assert peaks == [(40, 10), (5, 30)]

    def test_plateau_collapses_to_one_peak(self, helper):
        res = np.zeros((30, 40), dtype=np.float32)
        res[5:8, 5:8] = 0.95

        peaks = TemplateMatchingHelper._distinct_peaks(res, 0.85, tw=8, th=8)

        assert len(peaks) == 1

    def test_single_match_yields_exactly_one_peak(self, rng, helper):
        template = noise(rng, 16, 24, 3)
        frame = noise(rng, 150, 200, 3)
        frame_with(frame, template, [(70, 80)])
        res = cv2.matchTemplate(
            cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
            cv2.cvtColor(template, cv2.COLOR_BGR2GRAY),
            cv2.TM_CCOEFF_NORMED,
        )

        peaks = TemplateMatchingHelper._distinct_peaks(res, 0.85, tw=24, th=16)

        assert len(peaks) == 1
        assert helper._match_template_fast(frame, template, 0.85, 1) is _INDEX_MISS

    def test_wide_short_template_keeps_stacked_rows(self, helper):
        """Per-axis radii keep stacked instances a single radius would merge."""
        res = np.zeros((220, 100), dtype=np.float32)
        rows = list(range(50, 210, 20))
        for i, y in enumerate(rows):
            res[y, 20] = 0.90 + 0.001 * i

        peaks = TemplateMatchingHelper._distinct_peaks(res, 0.85, tw=90, th=10)

        assert len(peaks) == 8
        assert [p[1] for p in peaks] == rows

    def test_instances_closer_than_half_template_merge(self, helper):
        """Documented NMS limit: sub-radius instances count as one group."""
        res = np.zeros((30, 40), dtype=np.float32)
        res[10, 10] = 0.99
        res[10, 13] = 0.98

        peaks = TemplateMatchingHelper._distinct_peaks(res, 0.85, tw=12, th=4)

        assert peaks == [(10, 10)]


class TestElementExist:
    def test_offset_applied_to_fast_path_center(self, rng, helper):
        template = noise(rng, 16, 24, 3)
        frame = noise(rng, 120, 150, 3)
        y, x = 40, 60
        frame_with(frame, template, [(y, x)])

        _, center, bbox = helper.element_exist(frame, template, offset=[5, -7])

        assert center == (x + 12 + 5, y + 8 + 7)  # center_x += dx, center_y -= dy
        assert bbox == [(x, y), (x + 24, y + 16)]

    def test_missing_inputs_raise(self, helper):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        with pytest.raises(ValueError):
            helper.element_exist(None, frame)
        with pytest.raises(ValueError):
            helper.element_exist(frame, None)

    def test_featureless_images_raise_from_sift_fallback(self, helper):
        """No-variance input falls through to SIFT, which raises on the miss."""
        frame = np.full((100, 100, 3), 128, dtype=np.uint8)
        same = frame.copy()

        with pytest.raises(RuntimeError, match="SIFT feature detection failed"):
            helper.element_exist(frame, same)


class TestFindElement:
    @pytest.fixture
    def templated(self, tmp_path):
        def _make(name: str, image: np.ndarray) -> TemplateMatchingHelper:
            path = tmp_path / name
            cv2.imwrite(str(path), image)
            helper = make_helper()
            helper.templates.add_template(name, str(path))
            return helper

        return _make

    def test_found_via_template_file(self, rng, templated):
        template = noise(rng, 16, 24, 3)
        frame = noise(rng, 120, 150, 3)
        y, x = 40, 60
        frame_with(frame, template, [(y, x)])
        helper = templated("btn.png", template)

        found, center, bbox = helper.find_element(frame, "btn.png")

        assert found is True
        assert center == (x + 12, y + 8)
        assert bbox == ((x, y), (x + 24, y + 16))

    def test_index_miss_returns_none_not_sift_false_positive(self, rng, templated):
        """index=5 with two real matches is not-found, never a SIFT hit."""
        template = noise(rng, 16, 24, 3)
        frame = noise(rng, 150, 200, 3)
        frame_with(frame, template, [(20, 30), (90, 120)])
        helper = templated("btn.png", template)

        assert helper.find_element(frame, "btn.png", index=5) is None

    def test_scaled_template_falls_back_to_sift(self, rng, templated):
        """A 2x-scaled instance is invisible to the fast path; SIFT recovers it."""
        small = np.random.default_rng(7).integers(40, 240, (7, 10), dtype=np.uint8)
        template = cv2.cvtColor(
            cv2.resize(small, (60, 42), interpolation=cv2.INTER_NEAREST),
            cv2.COLOR_GRAY2BGR,
        )
        frame = noise(rng, 220, 280, 3)
        scaled = cv2.resize(template, (120, 84), interpolation=cv2.INTER_LINEAR)
        y, x = 60, 80
        frame_with(frame, scaled, [(y, x)])
        helper = templated("btn.png", template)

        found, center, _ = helper.find_element(frame, "btn.png")

        assert found is True
        assert abs(center[0] - (x + 60)) <= 6
        assert abs(center[1] - (y + 42)) <= 6
