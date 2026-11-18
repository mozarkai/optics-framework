import pytest

from optics_framework.common.agent_core import Action, Fail, prepare
from optics_framework.common.screen_snapshot import RefTable, TargetError, render_row

pytestmark = pytest.mark.white_box


def _native(label: str, cls: str, act: list[str], rid: str = "", y: int = 0) -> dict:
    extra = {"class": cls, "resource-id": rid} if rid else {"class": cls}
    entry = {"text": label, "bounds": [0, y, 100, y + 20], "act": act, "extra": extra}
    return entry | {"loc": f'//{cls}[@id="{rid}"]'} if rid and ":" not in rid else entry


def _read(*texts: str, shift: int = 0) -> list[dict]:
    return [
        {"text": text, "bounds": {"x1": shift, "y1": 40 * i + shift, "x2": 100 + shift, "y2": 40 * i + 30 + shift},
         "act": ["tap"], "extra": {"class": "Text"}, "ocr": True, "ocr_index": texts[:i].count(text)}
        for i, text in enumerate(texts)
    ]


def test_an_ocr_row_shows_its_new_text_when_the_text_changes():
    refs = RefTable()
    refs.snapshot(_read("4.2", "+"))

    now = refs.snapshot(_read("42", "="))

    assert [r.label for r in now.rows] == ["42", "="]
    assert len({r.ref for r in now.all_rows} | {r.ref for r in refs.snapshot(_read("+")).all_rows}) == 3


def test_typing_targets_a_camera_read_label_but_not_a_known_label():
    def enter_text(element, text):
        return None

    refs = RefTable()
    search = refs.snapshot(_read("Search", "Wi-Fi")).rows[0]
    calls = prepare(Action("type", target=search.ref, text="wallpaper"), refs, {"enter_text": enter_text}).calls
    assert [(c.keyword, c.params["text"]) for c in calls] == [("enter_text", "wallpaper")]

    label = refs.snapshot([_native("Search", "static_text", [])]).rows[0]
    with pytest.raises(Fail, match="not a text field"):
        prepare(Action("type", target=label.ref, text="wallpaper"), refs, {"enter_text": enter_text})


def test_a_label_names_the_open_menu_item_before_list_cells_with_the_same_text():
    refs = RefTable()
    refs.snapshot([_native("Playlist", "table_cell", ["tap"], y=0), _native("Playlist", "table_cell", ["tap"], y=20),
                   _native("Playlist", "menu_item", ["tap"], y=40)])

    assert refs.resolve("Playlist").cls == "menu_item"


def test_a_query_finds_rows_by_their_role_or_their_ref():
    refs = RefTable()
    screen = [_native("Library", "static_text", [], y=0), _native("Delete", "menu_item", ["tap"], y=20),
              _native("", "text_field", ["tap", "input"], y=40)]
    delete = refs.snapshot(screen).rows[1].ref

    assert [r.cls for r in refs.snapshot(screen, query="menu").rows] == ["menu_item"]
    assert [r.cls for r in refs.snapshot(screen, query="text field").rows] == ["text_field"]
    assert [r.ref for r in refs.snapshot(screen, query=delete).rows] == [delete]


def _in(area: str, entry: dict) -> dict:
    entry["extra"]["area"] = area
    return entry


def test_rows_with_the_same_label_show_where_each_one_is():
    refs = RefTable()
    snap = refs.snapshot([
        _in("Good afternoon", _native("Pause", "button", ["tap"], y=0)),
        _in("Player controls", _native("Pause", "button", ["tap"], y=20)),
        _in("Good afternoon", _native("Play", "button", ["tap"], y=40)),
        _in("Liked Songs", _native("Play Liked Songs", "button", ["tap"], y=60)),
        _in("Liked Songs", _native("Play Liked Songs", "button", ["tap"], y=80)),
    ])

    assert [render_row(r) for r in snap.rows] == [
        'e1 button "Pause" [tap] in "Good afternoon"',
        'e2 button "Pause" [tap] in "Player controls"',
        'e3 button "Play" [tap]',
        'e4 button "Play Liked Songs" [tap]',
        'e5 button "Play Liked Songs" [tap]',
    ]
    with pytest.raises(TargetError, match='e2 "Pause" in "Player controls"'):
        refs.resolve("Pause")
