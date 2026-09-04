"""AppiumPageSource text-presence checks must be exact -- regression cover for a
Condition's "element exists" probe reporting true for a value that only fuzzy-matched
what was actually on screen (e.g. "Buy 102 devices" against an on-screen "Buy 101
devices"), which meant the ELSE branch could never be reached. Mirrors the strict
treatment find_xpath already gives XPath-type presence checks.
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

    def test_fuzzy_near_miss_is_not_reported_as_present(self):
        source = _source()

        with pytest.raises(TimeoutError):
            source.assert_elements(["Buy 102 devices"], timeout=0.05, rule="any")


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
