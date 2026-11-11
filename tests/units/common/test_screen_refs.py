import pytest

from optics_framework.common.screen_snapshot import RefTable, render_row

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
