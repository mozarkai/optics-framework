"""Unit tests for TemplateMatchingHelper on synthetic frames (no device)."""


import cv2
import numpy as np
import pytest

from optics_framework.common.models import TemplateData
from optics_framework.engines.vision_models.image_models.templatematch import (
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


def blocky(seed: int, h: int, w: int) -> np.ndarray:
    """A flat-shaded pattern that, like UI, stays recognisable when resized."""
    cells = np.random.default_rng(seed).integers(40, 220, (7, 10), dtype=np.uint8)
    return cv2.cvtColor(cv2.resize(cells, (w, h), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)


def resized(image: np.ndarray, scale: float) -> np.ndarray:
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=interpolation)


def blocky_frame(rng: np.random.Generator) -> np.ndarray:
    cells = rng.integers(0, 255, (30, 40), dtype=np.uint8)
    return cv2.cvtColor(cv2.resize(cells, (400, 300), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)


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


class TestOtherScreenSize:


    def test_scale_range_widens_the_sweep(self, rng):
        template = blocky(7, 84, 120)
        frame = frame_with(blocky_frame(rng), resized(template, 0.5), [(100, 150)])

        assert make_helper()._match_template_fast(frame, template, 0.85, None) is None
        assert make_helper(scale_range=[0.45, 1.0])._match_template_fast(frame, template, 0.85, None)

    def test_inverted_scale_range_is_rejected(self):
        with pytest.raises(ValueError, match="scale_range"):
            make_helper(scale_range=[1.2, 0.8])


class TestElementExist:
    def test_offset_applied_to_fast_path_center(self, rng, helper):
        template = noise(rng, 16, 24, 3)
        frame = noise(rng, 120, 150, 3)
        y, x = 40, 60
        frame_with(frame, template, [(y, x)])

        _, center, bbox = helper.element_exist(frame, template, offset=[5, -7])

        assert center == (x + 12 + 5, y + 8 + 7)  # center_x += dx, center_y -= dy
        assert bbox == [(x, y), (x + 24, y + 16)]

    def test_featureless_images_raise_from_sift_fallback(self, helper):
        """No-variance input falls through to SIFT, which raises on the miss."""
        frame = np.full((100, 100, 3), 128, dtype=np.uint8)
        # Template larger than the frame at every swept scale so the fast path
        # routes to SIFT deterministically; cv2.matchTemplate on two identical
        # solid regions returns a perfect-correlation 1.0 on some builds
        # (notably the Linux wheels), short-circuiting the SIFT fallback.
        template = np.full((130, 130, 3), 128, dtype=np.uint8)

        with pytest.raises(RuntimeError):
            helper.element_exist(frame, template)


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
