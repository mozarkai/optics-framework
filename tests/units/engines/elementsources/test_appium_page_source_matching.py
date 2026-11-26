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
IOS = {"platformName": "iOS", "appium:automationName": "XCUITest", "appium:bundleId": "in.app"}
IOS_PAGE = "".join([
    "<AppiumAUT>",
    *(f'<XCUIElementTypeButton type="XCUIElementTypeButton" name="{name}" label="{label}" x="0" y="{y}" width="200" height="50"/>'
      for name, label, y in [("buy_99", "Buy 99 seats", 100), ("buy_101", "Buy 101 seats", 0)]),
    "</AppiumAUT>",
])


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setattr(appium_page_source, "time", FakeTime())

    def on_screen(strict=False, page=PAGE, capabilities=ANDROID):
        driver = appium_driver(monkeypatch, AppiumServer(page=page), capabilities, strict_element_match=strict)
        driver.start_session()
        return AppiumPageSource(driver=driver)
    return on_screen


@pytest.mark.parametrize("strict", [False, True])
def test_a_near_miss_is_found_only_when_matching_is_loose(source, strict):
    locate = source(strict=strict).locate
    if strict:
        with pytest.raises(RuntimeError):
            locate("Buy 102 devices")
    else:
        assert locate("Buy 102 devices").rect == BUY_101


@pytest.mark.parametrize("strict", [False, True])
def test_a_near_miss_passes_a_presence_check_only_when_matching_is_loose(source, strict):
    check = source(strict=strict).assert_elements
    if strict:
        with pytest.raises(TimeoutError):
            check(["Buy 102 devices"], timeout=0.05, rule="any")
    else:
        assert check(["Buy 102 devices"], timeout=0.05, rule="any")[0] is True


@pytest.mark.parametrize("xpath", [
    pytest.param('//*[@text="Buy 101"]', id="in part"),
    pytest.param("//*[@text='Buy 101']", id="in part, single quotes"),
    pytest.param('//*[@resource-id="com.app:id/buy_1O1"]', id="a close id"),
])
@pytest.mark.parametrize("strict", [False, True])
def test_a_loose_xpath_is_found_only_when_matching_is_loose(source, xpath, strict):
    locate = source(strict=strict).locate
    if strict:
        with pytest.raises(OpticsError):
            locate(xpath)
    else:
        assert locate(xpath).rect == BUY_101


def test_an_ios_xpath_is_found_in_part_by_its_label(source):
    assert source(page=IOS_PAGE, capabilities=IOS).locate('//*[@label="101"]').rect == BUY_101


def test_an_index_picks_that_match_of_a_repeated_name(source):
    assert source().locate("Add", index=1).rect == {"x": 0, "y": 200, "width": 100, "height": 50}


def test_one_look_answers_each_element(source):
    assert source().check_elements(["Buy 101 devices", "Checkout"]) == [True, False]
