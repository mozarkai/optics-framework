import pytest
from lxml import etree

from optics_framework.api.verifier import Verifier
from optics_framework.engines.elementsources.playwright_page_source import (
    PlaywrightPageSource,
)

pytestmark = pytest.mark.white_box

BOUNDS = {"x1": 1, "y1": 2, "x2": 3, "y2": 4}


def _source(html: str) -> PlaywrightPageSource:
    source = PlaywrightPageSource.__new__(PlaywrightPageSource)
    source.tree = etree.HTML(html)
    source._extract_bounds = lambda node, page: dict(BOUNDS)
    return source


def _compact(html: str) -> list[dict]:
    return _source(html)._extract_compact_web_interactives(None)


def _by_label(entries: list[dict]) -> dict[str, dict]:
    return {entry["text"]: entry for entry in entries}


class TestWebCompactActions:
    def test_button_folds_descendant_text(self):
        entries = _by_label(_compact('<button id="go"><span>Go</span></button>'))
        assert entries["Go"]["act"] == ["tap"]
        assert entries["Go"]["extra"] == {"class": "button", "resource-id": "go"}

    def test_button_folds_direct_and_nested_text_in_order(self):
        assert _compact("<button>Save <b>Changes</b> now</button>")[0]["text"] == (
            "Save Changes now"
        )

    def test_inline_markup_without_tail_is_folded(self):
        assert _compact('<a href="#">Read <b>more</b></a>')[0]["text"] == "Read more"

    def test_attribute_label_wins_without_direct_text(self):
        entries = _by_label(_compact('<button aria-label="Menu"><span>Icon</span></button>'))
        assert entries["Menu"]["act"] == ["tap"]

    def test_text_input_is_tappable_and_typable(self):
        entry = _compact('<input type="text" placeholder="Search">')[0]
        assert entry["act"] == ["tap", "input"]
        assert entry["text"] == "Search"

    def test_checkbox_toggles(self):
        entry = _compact('<input type="checkbox" id="cb">')[0]
        assert entry["act"] == ["tap", "toggle"]

    def test_switch_role_toggles(self):
        entry = _compact('<div role="switch" aria-label="Wifi"></div>')[0]
        assert entry["act"] == ["tap", "toggle"]

    def test_contenteditable_is_typable(self):
        entry = _compact('<div contenteditable="true" aria-label="Note"></div>')[0]
        assert entry["act"] == ["tap", "input"]

    def test_select_folds_its_options(self):
        entry = _compact("<select><option>One</option><option>Two</option></select>")[0]
        assert entry["act"] == ["tap"]
        assert entry["text"] == "One Two"

    def test_link_with_image_alt_folds_the_alt(self):
        entries = _by_label(_compact('<a href="/home"><img alt="Home"></a>'))
        assert entries["Home"]["act"] == ["tap"]


class TestWebCompactTextAndDrops:
    def test_standalone_text_is_read_only(self):
        assert _compact("<div><span>Hello</span></div>") == [
            {"text": "Hello", "bounds": dict(BOUNDS), "act": [], "extra": {"class": "span"}}
        ]

    def test_container_without_label_is_dropped_around_its_button(self):
        assert [entry["text"] for entry in _compact("<div><button>Go</button></div>")] == ["Go"]

    def test_hidden_nodes_are_dropped(self):
        assert _compact("<div hidden>Secret</div>") == []
        assert _compact('<span aria-hidden="true">Secret</span>') == []
        assert _compact('<input type="hidden" value="x">') == []

    def test_disabled_control_is_read_only_text(self):
        assert _by_label(_compact("<button disabled>Nope</button>"))["Nope"]["act"] == []

    def test_aria_disabled_widget_is_read_only_text(self):
        entries = _by_label(
            _compact('<div role="switch" aria-disabled="true" aria-label="Wifi"></div>')
        )
        assert entries["Wifi"]["act"] == []

    def test_aria_disabled_false_keeps_actions(self):
        entries = _by_label(
            _compact('<div role="switch" aria-disabled="false" aria-label="Wifi"></div>')
        )
        assert entries["Wifi"]["act"] == ["tap", "toggle"]

    def test_script_and_style_text_is_not_reported(self):
        assert _compact("<script>var secret = 1;</script>") == []
        assert _compact("<style>.a{color:red}</style>") == []


class TestWebCompactProjectsToMCPShape:
    def test_verifier_projects_web_records_to_compact_shape(self):
        entries = _source(
            '<button id="go"><span>Go</span></button><span>Hello</span>'
        )._extract_compact_web_interactives(None)

        assert Verifier._to_compact_shape(entries) == [
            {"i": 0, "label": "Go", "cls": "button", "bounds": [1, 2, 3, 4],
             "act": ["tap"], "rid": "go"},
            {"i": 1, "label": "Hello", "cls": "span", "bounds": [1, 2, 3, 4], "act": []},
        ]

    def test_legacy_payload_without_actions_passes_through(self):
        full = [{"text": "OK", "bounds": dict(BOUNDS), "xpath": "//x", "extra": {}}]
        assert Verifier._to_compact_shape(full) == full
