"""AppiumPageSource presence checks honour Config.strict_element_match, uniform with the
locate path. Strict mode rejects a value that only fuzzy-matched what is on screen (e.g.
"Buy 102 devices" against an on-screen "Buy 101 devices" -- the near-miss that let a
Condition's "element exists" probe report true and skip its ELSE branch); the default
fuzzy mode accepts it. Both text- and XPath-type presence follow the same flag.
"""
from unittest.mock import MagicMock

import pytest

from optics_framework.engines.elementsources.appium_page_source import AppiumPageSource

pytestmark = pytest.mark.white_box

ANDROID_TREE = (
    '<hierarchy rotation="0">'
    '<android.widget.RadioButton text="Buy 101 devices" '
    'resource-id="com.app:id/buy_101" bounds="[0,0][200,50]"/>'
    "</hierarchy>"
)


def _source(page_source: str = ANDROID_TREE, strict_element_match: bool = False) -> AppiumPageSource:
    driver = MagicMock()
    driver.driver.page_source = page_source
    driver.event_sdk.config_handler.config.strict_element_match = strict_element_match
    return AppiumPageSource(driver=driver)


class TestStrictElementMatchConfig:
    def test_reads_config_flag(self):
        assert _source(strict_element_match=True)._strict_element_match() is True
        assert _source(strict_element_match=False)._strict_element_match() is False

    def test_missing_config_chain_defaults_to_false(self):
        source = AppiumPageSource(driver=object())

        assert source._strict_element_match() is False


class TestAssertElementsTextPresence:
    def test_present_text_is_found(self):
        source = _source()

        source.assert_elements(["Buy 101 devices"], timeout=1, rule="any")

    def test_fuzzy_near_miss_rejected_when_strict(self):
        source = _source(strict_element_match=True)

        with pytest.raises(TimeoutError):
            source.assert_elements(["Buy 102 devices"], timeout=0.05, rule="any")

    def test_fuzzy_near_miss_matches_when_not_strict(self):
        # Default (fuzzy) mode: presence follows the flag, uniform with locate, so a
        # near-miss fuzzy-matches instead of being rejected.
        source = _source(strict_element_match=False)

        source.assert_elements(["Buy 102 devices"], timeout=1, rule="any")


class TestAssertElementsXPathStrictWiring:
    @pytest.mark.parametrize("flag", [True, False])
    def test_assert_forwards_config_strict_flag_to_find_xpath(self, flag):
        source = _source(strict_element_match=flag)
        source.driver.ui_helper.find_xpath.return_value = ("//android.widget.RadioButton", "ts")

        source.assert_elements(["//android.widget.RadioButton"], timeout=1, rule="any")

        source.driver.ui_helper.find_xpath.assert_called_with(
            "//android.widget.RadioButton", strict=flag
        )


class TestLocateStrictWiring:
    def test_locate_by_xpath_passes_config_strict_flag(self):
        source = _source(strict_element_match=True)
        source.driver.ui_helper.find_xpath.return_value = ("//x", "ts")

        source._locate_by_xpath(source.driver, "xpath=//x", "XPath")

        source.driver.ui_helper.find_xpath.assert_called_once_with("xpath=//x", strict=True)

    def test_locate_by_text_passes_config_strict_flag(self):
        source = _source(strict_element_match=True)
        source.driver.ui_helper.get_locator_and_strategy.return_value = None

        with pytest.raises(RuntimeError):
            source._locate_by_text(source.driver, "Buy 101 devices", "Text")

        source.driver.ui_helper.get_locator_and_strategy.assert_called_once_with(
            "Buy 101 devices", strict=True
        )

    def test_locate_by_text_with_index_passes_config_strict_flag(self):
        source = _source(strict_element_match=True)
        source.driver.ui_helper.get_locator_and_strategy_using_index.return_value = None

        source._locate_by_text(source.driver, "Buy 101 devices", "Text", index=0)

        source.driver.ui_helper.get_locator_and_strategy_using_index.assert_called_once_with(
            "Buy 101 devices", 0, None, strict=True
        )
