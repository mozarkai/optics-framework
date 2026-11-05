from unittest.mock import MagicMock, patch

from optics_framework.engines.drivers.selenium import SeleniumDriver


def _sent_chrome_options(capabilities):
    driver = SeleniumDriver({"url": "http://grid:4444", "capabilities": capabilities}, event_sdk=MagicMock())
    with patch("optics_framework.engines.drivers.selenium.webdriver.Remote") as remote, \
            patch("optics_framework.engines.drivers.selenium.UIHelper"):
        driver.start_session()
    return remote.call_args.kwargs["options"].to_capabilities()["goog:chromeOptions"]


def test_chrome_does_not_announce_automation():
    sent = _sent_chrome_options({"browserName": "chrome"})
    assert "--disable-blink-features=AutomationControlled" in sent["args"]
    assert sent["excludeSwitches"] == ["enable-automation"]


def test_configured_chrome_options_reach_the_browser():
    sent = _sent_chrome_options({
        "browserName": "chrome",
        "goog:chromeOptions": {
            "args": ["--headless=new"],
            "prefs": {"intl.accept_languages": "fr"},
            "excludeSwitches": ["enable-logging"],
        },
    })
    assert "--headless=new" in sent["args"]
    assert "--disable-blink-features=AutomationControlled" in sent["args"]
    assert sent["prefs"] == {"intl.accept_languages": "fr"}
    assert sent["excludeSwitches"] == ["enable-automation", "enable-logging"]
