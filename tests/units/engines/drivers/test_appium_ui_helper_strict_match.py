import pytest
from lxml import etree

from optics_framework.engines.drivers.appium_UI_helper import UIHelper


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

    def test_exact_match_unaffected_by_strict(self):
        helper = _helper(ANDROID_TREE)

        result = helper.get_locator_and_strategy("Buy 101 devices", strict=True)

        assert result is not None
        assert result["locator"] == "Buy 101 devices"


class TestGetLocatorAndStrategyUsingIndex:
    def test_fuzzy_candidate_rejected_when_strict(self):
        helper = _helper(ANDROID_TREE)

        with pytest.raises(IndexError):
            helper.get_locator_and_strategy_using_index("Buy 102 devices", 0, strict=True)


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
