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
