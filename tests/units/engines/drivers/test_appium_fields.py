from unittest.mock import MagicMock

import pytest
from lxml import etree

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


def _root(xml: str):
    return etree.fromstring(xml.encode())


def _text(root, text: str):
    return root.xpath("//*[@text=$t or @label=$t]", t=text)[0]


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
