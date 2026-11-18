
from optics_framework.common.utils import compare_text


class TestNonStrictMatching:
    def test_substring_match(self):
        assert compare_text("Buy 101 devices", "devices") is True

    def test_fuzzy_match_above_threshold(self):
        assert compare_text("Buy 101 devices", "Buy 102 devices") is True

    def test_empty_strings_never_match(self):
        assert compare_text("", "Buy 101 devices") is False
        assert compare_text("Buy 101 devices", "") is False
