import pytest
from lxml import etree

pytest.importorskip("playwright")

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.drivers.playwright import Playwright
from optics_framework.engines.elementsources import playwright_page_source as pw


class Page:
    """A page that serves ``html`` and answers each script it runs with ``answer(script, xpaths)``."""

    def __init__(self, html, answer):
        self.html, self.answer, self.scripts = html, answer, []

    async def content(self):
        return self.html

    async def evaluate(self, script, xpaths):
        self.scripts.append(script)
        return self.answer(script, xpaths)


def _source(html, answer):
    driver = Playwright({"capabilities": {"settle_timeout_ms": 0}})
    driver.page = Page(html, answer)
    return pw.PlaywrightPageSource(driver=driver), driver.page


def _grid_html(n):
    items = "".join(f'<button id="item-{i}">Item {i}</button>' for i in range(n))
    return f"<html><body>{items}</body></html>"


def _candidate_count(html):
    """Every node .//* sees, including lxml's implicit <head/>."""
    tree = etree.HTML(html)
    return len(tree.xpath(".//*")) if tree is not None else 0


def _uniform_rects(script, xpaths):
    if script != pw.BOUNDS_JS:
        return [None] * len(xpaths)
    return [{"x": i, "y": i, "width": 20, "height": 20} for i, _ in enumerate(xpaths)]


def _page_navigated(script, xpaths):
    raise RuntimeError("page navigated mid-evaluate")


class TestOutputShapeAndFiltering:
    def test_filter_config_still_applies_after_bounds_check(self):
        html = (
            "<html><body>"
            '<button id="btn-a">Alpha</button>'
            '<span id="span-a">Not a button</span>'
            "</body></html>"
        )
        src, _ = _source(html, _uniform_rects)
        elements = src.get_interactive_elements(["buttons"])
        assert [el["text"] for el in elements] == ["Alpha"]

    def test_all_filter_returns_every_bounded_element(self):
        html = _grid_html(5)
        src, _ = _source(html, _uniform_rects)
        elements = src.get_interactive_elements(["all"])

        assert len(elements) == _candidate_count(html)


class TestBatchFailureDegradesGracefully:
    def test_compact_batch_failure_is_unavailable_not_empty(self):
        # The NL agent falls back to page source on "unavailable", so a failed batch
        # must raise rather than return [] and look like a genuinely empty screen.
        src, page = _source(_grid_html(3), _page_navigated)
        with pytest.raises(OpticsError) as excinfo:
            src.get_interactive_elements(None, compact=True)
        assert excinfo.value.code == Code.E0202
        assert len(page.scripts) == 1

    def test_mismatched_length_response_is_treated_as_all_absent(self):
        src, _ = _source(_grid_html(3), lambda script, xpaths: [{"x": 0, "y": 0, "width": 5, "height": 5}])
        elements = src.get_interactive_elements(None)
        assert elements == []
