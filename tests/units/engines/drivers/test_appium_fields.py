from unittest.mock import MagicMock

import pytest
from lxml import etree

from optics_framework.engines.drivers import appium_fields
from optics_framework.engines.drivers.appium import Appium

pytestmark = pytest.mark.white_box

ANDROID = (
    '<hierarchy rotation="0">'
    '<android.widget.LinearLayout class="android.widget.LinearLayout" bounds="[0,0][1000,400]">'
    '<android.view.View class="android.view.View" bounds="[0,0][1000,200]">'
    '<android.widget.TextView class="android.widget.TextView" text="First Name" bounds="[0,0][300,50]"/>'
    '<android.widget.EditText class="android.widget.EditText" text="" bounds="[0,60][1000,150]"/>'
    "</android.view.View>"
    '<android.view.View class="android.view.View" bounds="[0,200][1000,400]">'
    '<android.widget.TextView class="android.widget.TextView" text="Last Name" bounds="[0,200][300,250]"/>'
    '<android.widget.EditText class="android.widget.EditText" text="" bounds="[0,260][1000,350]"/>'
    "</android.view.View>"
    "</android.widget.LinearLayout>"
    '<android.view.View class="android.view.View" bounds="[0,450][1000,600]">'
    '<android.view.View class="android.view.View" bounds="[0,450][500,500]">'
    '<android.widget.TextView class="android.widget.TextView" text="Enter Mobile Number" bounds="[0,450][500,500]"/>'
    "</android.view.View>"
    '<android.widget.EditText class="android.widget.EditText" hint="Mobile Number" bounds="[0,510][1000,600]"/>'
    "</android.view.View>"
    "</hierarchy>"
)

IOS = (
    '<AppiumAUT><XCUIElementTypeApplication type="XCUIElementTypeApplication" x="0" y="0" width="400" height="800">'
    '<XCUIElementTypeOther type="XCUIElementTypeOther" x="0" y="100" width="400" height="120">'
    '<XCUIElementTypeStaticText type="XCUIElementTypeStaticText" label="Email" x="16" y="100" width="100" height="20"/>'
    '<XCUIElementTypeTextField type="XCUIElementTypeTextField" x="16" y="130" width="368" height="40"/>'
    "</XCUIElementTypeOther>"
    "</XCUIElementTypeApplication></AppiumAUT>"
)


def _root(xml: str):
    return etree.fromstring(xml.encode())


def _text(root, text: str):
    return root.xpath("//*[@text=$t or @label=$t]", t=text)[0]


@pytest.mark.parametrize("caption, field_bounds", [
    ("First Name", "[0,60][1000,150]"),
    ("Last Name", "[0,260][1000,350]"),
])
def test_a_caption_names_the_only_field_in_its_container(caption, field_bounds):
    root = _root(ANDROID)

    assert appium_fields.field_for(root, _text(root, caption)).get("bounds") == field_bounds


def test_a_flutter_caption_beside_its_field():
    root = _root(ANDROID)

    assert appium_fields.field_for(root, _text(root, "Enter Mobile Number")).get("hint") == "Mobile Number"


def test_a_partial_caption_finds_the_field_it_names():
    root = _root(ANDROID)
    caption = etree.SubElement(root, "android.widget.TextView", text="Mobile")

    assert appium_fields.field_for(root, caption).get("hint") == "Mobile Number"


def test_no_field_when_the_container_holds_several():
    root = _root(ANDROID)
    heading = etree.SubElement(root.xpath("//android.widget.LinearLayout")[0], "android.widget.TextView", text="Your name")

    assert appium_fields.field_for(root, heading) is None


def test_ios_caption_and_field():
    root = _root(IOS)

    field = appium_fields.field_for(root, _text(root, "Email"))

    assert field.get("type") == "XCUIElementTypeTextField"


def test_node_at_prefers_the_node_showing_the_text():
    root = _root(ANDROID)

    node = appium_fields.node_at(root, {"x": 0, "y": 0, "width": 300, "height": 50}, "First Name")

    assert node.get("text") == "First Name"


@pytest.mark.parametrize("xml", [ANDROID, IOS])
def test_absolute_xpath_finds_the_same_node(xml):
    root = _root(xml)
    for node in root.iter():
        assert root.getroottree().xpath(appium_fields.absolute_xpath(node))[0] is node


def _appium(page_source: str) -> Appium:
    instance = Appium.__new__(Appium)
    instance.driver = MagicMock()
    instance.driver.capabilities = {"platformName": "Android"}
    instance.driver.page_source = page_source
    instance.capabilities = {}
    instance.event_sdk = MagicMock()
    return instance


def test_enter_text_on_a_caption_types_into_its_field():
    instance = _appium(ANDROID)
    caption = MagicMock(text="Last Name", rect={"x": 0, "y": 200, "width": 300, "height": 50})
    caption.get_attribute.side_effect = {"className": "android.widget.TextView"}.get

    instance.enter_text_element(caption, "Doe")

    xpath = instance.driver.find_element.call_args.args[1]
    assert _root(ANDROID).getroottree().xpath(xpath)[0].get("bounds") == "[0,260][1000,350]"
    instance.driver.find_element.return_value.send_keys.assert_called_once_with("Doe")
    caption.send_keys.assert_not_called()


def test_enter_text_on_a_field_reads_no_page_source():
    instance = _appium(ANDROID)
    field = MagicMock()
    field.get_attribute.side_effect = {"className": "android.widget.EditText"}.get
    type(instance.driver).page_source = property(lambda _: pytest.fail("page source read"))

    instance.clear_text_element(field)

    field.clear.assert_called_once()
