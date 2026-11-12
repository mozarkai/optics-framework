import pytest

from optics_framework.api.verifier import Verifier
from optics_framework.common.screen_snapshot import RefTable, ladder
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


MAC_TEXTEDIT = (
    '<application type="AXApplication" name="TextEdit" id="" enabled="true" visible="true">'
    '<window type="AXWindow" name="Untitled" id="_NS:34" enabled="true" visible="true" clickable="true" '
    'x="141" y="64" width="586" height="488">'
    '<text_area type="AXTextArea" name="" value="" label="" id="First Text View" enabled="true" '
    'visible="true" focused="true" clickable="true" editable="true" scrollable="true" '
    'x="141" y="164" width="586" height="382"/>'
    "</window>"
    "</application>"
)

MAC_CALCULATOR = (
    '<application type="AXApplication" name="Calculator" id="" enabled="true" visible="true">'
    '<window type="AXWindow" name="Calculator" id="main" enabled="true" visible="true" clickable="true" '
    'x="300" y="400" width="230" height="400">'
    '<group type="AXScrollArea" name="Edit field" id="StandardInputView" enabled="true" visible="true" '
    'scrollable="true" x="307" y="535" width="210" height="42">'
    '<static_text type="AXStaticText" name="12" value="12" id="" enabled="true" visible="true" '
    'x="485" y="538" width="32" height="36"/>'
    "</group>"
    "</window>"
    "</application>"
)

WINDOWS_NOTEPAD = (
    '<application type="Application" name="notepad" id="" enabled="true" visible="true">'
    '<window type="Window" name="*Untitled - Notepad" id="" enabled="true" visible="true" '
    'x="78" y="78" width="768" height="519">'
    '<web_area type="Document" name="Text Editor" value="read me" label="" id="15" enabled="true" visible="true" '
    'focused="true" clickable="false" editable="true" scrollable="true" x="86" y="129" width="752" height="437"/>'
    "</window>"
    "</application>"
)


class TestDesktopCompactActions:
    def test_text_area_takes_input_and_keeps_its_stable_id(self):
        entries = _compact(MAC_TEXTEDIT)
        areas = [entry for entry in entries if entry["extra"].get("id") == "First Text View"]
        assert areas
        assert "input" in areas[0]["act"]

    def test_text_inside_a_window_keeps_its_own_row(self):
        assert _by_label(_compact(MAC_CALCULATOR))["12"]["act"] == []

    def test_a_named_field_shows_what_it_holds_and_keeps_its_name_as_the_hint(self):
        [field] = [entry for entry in _compact(WINDOWS_NOTEPAD) if "input" in entry["act"]]
        assert (field["text"], field["extra"]["hint"]) == ("read me", "Text Editor")

    def test_a_named_field_shows_what_it_holds_in_the_full_list_too(self):
        [field] = [e for e in _helper(WINDOWS_NOTEPAD).get_interactive_elements(None) if e["extra"]["tag"] == "web_area"]
        assert (field["text"], field["extra"]["name"]) == ("read me", "Text Editor")

    @pytest.mark.parametrize("shape", [list, Verifier._to_compact_shape], ids=["screen", "keyword"])
    def test_a_desktop_id_is_found_by_its_own_attribute(self, shape):
        snap = RefTable().snapshot(shape(_compact(MAC_TEXTEDIT)))
        [area] = [row for row in snap.all_rows if row.rid == "First Text View"]
        assert ladder(area, snap).act == ['//text_area[@id="First Text View"]', '//*[@id="First Text View"]']


IOS_NOTE = (
    '<XCUIElementTypeApplication name="Notes" label="Notes" x="0" y="0" width="390" height="844">'
    '<XCUIElementTypeStaticText name="Title" label="Title" x="16" y="100" width="200" height="20" '
    'enabled="true" visible="true"/>'
    '<XCUIElementTypeTextView value="Body" x="16" y="130" width="358" height="300" enabled="true" visible="true"/>'
    "</XCUIElementTypeApplication>"
)

ANDROID_FOCUSABLE_LABEL = (
    '<hierarchy rotation="0">'
    '<android.widget.TextView class="android.widget.TextView" text="Row label" focusable="true" '
    'clickable="true" enabled="true" bounds="[10,20][400,80]"/>'
    "</hierarchy>"
)


@pytest.mark.parametrize("page_source, label, is_input", [
    (IOS_NOTE, "Body", True),
    (MAC_TEXTEDIT.replace('value=""', 'value="Draft"', 1), "Draft", True),
    (MAC_TEXTEDIT.replace('value=""', 'value="Locked"', 1).replace('editable="true"', 'editable="false"'), "Locked", False),
    (ANDROID_SCREEN, "Read only", False),
    (ANDROID_FOCUSABLE_LABEL, "Row label", False),
])
def test_the_inputs_filter_and_the_compact_list_agree(page_source, label, is_input):
    inputs = {entry["text"] for entry in _helper(page_source).get_interactive_elements(["inputs"])}
    compact = {entry["text"]: entry for entry in _compact(page_source)}

    assert (label in inputs) is is_input
    assert ("input" in compact[label]["act"]) is is_input


MAC_DIALOG = (
    '<application name="TextEdit">'
    '<window name="Save" x="0" y="0" width="400" height="300" enabled="true" visible="true">'
    '<static_text name="Save changes?" value="Save changes?" x="20" y="20" width="200" height="20" enabled="true" visible="true"/>'
    '<image name="Icon" x="20" y="60" width="64" height="64" enabled="true" visible="true"/>'
    '<button name="Save" x="300" y="250" width="80" height="30" enabled="true" visible="true"/>'
    "</window></application>"
)


@pytest.mark.parametrize("kind, tag", [("buttons", "button"), ("text", "static_text"), ("images", "image")])
def test_each_filter_keeps_its_desktop_role(kind, tag):
    assert tag in {entry["extra"]["tag"] for entry in _helper(MAC_DIALOG).get_interactive_elements([kind])}


MAC_CHAT_LIST = (
    '<application name="‎WhatsApp">'
    '<window name="WhatsApp" x="0" y="0" width="800" height="800" enabled="true" visible="true">'
    '<static_text name="" label="Chats, 2" id="SidebarButton_Chats" x="10" y="40" width="80" height="20" '
    'enabled="true" visible="true" clickable="true"/>'
    '<button name="Records" value="Your message, hello" label="Double tap to open chat, Double tap and hold for more options" '
    'x="100" y="100" width="300" height="60" enabled="true" visible="true" clickable="true"/>'
    '<button name="Daddy" value="Spice or indigo" label="Double tap to open chat, Double tap and hold for more options" '
    'x="100" y="160" width="300" height="60" enabled="true" visible="true" clickable="true"/>'
    "</window></application>"
)


def test_a_desktop_row_shows_its_name_before_its_description():
    texts = {entry["text"] for entry in _compact(MAC_CHAT_LIST)}
    assert {"Records", "Daddy", "Chats, 2"} <= texts


MAC_NOTE_LIST = (
    '<application name="Notes">'
    '<window name="Notes" x="0" y="0" width="800" height="800" enabled="true" visible="true">'
    '<table_cell name="" label="Perform press or select Return to open note." id="ICMNoteListCell" '
    'x="100" y="100" width="300" height="60" enabled="true" visible="true" clickable="true">'
    '<static_text name="Groceries" value="Groceries" x="110" y="105" width="200" height="20" enabled="true" visible="true"/>'
    '<static_text name="10:12 AM" value="10:12 AM" x="110" y="130" width="60" height="20" enabled="true" visible="true"/>'
    "</table_cell></window></application>"
)


def test_a_desktop_row_without_a_name_shows_its_texts_before_its_description():
    texts = {entry["text"] for entry in _compact(MAC_NOTE_LIST)}
    assert "Groceries 10:12 AM" in texts


MAC_PLAYER = (
    '<application name="Spotify">'
    '<window name="Spotify" x="0" y="0" width="800" height="800" enabled="true" visible="true">'
    '<web_area name="Kesariya" x="0" y="0" width="800" height="800" enabled="true" visible="true">'
    '<group name="" label="Player controls" x="0" y="700" width="800" height="100" enabled="true" visible="true">'
    '<button name="" label="Pause" x="380" y="720" width="40" height="40" enabled="true" visible="true" clickable="true"/>'
    "</group>"
    '<group name="" label="Good afternoon" x="0" y="300" width="800" height="300" enabled="true" visible="true">'
    '<group name="" label="Perform press to play" x="0" y="300" width="400" height="60" enabled="true" visible="true" '
    'clickable="true">'
    '<button name="" label="Pause" x="300" y="310" width="40" height="40" enabled="true" visible="true" clickable="true"/>'
    "</group></group>"
    '<button name="" label="Pause" x="100" y="100" width="40" height="40" enabled="true" visible="true" clickable="true"/>'
    "</web_area></window></application>"
)


def test_a_desktop_row_names_the_nearest_named_group_around_it():
    areas = sorted(entry["extra"].get("area", "") for entry in _compact(MAC_PLAYER) if entry["text"] == "Pause")
    assert areas == ["", "Good afternoon", "Player controls"]
