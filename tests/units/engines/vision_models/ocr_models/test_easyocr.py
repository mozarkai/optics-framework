"""Unit tests for EasyOCRHelper.find_element -- highest-confidence match selection.

A low-confidence misread elsewhere on screen can still contain the target text as a
substring; find_element must not let that noise outrank a genuine, high-confidence match.
"""
from unittest.mock import patch

import numpy as np
import pytest

from optics_framework.engines.vision_models.ocr_models.easyocr import EasyOCRHelper


def _bbox(x1, y1, x2, y2):
    """Quad in EasyOCR's (top-left, top-right, bottom-right, bottom-left) format."""
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


@pytest.fixture
def helper():
    with patch("optics_framework.engines.vision_models.ocr_models.easyocr.easyocr.Reader"):
        return EasyOCRHelper({"language": "en"})


@pytest.fixture
def frame():
    return np.zeros((100, 100, 3), dtype=np.uint8)


class TestFindElementConfidence:
    def test_prefers_highest_confidence_match_over_earlier_low_confidence_substring(self, helper, frame):
        """A low-confidence noise match listed first must not beat a later, real match."""
        helper.reader.readtext.return_value = [
            (_bbox(0, 0, 50, 50), 'garbled noise containing "TARGET" by chance', 0.05),
            (_bbox(200, 200, 300, 300), "TARGET", 0.9),
        ]
        result = helper.find_element(frame, "TARGET", index=0)
        assert result is not None
        _found, (center_x, center_y), _bbox_out = result
        assert (center_x, center_y) == (250, 250)

    def test_filters_out_low_confidence_noise_below_threshold(self, helper, frame):
        """No above-threshold candidate -- no match."""
        helper.reader.readtext.return_value = [
            (_bbox(0, 0, 50, 50), 'garbled noise containing "TARGET" by chance', 0.05),
        ]
        assert helper.find_element(frame, "TARGET", index=0) is None

    def test_returns_none_when_text_not_present(self, helper, frame):
        helper.reader.readtext.return_value = [
            (_bbox(0, 0, 50, 50), "UNRELATED", 0.95),
        ]
        assert helper.find_element(frame, "TARGET", index=0) is None

    def test_index_selects_among_matches_sorted_by_confidence_descending(self, helper, frame):
        """index=0 is the highest-confidence match, index=1 the next -- not detection order."""
        helper.reader.readtext.return_value = [
            (_bbox(0, 0, 50, 50), "TARGET", 0.5),      # listed first, lower confidence
            (_bbox(200, 200, 300, 300), "TARGET", 0.9),  # listed second, higher confidence
        ]
        first = helper.find_element(frame, "TARGET", index=0)
        second = helper.find_element(frame, "TARGET", index=1)
        assert first[1] == (250, 250)  # the 0.9-confidence match
        assert second[1] == (25, 25)   # the 0.5-confidence match
