"""Unit tests for PlaywrightPageSource.get_interactive_elements batched bounds extraction.

Bounds used to be resolved one Playwright locator round trip (count() + bounding_box())
per candidate DOM node -- fine for a mobile page source dump, painfully slow for a web
page with a few hundred nodes. It now issues exactly one page.evaluate() call that
resolves every candidate's bounding box in-browser via document.evaluate() +
getBoundingClientRect(), so the call count stays constant regardless of node count.

These tests assert the call-count fix and pin the output contract (same dict shape,
same `if not bounds: continue` filtering, same filter_config semantics) that the
rewrite must not change.
"""
from unittest.mock import MagicMock

import pytest
from lxml import etree

from optics_framework.engines.elementsources import playwright_page_source as pw

pytestmark = pytest.mark.white_box


def _source(html, monkeypatch):
    """A PlaywrightPageSource wired to a fake page.

    run_async is patched to a pass-through since the fake page's methods return plain
    values (via Mock return_value/side_effect) rather than real coroutines.
    """
    monkeypatch.setattr(pw, "run_async", lambda coro: coro)
    page = MagicMock()
    page.content.return_value = html
    src = pw.PlaywrightPageSource(driver=MagicMock(page=page))
    return src, page


def _grid_html(n):
    items = "".join(f'<button id="item-{i}">Item {i}</button>' for i in range(n))
    return f"<html><body>{items}</body></html>"


def _candidate_count(html):
    """Every node .//* sees, including lxml's implicit <head/> -- ground truth for
    'every candidate got bounds' assertions, independent of that auto-wrapping."""
    tree = etree.HTML(html)
    return len(tree.xpath(".//*")) if tree is not None else 0


def _uniform_rects(width=20, height=20):
    """An evaluate() side_effect giving every candidate xpath the same real rect."""
    def _side_effect(script, xpaths):
        return [{"x": i, "y": i, "width": width, "height": height} for i, _ in enumerate(xpaths)]
    return _side_effect


class TestBatchedEvaluateCallCount:
    @pytest.mark.parametrize("n", [1, 50])
    def test_evaluate_called_once_regardless_of_node_count(self, n, monkeypatch):
        html = _grid_html(n)
        src, page = _source(html, monkeypatch)
        page.evaluate.side_effect = _uniform_rects()

        elements = src.get_interactive_elements(None)

        assert page.evaluate.call_count == 1
        # Every candidate node (including lxml's implicit <head/>) got real bounds from
        # the single evaluate() call, so none were dropped by the batching rewrite.
        assert len(elements) == _candidate_count(html)

    def test_evaluate_not_called_when_there_are_no_candidate_nodes(self, monkeypatch):
        src, page = _source("", monkeypatch)
        page.evaluate.side_effect = _uniform_rects()

        elements = src.get_interactive_elements(None)

        assert elements == []
        assert page.evaluate.call_count == 0


class TestOutputShapeAndFiltering:
    def test_mixed_present_absent_bounds_matches_prior_semantics(self, monkeypatch):
        html = (
            "<html><body>"
            '<button id="btn-a">Alpha</button>'
            '<button id="btn-b">Beta</button>'
            '<button id="btn-c">Gamma</button>'
            "</body></html>"
        )
        src, page = _source(html, monkeypatch)

        def side_effect(script, xpaths):
            # Bounds for btn-a/btn-c, withheld for btn-b -- exercises the same
            # `if not bounds: continue` filtering the old per-node code performed.
            rects = []
            for xp in xpaths:
                if xp and "btn-a" in xp:
                    rects.append({"x": 10, "y": 20, "width": 100, "height": 40})
                elif xp and "btn-c" in xp:
                    rects.append({"x": 200, "y": 20, "width": 100, "height": 40})
                else:
                    rects.append(None)
            return rects

        page.evaluate.side_effect = side_effect
        elements = src.get_interactive_elements(None)

        assert page.evaluate.call_count == 1
        texts = {el["text"] for el in elements}
        assert texts == {"Alpha", "Gamma"}

        alpha = next(el for el in elements if el["text"] == "Alpha")
        assert set(alpha.keys()) == {"text", "bounds", "xpath", "extra"}
        assert alpha["bounds"] == {"x1": 10, "y1": 20, "x2": 110, "y2": 60}
        assert alpha["xpath"]

    def test_filter_config_still_applies_after_bounds_check(self, monkeypatch):
        html = (
            "<html><body>"
            '<button id="btn-a">Alpha</button>'
            '<span id="span-a">Not a button</span>'
            "</body></html>"
        )
        src, page = _source(html, monkeypatch)
        page.evaluate.side_effect = _uniform_rects()

        elements = src.get_interactive_elements(["buttons"])

        assert [el["text"] for el in elements] == ["Alpha"]

    def test_no_filter_config_returns_all_bounded_elements(self, monkeypatch):
        html = _grid_html(5)
        src, page = _source(html, monkeypatch)
        page.evaluate.side_effect = _uniform_rects()

        elements = src.get_interactive_elements(None)

        assert len(elements) == _candidate_count(html)


class TestBatchFailureDegradesGracefully:
    def test_evaluate_exception_returns_no_elements_without_per_node_fallback(self, monkeypatch):
        src, page = _source(_grid_html(3), monkeypatch)
        page.evaluate.side_effect = RuntimeError("page navigated mid-evaluate")

        elements = src.get_interactive_elements(None)

        assert elements == []
        # A whole-batch failure must not fall back to one evaluate() call per node.
        assert page.evaluate.call_count == 1

    def test_mismatched_length_response_is_treated_as_all_absent(self, monkeypatch):
        src, page = _source(_grid_html(3), monkeypatch)
        page.evaluate.return_value = [{"x": 0, "y": 0, "width": 5, "height": 5}]  # too short

        elements = src.get_interactive_elements(None)

        assert elements == []
