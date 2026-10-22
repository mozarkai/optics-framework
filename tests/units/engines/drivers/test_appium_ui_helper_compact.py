import pytest

from optics_framework.engines.drivers.appium_UI_helper import UIHelper

pytestmark = pytest.mark.white_box


def _helper(page_source: str) -> UIHelper:
    helper = UIHelper.__new__(UIHelper)
    helper.driver = None
    helper.tree = None
    helper.root = None
    helper.prev_hash = None
    helper.get_page_source = lambda: (page_source, "ts")
    return helper


def _compact(page_source: str) -> list[dict]:
    return _helper(page_source).get_interactive_elements(None, compact=True)


def _by_label(entries: list[dict]) -> dict[str, dict]:
    return {entry["text"]: entry for entry in entries}


IOS_KEYBOARD_AND_PICKER = (
    '<XCUIElementTypeApplication name="App" label="App" x="0" y="0" width="390" height="844">'
    '<XCUIElementTypeKeyboard x="0" y="500" width="390" height="300" enabled="true" visible="true">'
    '<XCUIElementTypeKey name="q" label="q" x="10" y="510" width="30" height="40" '
    'enabled="true" visible="true"/>'
    "</XCUIElementTypeKeyboard>"
    '<XCUIElementTypePickerWheel name="" label="" value="Item 1" x="0" y="300" width="300" '
    'height="200" enabled="true" visible="true"/>'
    '<XCUIElementTypePickerWheel name="" label="" value="Item 2" x="0" y="300" width="300" '
    'height="200" enabled="true" visible="true">'
    '<XCUIElementTypeOther label="row" x="0" y="300" width="300" height="40" enabled="true" '
    'visible="true"/>'
    "</XCUIElementTypePickerWheel>"
    "</XCUIElementTypeApplication>"
)

ANDROID_SCREEN = (
    '<hierarchy rotation="0">'
    '<android.widget.FrameLayout class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">'
    '<android.widget.Button class="android.widget.Button" text="OK" clickable="true" '
    'enabled="true" bounds="[10,20][200,100]"/>'
    '<android.widget.CheckBox class="android.widget.CheckBox" text="Agree" clickable="true" '
    'checkable="true" enabled="true" bounds="[10,120][200,200]"/>'
    '<android.widget.EditText class="android.widget.EditText" text="" clickable="true" '
    'editable="true" enabled="true" bounds="[10,220][900,300]"/>'
    '<android.widget.TextView class="android.widget.TextView" text="Read only" enabled="true" '
    'bounds="[10,320][400,380]"/>'
    '<android.widget.ScrollView class="android.widget.ScrollView" scrollable="true" '
    'enabled="true" bounds="[0,400][1080,2400]"/>'
    "</android.widget.FrameLayout>"
    "</hierarchy>"
)


class TestIOSCompactActions:
    def test_keyboard_key_is_tappable(self):
        entries = _by_label(_compact(IOS_KEYBOARD_AND_PICKER))
        assert entries["q"]["act"] == ["tap"]

    def test_keyboard_container_is_not_reported_as_tappable(self):
        classes = {entry["extra"]["class"] for entry in _compact(IOS_KEYBOARD_AND_PICKER)}
        assert "XCUIElementTypeKeyboard" not in classes

    def test_picker_wheel_is_scrollable(self):
        entries = _by_label(_compact(IOS_KEYBOARD_AND_PICKER))
        assert entries["Item 1"]["act"] == ["scroll"]

    def test_picker_wheel_with_child_keeps_its_own_value(self):
        entries = _by_label(_compact(IOS_KEYBOARD_AND_PICKER))
        assert entries["Item 2"]["act"] == ["scroll"]
        assert entries["Item 2"]["extra"]["class"] == "XCUIElementTypePickerWheel"


class TestAndroidCompactActions:
    def test_button_is_tappable(self):
        assert _by_label(_compact(ANDROID_SCREEN))["OK"]["act"] == ["tap"]

    def test_checkbox_toggles(self):
        assert _by_label(_compact(ANDROID_SCREEN))["Agree"]["act"] == ["tap", "toggle"]

    def test_empty_edit_text_is_actionable(self):
        entries = _compact(ANDROID_SCREEN)
        inputs = [
            entry for entry in entries
            if entry["extra"]["class"] == "android.widget.EditText"
        ]
        assert inputs
        assert inputs[0]["act"] == ["tap", "input"]

    def test_standalone_text_is_read_only(self):
        assert _by_label(_compact(ANDROID_SCREEN))["Read only"]["act"] == []

    def test_scroll_container_is_scrollable(self):
        entries = _compact(ANDROID_SCREEN)
        scroll = [
            entry for entry in entries
            if entry["extra"]["class"] == "android.widget.ScrollView"
        ]
        assert scroll
        assert scroll[0]["act"] == ["scroll"]
