"""compare_text's strict mode -- regression cover for the fuzzy false-positive that let
an "element exists" check succeed for a value that only vaguely resembled what was on
screen (e.g. "Buy 102 devices" matching an actual "Buy 101 devices"), so a Condition's
ELSE branch could never be reached.
"""
import pytest

from optics_framework.common.utils import compare_text

pytestmark = pytest.mark.white_box


class TestNonStrictMatching:
    def test_exact_match(self):
        assert compare_text("Buy 101 devices", "Buy 101 devices") is True

    def test_case_and_whitespace_insensitive(self):
        assert compare_text("  BUY 101 DEVICES ", "buy 101 devices") is True

    def test_substring_match(self):
        assert compare_text("Buy 101 devices", "devices") is True

    def test_fuzzy_match_above_threshold(self):
        assert compare_text("Buy 101 devices", "Buy 102 devices") is True

    def test_no_match_below_threshold(self):
        assert compare_text("Buy 101 devices", "Ask mom for help") is False

    def test_empty_strings_never_match(self):
        assert compare_text("", "Buy 101 devices") is False
        assert compare_text("Buy 101 devices", "") is False


class TestStrictMatching:
    def test_exact_match_still_succeeds(self):
        assert compare_text("Buy 101 devices", "Buy 101 devices", strict=True) is True

    def test_case_and_whitespace_insensitive(self):
        assert compare_text("  BUY 101 DEVICES ", "buy 101 devices", strict=True) is True

    def test_substring_is_rejected(self):
        assert compare_text("Buy 101 devices", "devices", strict=True) is False

    def test_near_miss_fuzzy_candidate_is_rejected(self):
        assert compare_text("Buy 101 devices", "Buy 102 devices", strict=True) is False
