"""AppiumFindElement finds elements through the Appium session's own lookups, on a fake Appium server."""
import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.elementsources.appium_find_element import AppiumFindElement
from tests.units.fake_appium import AppiumServer, appium_driver

ANDROID = {"platformName": "Android", "appium:automationName": "UiAutomator2", "appium:appPackage": "in.app"}


def source_on(monkeypatch, *nodes):
    page = "<hierarchy>" + "".join(nodes) + "</hierarchy>"
    driver = appium_driver(monkeypatch, AppiumServer(page=page), ANDROID)
    driver.start_session()
    return AppiumFindElement(driver)


def button(name, bounds="[0,0][100,50]", displayed="true"):
    return f'<b class="android.widget.Button" name="{name}" bounds="{bounds}" displayed="{displayed}"/>'


@pytest.mark.parametrize(("node", "visible"), [
    pytest.param(button("Option 1"), True, id="on the screen"),
    pytest.param(button("Option 1", bounds="[0,5000][100,5050]"), False, id="scrolled off the screen"),
    pytest.param(button("Option 1", displayed="false"), False, id="hidden"),
    pytest.param(button("Option 2"), False, id="not in the tree"),
])
def test_a_visibility_check(monkeypatch, node, visible):
    assert source_on(monkeypatch, node).check_elements(["Option 1"], visible=True) == [visible]


def test_a_visible_element_passes_the_visibility_assertion(monkeypatch):
    result, timestamp = source_on(monkeypatch, button("Option 1")).assert_elements_visible(["Option 1"], timeout=1)
    assert (result, bool(timestamp)) == (True, True)


def test_an_index_picks_that_match_of_a_text(monkeypatch):
    source = source_on(monkeypatch, button("NEXT"), button("NEXT", bounds="[0,60][100,110]"))
    assert source.locate("NEXT", index=1).rect == {"x": 0, "y": 60, "width": 100, "height": 50}


def test_an_index_past_the_last_match_is_not_found(monkeypatch):
    source = source_on(monkeypatch, button("A"), button("B"))
    with pytest.raises(OpticsError) as caught:
        source.locate("android.widget.Button", index=5)
    assert caught.value.code == Code.E0201
