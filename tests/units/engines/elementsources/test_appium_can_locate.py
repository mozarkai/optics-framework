from unittest.mock import MagicMock


from optics_framework.engines.elementsources.appium_web import AppiumWeb


def _driver(platform: str | None) -> MagicMock:
    driver = MagicMock()
    driver.native_ui_platform.return_value = platform
    return driver


def test_appium_web_leaves_a_browser_session_in_its_web_context():
    driver = _driver(None)

    assert AppiumWeb(driver=driver).locate("css=#q") is None
    assert AppiumWeb(driver=driver).get_interactive_elements() == []
    driver.web_context.assert_not_called()


def test_appium_web_check_elements_answers_per_element_in_one_web_context():
    driver = _driver("android")
    page = driver.web_context.return_value.__enter__.return_value
    page.find_elements.side_effect = lambda by, value: [MagicMock()] if value == "#q" else []

    assert AppiumWeb(driver=driver).check_elements(["css=#q", "css=#missing"]) == [True, False]
    driver.web_context.assert_called_once()
