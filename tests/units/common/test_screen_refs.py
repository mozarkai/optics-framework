import pytest

from optics_framework.common.agent_core import Action, Fail, prepare
from optics_framework.common.screen_snapshot import RefTable


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


def _calculator(display: str) -> list[dict]:
    return [_native(display, "static_text", []), _native("7", "button", ["tap"], rid="Seven", y=20),
            _native("", "button", ["tap"], rid="Equals", y=40)]


def _note(text: str, rid: str = "") -> list[dict]:
    return [_native("File menu", "menu_item", ["tap"]), _native(text, "text_area", ["tap", "input"], rid=rid, y=20)]


def _editor(title: str) -> list[dict]:
    return [_native(title, "window", ["tap"]), _native("File menu", "menu_item", ["tap"], y=20),
            _native("Edit menu", "menu_item", ["tap"], y=40)]


@pytest.mark.parametrize(("before", "after"), [
    (_calculator("0"), _calculator("17×23")),
    (_note(""), _note("Hello optics")),
    (_note("", rid="First Text View"), _note("Hello optics", rid="First Text View")),
    (_editor("Untitled 1 - Mousepad"), _editor("*Untitled 1 - Mousepad")),
], ids=["own_id_under_a_changing_display", "field_gets_text", "field_with_id_gets_text", "window_title_changes"])
def test_controls_keep_their_refs_when_text_on_the_screen_changes(before, after):
    refs = RefTable()
    first = [row.ref for row in refs.snapshot(before).rows]

    assert [row.ref for row in refs.snapshot(after).rows][1:] == first[1:]


def test_ocr_rows_keep_refs_and_text_when_a_camera_frame_moves_and_reads_punctuation_differently():
    refs = RefTable()
    first = refs.snapshot(_read("8.57", "Picture in Picture", "AirDrop and more:"))

    moved = refs.snapshot(_read("8:57", "picture in Picture", "AirDrop and more.", shift=3))

    assert [(r.ref, r.label) for r in moved.rows] == [(r.ref, r.label) for r in first.rows]


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
    action = Action("type", target=label.ref, text="wallpaper")
    with pytest.raises(Fail, match="not a text field"):
        prepare(action, refs, {"enter_text": enter_text})


def test_a_label_names_the_open_menu_item_before_list_cells_with_the_same_text():
    refs = RefTable()
    refs.snapshot([_native("Playlist", "table_cell", ["tap"], y=0), _native("Playlist", "table_cell", ["tap"], y=20),
                   _native("Playlist", "menu_item", ["tap"], y=40)])

    assert refs.resolve("Playlist").cls == "menu_item"
