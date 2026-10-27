from unittest.mock import MagicMock

import pytest

from optics_framework.common import utils
from optics_framework.engines.elementsources.appium_page_source import AppiumPageSource

pytestmark = pytest.mark.white_box


def _source(**located) -> AppiumPageSource:
    driver = MagicMock()
    driver.event_sdk.config_handler.config.strict_element_match = False
    driver.ui_helper.get_locator_and_strategy_using_index.return_value = {"strategy": "text", "locator": "Add", **located}
    return AppiumPageSource(driver=driver)


def test_warns_when_a_text_matches_several_elements(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(utils, "execution_logger", logger)

    _source(count=3, occurrence=1).find_xpath_from_text_index("Add", 0)

    assert "matches 3 elements" in logger.warning.call_args.args[0]


def test_index_resolves_to_the_chosen_node_with_the_same_text():
    source = _source(count=2, occurrence=2)

    source.find_xpath_from_text_index("Add", 1)

    source.driver.ui_helper.nth_view_locator.assert_called_once_with("text", "Add", 2)
    source.driver.ui_helper.get_view_locator.assert_not_called()
