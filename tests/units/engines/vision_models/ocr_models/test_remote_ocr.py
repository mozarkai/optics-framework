"""Unit tests for RemoteOCR.find_element -- highest-confidence match selection.

Same reasoning as test_easyocr.py: a low-confidence misread elsewhere on screen can
still contain the target text as a substring and must not outrank a real match.
"""
from unittest.mock import patch

import pytest

from optics_framework.engines.vision_models.ocr_models.remote_ocr import RemoteOCR


def _bbox(x1, y1, x2, y2):
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


@pytest.fixture
def ocr():
    return RemoteOCR({"url": "http://localhost:9999"})


class TestFindElementConfidence:
    def test_prefers_highest_confidence_match_over_earlier_low_confidence_substring(self, ocr):
        """A low-confidence noise match listed first must not beat a later, real match."""
        detections = [
            (_bbox(0, 0, 50, 50), 'garbled noise containing "TARGET" by chance', 0.05),
            (_bbox(200, 200, 300, 300), "TARGET", 0.9),
        ]
        with patch.object(ocr, "detect_text", return_value=("", detections)):
            result = ocr.find_element("unused", "TARGET", index=0)
        assert result is not None
        _found, (center_x, center_y), _bbox_out = result
        assert (center_x, center_y) == (250, 250)

    def test_filters_out_low_confidence_noise_below_threshold(self, ocr):
        """No above-threshold candidate -- no match."""
        detections = [
            (_bbox(0, 0, 50, 50), 'garbled noise containing "TARGET" by chance', 0.05),
        ]
        with patch.object(ocr, "detect_text", return_value=("", detections)):
            assert ocr.find_element("unused", "TARGET", index=0) is None

    def test_returns_none_when_text_not_present(self, ocr):
        detections = [(_bbox(0, 0, 50, 50), "UNRELATED", 0.95)]
        with patch.object(ocr, "detect_text", return_value=("", detections)):
            assert ocr.find_element("unused", "TARGET", index=0) is None

    def test_index_selects_among_matches_sorted_by_confidence_descending(self, ocr):
        """index=0 is the highest-confidence match, index=1 the next -- not detection order."""
        detections = [
            (_bbox(0, 0, 50, 50), "TARGET", 0.5),        # listed first, lower confidence
            (_bbox(200, 200, 300, 300), "TARGET", 0.9),  # listed second, higher confidence
        ]
        with patch.object(ocr, "detect_text", return_value=("", detections)):
            first = ocr.find_element("unused", "TARGET", index=0)
            second = ocr.find_element("unused", "TARGET", index=1)
        assert first[1] == (250, 250)  # the 0.9-confidence match
        assert second[1] == (25, 25)   # the 0.5-confidence match
