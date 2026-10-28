"""UIHelper's text-locator strict mode -- the same fuzzy/partial fallback that
find_xpath already gated behind `strict` for XPath locators, extended to
get_locator_and_strategy/get_locator_and_strategy_using_index.
"""
import pytest
from lxml import etree

from optics_framework.engines.drivers.appium_UI_helper import UIHelper

pytestmark = pytest.mark.white_box

ANDROID_TREE = (
    '<hierarchy rotation="0">'
    '<android.widget.RadioButton text="Buy 101 devices" '
    'resource-id="com.app:id/buy_101" bounds="[0,0][200,50]"/>'
    "</hierarchy>"
)


def _helper(page_source: str) -> UIHelper:
    helper = UIHelper.__new__(UIHelper)
    helper.driver = None
    helper.tree = etree.ElementTree(etree.fromstring(page_source.encode("utf-8")))
    helper.root = helper.tree.getroot()
    helper.get_page_source = lambda: (page_source, "ts")
    return helper


class TestGetLocatorAndStrategy:
    def test_fuzzy_candidate_matched_when_not_strict(self):
        helper = _helper(ANDROID_TREE)

        result = helper.get_locator_and_strategy("Buy 102 devices")

        assert result is not None
        assert result["locator"] == "Buy 101 devices"

    def test_fuzzy_candidate_rejected_when_strict(self):
        helper = _helper(ANDROID_TREE)

        assert helper.get_locator_and_strategy("Buy 102 devices", strict=True) is None

    def test_exact_match_unaffected_by_strict(self):
        helper = _helper(ANDROID_TREE)

        result = helper.get_locator_and_strategy("Buy 101 devices", strict=True)

        assert result is not None
        assert result["locator"] == "Buy 101 devices"


class TestGetLocatorAndStrategyUsingIndex:
    def test_fuzzy_candidate_matched_when_not_strict(self):
        helper = _helper(ANDROID_TREE)

        result = helper.get_locator_and_strategy_using_index("Buy 102 devices", 0)

        assert result["locator"] == "Buy 101 devices"

    def test_fuzzy_candidate_rejected_when_strict(self):
        helper = _helper(ANDROID_TREE)

        with pytest.raises(IndexError):
            helper.get_locator_and_strategy_using_index("Buy 102 devices", 0, strict=True)

    def test_exact_match_unaffected_by_strict(self):
        helper = _helper(ANDROID_TREE)

        result = helper.get_locator_and_strategy_using_index(
            "Buy 101 devices", 0, strict=True
        )

        assert result["locator"] == "Buy 101 devices"


SAME_TEXT_TREE = (
    '<hierarchy rotation="0">'
    '<android.widget.Button text="Add" content-desc="Add" bounds="[0,0][100,50]"/>'
    '<android.widget.Button text="Add" bounds="[0,60][100,110]"/>'
    "</hierarchy>"
)


class TestSeveralMatches:
    def test_a_node_matched_through_two_attributes_counts_once(self):
        result = _helper(SAME_TEXT_TREE).get_locator_and_strategy_using_index("Add", 0)

        assert result["count"] == 2
        assert result["occurrence"] == 1

    def test_index_picks_the_second_node_with_the_same_text(self):
        helper = _helper(SAME_TEXT_TREE)

        result = helper.get_locator_and_strategy_using_index("Add", 1)
        xpath = helper.nth_view_locator(result["strategy"], result["locator"], result["occurrence"])

        assert xpath == '(//*[@text="Add"])[2]'
        assert helper.tree.xpath(xpath)[0].get("bounds") == "[0,60][100,110]"
