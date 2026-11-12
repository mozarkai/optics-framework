import pytest

from optics_framework.common.screen_snapshot import RefTable, diff, render_diff, render_row, verify

pytestmark = pytest.mark.white_box


def _native(label: str, cls: str, act: list[str], rid: str = "", y: int = 0) -> dict:
    extra = {"class": cls, "resource-id": rid} if rid else {"class": cls}
    entry = {"text": label, "bounds": [0, y, 100, y + 20], "act": act, "extra": extra}
    return entry | {"loc": f'//{cls}[@id="{rid}"]'} if rid and ":" not in rid else entry


def _calculator(display: str) -> list[dict]:
    return [
        _native(display, "static_text", [], y=0),
        _native("7", "button", ["tap"], rid="Seven", y=20),
        _native("", "button", ["tap"], rid="Multiply", y=40),
        _native("", "button", ["tap"], rid="Equals", y=60),
    ]


def test_a_control_with_its_own_id_keeps_its_ref_when_the_text_above_it_changes():
    refs = RefTable()
    before = {row.rid: row.ref for row in refs.snapshot(_calculator("0")).rows}

    after = {row.rid: row.ref for row in refs.snapshot(_calculator("17×23")).rows}

    assert after["Equals"] == before["Equals"]
    assert after["Multiply"] == before["Multiply"]


def test_a_field_keeps_its_ref_when_it_gets_text():
    refs = RefTable()
    window = _native("Untitled", "window", ["tap"], y=0)
    empty = refs.snapshot([window, _native("", "text_area", ["tap", "input"], rid="First Text View", y=20)])

    typed = refs.snapshot([window, _native("Hello optics", "text_area", ["tap", "input"], rid="First Text View", y=20)])

    assert typed.rows[1].ref == empty.rows[1].ref


def test_rows_that_share_an_id_get_their_own_refs():
    refs = RefTable()
    rows = refs.snapshot([
        _native("Wi-Fi", "TextView", ["tap"], rid="android:id/title", y=0),
        _native("Bluetooth", "TextView", ["tap"], rid="android:id/title", y=20),
    ]).rows

    assert rows[0].ref != rows[1].ref


def test_rows_keep_their_refs_when_the_window_title_changes():
    refs = RefTable()

    def editor(title: str) -> list[dict]:
        return [
            _native(title, "window", ["tap"], y=0),
            _native("File menu", "menu_item", ["tap"], y=20),
            _native("Edit menu", "menu_item", ["tap"], y=20),
        ]

    before = [row.ref for row in refs.snapshot(editor("Untitled 1 - Mousepad")).rows]

    after = [row.ref for row in refs.snapshot(editor("*Untitled 1 - Mousepad")).rows]

    assert after[1:] == before[1:]


def test_a_field_without_an_id_keeps_its_ref_when_it_gets_text():
    refs = RefTable()
    menu = _native("File menu", "menu_item", ["tap"], y=0)
    empty = refs.snapshot([menu, _native("", "text_area", ["input", "scroll"], y=20)])

    typed = refs.snapshot([menu, _native("Hello optics", "text_area", ["input", "scroll"], y=20)])

    assert typed.rows[1].ref == empty.rows[1].ref


def test_a_field_row_shows_the_line_breaks_of_its_text():
    row = RefTable().snapshot([_native("Hello optics\nSecond line 42", "text_area", ["input"], y=0)]).rows[0]

    assert '"Hello optics\\nSecond line 42"' in render_row(row)


_VOICE = "Double tap and hold to record, release to send or slide left to cancel"


def test_a_control_named_in_full_is_absent_once_it_goes_although_another_label_says_the_word():
    refs = RefTable()
    typing = refs.snapshot([_native("Hello", "text_area", ["tap", "input"], y=0), _native("Send", "button", ["tap"], y=20)])
    sent = refs.snapshot([_native("Compose message", "text_area", ["tap", "input"], y=0), _native(_VOICE, "button", ["tap"], y=20)])

    [check] = verify(True, typing, sent, absent=["Send"]).checks

    assert check.met


def test_a_word_inside_a_longer_label_is_still_present():
    refs = RefTable()
    shown = refs.snapshot([_native("Records, 3 unread messages", "button", ["tap"], y=0)])

    [check] = verify(True, None, shown, present=["Records"]).checks

    assert check.met


def test_typed_text_is_present_once_sent_inside_a_longer_message():
    refs = RefTable()
    typed = refs.snapshot([_native("optics says hi", "text_area", ["tap", "input"], y=0)])
    sent = refs.snapshot([_native("Compose message", "text_area", ["tap", "input"], y=0),
                          _native("optics says hi, 10:01 AM", "static_text", [], y=20)])

    [check] = verify(True, typed, sent, present=["optics says hi"]).checks

    assert check.met


def test_a_label_names_the_open_menu_item_before_list_cells_with_the_same_text():
    refs = RefTable()
    refs.snapshot([_native("Playlist", "table_cell", ["tap"], y=0), _native("Playlist", "table_cell", ["tap"], y=20),
                   _native("Playlist", "menu_item", ["tap"], y=40)])

    assert refs.resolve("Playlist").cls == "menu_item"


def test_rows_keep_their_refs_when_a_web_app_page_title_changes():
    refs = RefTable()

    def player(track: str) -> list[dict]:
        return [_native(track, "window", ["tap"], y=0), _native(track, "web_area", ["scroll"], y=0),
                _native("Home", "button", ["tap"], y=20), _native("Search", "button", ["tap"], y=40)]

    before = [row.ref for row in refs.snapshot(player("Kesariya")).rows]

    after = [row.ref for row in refs.snapshot(player("Otra Noche")).rows]

    assert after[2:] == before[2:]


def test_a_menu_that_opens_is_listed_before_the_other_rows_that_appear():
    refs = RefTable()
    cells = [_native(f"Playlist {n}", "table_cell", ["tap"], y=20 * n) for n in range(60)]
    before = refs.snapshot(cells[:1])
    after = refs.snapshot([*cells, _native("Delete", "menu_item", ["tap"], y=2000)])

    assert 'menu_item "Delete"' in render_diff(diff(before, after), after)


def test_a_field_named_by_its_placeholder_keeps_its_ref_when_it_gets_text():
    refs = RefTable()

    def chat(typed: str) -> list[dict]:
        return [_native("Sam Lee", "static_text", [], y=0),
                {"text": typed or "Type a message", "bounds": [0, 20, 100, 40], "act": ["input"],
                 "extra": {"class": "text_area", "hint": "Type a message"}, "loc": '//text_area[@name="Type a message"]'}]

    empty = refs.snapshot(chat(""))

    typed = refs.snapshot(chat("hello"))

    assert typed.rows[-1].ref == empty.rows[-1].ref


def test_a_search_field_keeps_its_ref_when_its_results_change_below_it():
    refs = RefTable()
    heading = _native("All References", "static_text", [], y=0)
    empty = refs.snapshot([heading, _native("", "text_field", ["tap", "input"], y=20),
                           _native("Welcome to your library", "static_text", [], y=40)])

    typed = refs.snapshot([heading, _native("prospect", "text_field", ["tap", "input"], y=20),
                           _native("Delete filters", "button", ["tap"], y=40)])

    assert typed.rows[1].ref == empty.rows[1].ref



def test_a_query_finds_rows_by_their_role_or_their_ref():
    refs = RefTable()
    screen = [_native("Library", "static_text", [], y=0), _native("Delete", "menu_item", ["tap"], y=20),
              _native("", "text_field", ["tap", "input"], y=40)]
    delete = refs.snapshot(screen).rows[1].ref

    assert [r.cls for r in refs.snapshot(screen, query="menu").rows] == ["menu_item"]
    assert [r.cls for r in refs.snapshot(screen, query="text field").rows] == ["text_field"]
    assert [r.ref for r in refs.snapshot(screen, query=delete).rows] == [delete]
