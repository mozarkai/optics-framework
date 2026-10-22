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
