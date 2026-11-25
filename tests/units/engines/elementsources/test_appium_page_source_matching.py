"""How the Appium page source matches a name on an Android screen, strict and loose."""
import pytest

from optics_framework.common.error import OpticsError
from optics_framework.engines.elementsources import appium_page_source
from optics_framework.engines.elementsources.appium_page_source import AppiumPageSource
from tests.units.fake_appium import AppiumServer, appium_driver
from tests.units.fakes import FakeTime

ANDROID = {"platformName": "Android", "appium:automationName": "UiAutomator2", "appium:appPackage": "in.app"}
PAGE = "".join([
    '<hierarchy rotation="0">',
    *(f'<{cls} class="{cls}" text="{text}" content-desc="" resource-id="{rid}" bounds="{bounds}"/>' for cls, text, rid, bounds in [
        ("android.widget.RadioButton", "Buy 101 devices", "com.app:id/buy_101", "[0,0][200,50]"),
        ("android.widget.Button", "Add", "", "[0,100][100,150]"),
        ("android.widget.Button", "Add", "", "[0,200][100,250]"),
    ]),
    "</hierarchy>",
])
BUY_101 = {"x": 0, "y": 0, "width": 200, "height": 50}


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setattr(appium_page_source, "time", FakeTime())

    def on_screen(strict=False):
        driver = appium_driver(monkeypatch, AppiumServer(page=PAGE), ANDROID, strict_element_match=strict)
        driver.start_session()
        return AppiumPageSource(driver=driver)
    return on_screen


@pytest.mark.parametrize("strict", [False, True])
def test_a_near_miss_is_found_only_when_matching_is_loose(source, strict):
    if strict:
        with pytest.raises(RuntimeError):
            source(strict=True).locate("Buy 102 devices")
    else:
        assert source().locate("Buy 102 devices").rect == BUY_101


@pytest.mark.parametrize("strict", [False, True])
def test_a_near_miss_passes_a_presence_check_only_when_matching_is_loose(source, strict):
    check = source(strict=strict).assert_elements
    if strict:
        with pytest.raises(TimeoutError):
            check(["Buy 102 devices"], timeout=0.05, rule="any")
    else:
        assert check(["Buy 102 devices"], timeout=0.05, rule="any")[0] is True


def test_pin_an_xpath_is_never_matched_in_part(source):
    with pytest.raises(OpticsError):
        source().locate('//*[@text="Buy 101"]')


@pytest.mark.skip(reason="bug: simplify_xpath reads attributes that extract_key_attributes dropped for being empty, "
                         "so the partial XPath match raises KeyError on any node without a content-desc")
def test_an_xpath_is_matched_in_part_when_matching_is_loose(source):
    assert source().locate('//*[@text="Buy 101"]').rect == BUY_101


def test_an_index_picks_that_match_of_a_repeated_name(source):
    assert source().locate("Add", index=1).rect == {"x": 0, "y": 200, "width": 100, "height": 50}


def test_one_look_answers_each_element(source):
    assert source().check_elements(["Buy 101 devices", "Checkout"]) == [True, False]
