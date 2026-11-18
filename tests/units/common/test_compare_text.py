"""compare_text's strict mode -- regression cover for the fuzzy false-positive that let
an "element exists" check succeed for a value that only vaguely resembled what was on
screen (e.g. "Buy 102 devices" matching an actual "Buy 101 devices"), so a Condition's
ELSE branch could never be reached.
"""
import pytest

from optics_framework.common.utils import compare_text

pytestmark = pytest.mark.white_box


class TestNonStrictMatching:


    def test_substring_match(self):
        assert compare_text("Buy 101 devices", "devices") is True

    def test_fuzzy_match_above_threshold(self):
        assert compare_text("Buy 101 devices", "Buy 102 devices") is True

    def test_empty_strings_never_match(self):
        assert compare_text("", "Buy 101 devices") is False
        assert compare_text("Buy 101 devices", "") is False
