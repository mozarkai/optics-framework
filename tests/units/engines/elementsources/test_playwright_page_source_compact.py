from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from lxml import etree

from optics_framework.api.verifier import Verifier
from optics_framework.engines.elementsources.playwright_page_source import BOUNDS_JS
from optics_framework.engines.elementsources.playwright_page_source import (
    PlaywrightPageSource,
)

pytestmark = pytest.mark.white_box

RECT = {"x": 1, "y": 2, "width": 2, "height": 2}
BOUNDS = {"x1": 1, "y1": 2, "x2": 3, "y2": 4}


def _source(html: str) -> PlaywrightPageSource:
    source = PlaywrightPageSource.__new__(PlaywrightPageSource)
    source.tree = etree.HTML(html)
    source.driver = SimpleNamespace(run=lambda coro, **_: coro, page=_page())
    return source


def _page() -> MagicMock:
    # Stub the browser boundary, not the source's own bounds helper, so these tests
    # exercise the real extraction path and a helper that disappears fails here.
    page = MagicMock()
    page.evaluate.side_effect = lambda script, xpaths: [
        dict(RECT) if script == BOUNDS_JS else None for _ in xpaths
    ]
    return page


def _compact(html: str) -> list[dict]:
    return _source(html)._extract_compact_web_interactives()


def _by_label(entries: list[dict]) -> dict[str, dict]:
    return {entry["text"]: entry for entry in entries}


class TestWebCompactActions:


    def test_inline_markup_without_tail_is_folded(self):
        assert _compact('<a href="#">Read <b>more</b></a>')[0]["text"] == "Read more"

    def test_checkbox_toggles(self):
        entry = _compact('<input type="checkbox" id="cb">')[0]
        assert entry["act"] == ["tap", "toggle"]


class TestWebCompactTextAndDrops:


    def test_hidden_nodes_are_dropped(self):
        assert _compact("<div hidden>Secret</div>") == []
        assert _compact('<span aria-hidden="true">Secret</span>') == []
        assert _compact('<input type="hidden" value="x">') == []


class TestWebCompactProjectsToMCPShape:

    def test_legacy_payload_without_actions_passes_through(self):
        full = [{"text": "OK", "bounds": dict(BOUNDS), "xpath": "//x", "extra": {}}]
        assert Verifier._to_compact_shape(full) == full
