
import pytest

from optics_framework.common.utils import compare_text


class TestNonStrictMatching:
    def test_substring_match(self):
        assert compare_text("Buy 101 devices", "devices") is True

    def test_fuzzy_match_above_threshold(self):
        assert compare_text("Buy 101 devices", "Buy 102 devices") is True

    def test_empty_strings_never_match(self):
        assert compare_text("", "Buy 101 devices") is False
        assert compare_text("Buy 101 devices", "") is False


@pytest.mark.parametrize("seen,wanted", [("Trail Runner 2", "Trail Runner 3"), ("Tempo Lite", "Tempo Lit")])
def test_near_miss_matches_by_default_but_not_when_strict(seen, wanted):
    assert compare_text(seen, wanted) and not compare_text(seen, wanted, strict=True)
