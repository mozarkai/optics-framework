from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.elementsources.appium_find_element import (
    AppiumFindElement,
)
from optics_framework.engines.elementsources.appium_page_source import AppiumPageSource
from optics_framework.engines.elementsources.appium_web import AppiumWeb


def _driver(platform: str | None) -> MagicMock:
    driver = MagicMock()
    driver.native_ui_platform.return_value = platform
    return driver


@pytest.mark.parametrize("source_cls", [AppiumFindElement, AppiumPageSource])
def test_native_sources_decline_dom_only_xpath_on_phones(source_cls):
    source = source_cls(driver=_driver("android"))

    assert source.can_locate('//input[@name="email"]') is False
    assert source.can_locate('//*[@id="x"]') is True
    assert source.can_locate("//android.widget.Button") is True


@pytest.mark.parametrize("source_cls", [AppiumFindElement, AppiumPageSource])
def test_native_sources_keep_dom_xpath_off_phones(source_cls):
    assert source_cls(driver=_driver(None)).can_locate("//input") is True


def test_appium_web_accepts_web_locators_on_android_native_sessions_only():
    assert AppiumWeb(driver=_driver("android")).can_locate("css=#q") is True
    assert AppiumWeb(driver=_driver("android")).can_locate("//input") is True
    for platform in (None, "ios"):
        assert AppiumWeb(driver=_driver(platform)).can_locate("css=#q") is False
        assert AppiumWeb(driver=_driver(platform)).can_locate("//input") is False


def test_appium_web_leaves_a_browser_session_in_its_web_context():
    driver = _driver(None)

    assert AppiumWeb(driver=driver).locate("css=#q") is None
    driver.web_context.assert_not_called()


@pytest.mark.parametrize("source_cls", [AppiumFindElement, AppiumPageSource, AppiumWeb])
def test_can_locate_without_a_driver_does_not_raise(source_cls):
    source_cls(driver=None).can_locate("//input")
    source_cls(driver=None).can_locate("css=#q")


def test_check_once_reraises_dead_session():
    driver = _driver("android")

    @contextmanager
    def web_context():
        raise OpticsError(Code.E0106, message="gone")
        yield

    driver.web_context = web_context
    source = AppiumWeb(driver=driver)

    with pytest.raises(OpticsError) as excinfo:
        source._check_once([("css selector", "#q")], AppiumWeb._is_present)

    assert excinfo.value.code == Code.E0106


def test_appium_web_check_elements_answers_per_element_in_one_web_context():
    driver = _driver("android")
    page = MagicMock()
    page.find_elements.side_effect = lambda by, value: [MagicMock()] if value == "#q" else []
    entries = []

    @contextmanager
    def web_context():
        entries.append(1)
        yield page

    driver.web_context = web_context

    assert AppiumWeb(driver=driver).check_elements(["css=#q", "css=#missing"]) == [True, False]
    assert len(entries) == 1
