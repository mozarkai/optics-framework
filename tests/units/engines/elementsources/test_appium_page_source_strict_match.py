from unittest.mock import MagicMock

import pytest

from optics_framework.engines.elementsources.appium_page_source import AppiumPageSource


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


class TestAssertElementsTextPresence:
    def test_fuzzy_near_miss_rejected_when_strict(self):
        source = _source(strict_element_match=True)

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


def test_check_elements_answers_each_element_from_one_page_source():
    source = _source()

    assert source.check_elements(["Buy 101 devices", "Checkout"]) == [True, False]
