import pytest
from lxml import etree

from optics_framework.api.verifier import Verifier
from optics_framework.engines.drivers.appium_UI_helper import UIHelper

pytestmark = pytest.mark.white_box

ANDROID_SCREEN = (
    '<hierarchy rotation="0">'
    '<android.widget.FrameLayout class="android.widget.FrameLayout" package="com.example.shop" '
    'enabled="true" bounds="[0,0][1080,2400]">'
    '<android.view.ViewGroup class="android.view.ViewGroup" resource-id="com.example.shop:id/toolbar" '
    'enabled="true" bounds="[0,0][1080,160]">'
    '<android.widget.ImageButton class="android.widget.ImageButton" content-desc="Navigate up" '
    'clickable="true" enabled="true" bounds="[0,20][140,140]"/>'
    '<android.widget.TextView class="android.widget.TextView" text="Cart (2)" enabled="true" '
    'bounds="[160,40][600,120]"/>'
    "</android.view.ViewGroup>"
    '<androidx.recyclerview.widget.RecyclerView class="androidx.recyclerview.widget.RecyclerView" '
    'resource-id="com.example.shop:id/items" scrollable="true" enabled="true" bounds="[0,160][1080,1800]">'
    '<android.widget.LinearLayout class="android.widget.LinearLayout" enabled="true" bounds="[0,160][1080,400]">'
    '<android.widget.TextView class="android.widget.TextView" text="Blue mug" enabled="true" '
    'bounds="[40,200][700,260]"/>'
    '<android.widget.ImageButton class="android.widget.ImageButton" content-desc="Add" '
    'resource-id="com.example.shop:id/add" clickable="true" enabled="true" bounds="[900,200][1040,340]"/>'
    "</android.widget.LinearLayout>"
    '<android.widget.LinearLayout class="android.widget.LinearLayout" enabled="true" bounds="[0,400][1080,640]">'
    '<android.widget.TextView class="android.widget.TextView" text="Say &quot;hi&quot; card" enabled="true" '
    'bounds="[40,440][700,500]"/>'
    '<android.widget.ImageButton class="android.widget.ImageButton" content-desc="Add" '
    'resource-id="com.example.shop:id/add" clickable="true" enabled="true" bounds="[900,440][1040,580]"/>'
    "</android.widget.LinearLayout>"
    "</androidx.recyclerview.widget.RecyclerView>"
    '<android.widget.EditText class="android.widget.EditText" text="" resource-id="com.example.shop:id/coupon" '
    'clickable="true" editable="true" enabled="true" bounds="[40,1900][800,2000]"/>'
    '<android.widget.Button class="android.widget.Button" text="Checkout" clickable="true" enabled="true" '
    'bounds="[40,2100][1040,2260]"/>'
    "</android.widget.FrameLayout>"
    "</hierarchy>"
)

IOS_SCREEN = (
    '<AppiumAUT>'
    '<XCUIElementTypeApplication type="XCUIElementTypeApplication" name="Shop" label="Shop" '
    'enabled="true" visible="true" x="0" y="0" width="390" height="844">'
    '<XCUIElementTypeWindow type="XCUIElementTypeWindow" enabled="true" visible="true" '
    'x="0" y="0" width="390" height="844">'
    '<XCUIElementTypeNavigationBar type="XCUIElementTypeNavigationBar" name="Cart" '
    'enabled="true" visible="true" x="0" y="47" width="390" height="44">'
    '<XCUIElementTypeButton type="XCUIElementTypeButton" name="Back" label="Back" '
    'enabled="true" visible="true" x="0" y="47" width="80" height="44"/>'
    "</XCUIElementTypeNavigationBar>"
    '<XCUIElementTypeTable type="XCUIElementTypeTable" enabled="true" visible="true" '
    'x="0" y="91" width="390" height="600">'
    '<XCUIElementTypeCell type="XCUIElementTypeCell" enabled="true" visible="true" '
    'x="0" y="91" width="390" height="60">'
    '<XCUIElementTypeStaticText type="XCUIElementTypeStaticText" name="Blue mug" label="Blue mug" '
    'enabled="true" visible="true" x="16" y="100" width="200" height="20"/>'
    '<XCUIElementTypeButton type="XCUIElementTypeButton" name="add" label="Add" '
    'enabled="true" visible="true" x="330" y="100" width="44" height="44"/>'
    "</XCUIElementTypeCell>"
    '<XCUIElementTypeCell type="XCUIElementTypeCell" enabled="true" visible="true" '
    'x="0" y="151" width="390" height="60">'
    '<XCUIElementTypeStaticText type="XCUIElementTypeStaticText" name="Red mug" label="Red mug" '
    'enabled="true" visible="true" x="16" y="160" width="200" height="20"/>'
    '<XCUIElementTypeButton type="XCUIElementTypeButton" name="add" label="Add" '
    'enabled="true" visible="true" x="330" y="160" width="44" height="44"/>'
    "</XCUIElementTypeCell>"
    "</XCUIElementTypeTable>"
    '<XCUIElementTypeTextField type="XCUIElementTypeTextField" name="Coupon" value="" '
    'enabled="true" visible="true" x="16" y="720" width="358" height="40"/>'
    '<XCUIElementTypeStaticText type="XCUIElementTypeStaticText" name="Total" label="Total" '
    'enabled="true" visible="false" x="16" y="770" width="100" height="20"/>'
    "</XCUIElementTypeWindow>"
    "</XCUIElementTypeApplication>"
    "</AppiumAUT>"
)


def _helper(page_source: str) -> UIHelper:
    helper = UIHelper.__new__(UIHelper)
    helper.driver = None
    helper.tree = None
    helper.root = None
    helper.prev_hash = None
    helper.get_page_source = lambda: (page_source, "ts")
    return helper


@pytest.mark.parametrize("page_source", [ANDROID_SCREEN, IOS_SCREEN], ids=["android", "ios"])
def test_every_compact_row_loc_matches_only_its_own_node(page_source):
    helper = _helper(page_source)
    root = etree.fromstring(page_source.encode("utf-8"))
    entries = helper.get_interactive_elements(None, compact=True)
    assert entries
    for entry in entries:
        matches = root.xpath(entry["loc"])
        assert len(matches) == 1, entry["loc"]
        row = {key: value for key, value in entry.items() if key != "loc"}
        assert helper._compact_entry_for(matches[0]) == row


def test_identical_android_icons_get_distinct_locators():
    entries = _helper(ANDROID_SCREEN).get_interactive_elements(None, compact=True)
    adds = [entry["loc"] for entry in entries if entry["text"] == "Add"]
    assert adds == [
        '(//android.widget.ImageButton[@content-desc="Add"])[1]',
        '(//android.widget.ImageButton[@content-desc="Add"])[2]',
    ]


def test_identical_ios_icons_get_distinct_locators():
    entries = _helper(IOS_SCREEN).get_interactive_elements(None, compact=True)
    adds = [entry["loc"] for entry in entries if entry["text"] == "Add"]
    assert adds == [
        '(//XCUIElementTypeButton[@name="add"])[1]',
        '(//XCUIElementTypeButton[@name="add"])[2]',
    ]


def test_unique_attribute_gives_a_plain_locator():
    entries = _helper(ANDROID_SCREEN).get_interactive_elements(None, compact=True)
    by_label = {entry["text"]: entry["loc"] for entry in entries}
    assert by_label["Checkout"] == '//android.widget.Button[@text="Checkout"]'
    assert by_label['Say "hi" card'] == "//android.widget.TextView[@text='Say \"hi\" card']"


def test_compact_shape_keeps_native_loc():
    entries = _helper(IOS_SCREEN).get_interactive_elements(None, compact=True)
    shaped = Verifier._to_compact_shape(entries)
    assert {row["label"]: row["loc"] for row in shaped}["Back"] == '//XCUIElementTypeButton[@name="Back"]'
