import pytest

from optics_framework.engines.drivers.appium_UI_helper import UIHelper


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


class TestAndroidCompactActions:
    def test_standalone_text_is_read_only(self):
        assert _by_label(_compact(ANDROID_SCREEN))["Read only"]["act"] == []


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
