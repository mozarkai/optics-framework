"""XPath labelling contracts for UIHelper.get_interactive_elements.

Regression cover for mozarkai/optics-framework#455: labelling every node used to
answer "is this XPath unique?" with a fresh full-document ``xpath()`` scan, making
extraction quadratic in tree size (~22 CPU-seconds on a 10k-node iOS hierarchy).
That is enough sustained CPU to blow an external health checker's budget and get a
live ``optics serve`` worker declared dead. The uniqueness index must answer the
same question from one pass -- keeping the XPaths byte-identical while the cost
stays linear.
"""
import time

import pytest
from lxml import etree

from optics_framework.engines.drivers.appium_UI_helper import (
    UIHelper,
    XPathUniquenessIndex,
)

pytestmark = pytest.mark.white_box


def _helper(page_source: str) -> UIHelper:
    """A UIHelper wired to a fixed page source, with no Appium driver behind it."""
    helper = UIHelper.__new__(UIHelper)
    helper.driver = None
    helper.tree = None
    helper.root = None
    helper.prev_hash = None
    helper.get_page_source = lambda: (page_source, "ts")
    return helper


def _ios_tree(cells: int) -> str:
    """An iOS-shaped hierarchy: repeated cells whose labels collide across cells, so
    single-attribute probes are mostly non-unique -- the case that drove the cost."""
    parts = [
        '<XCUIElementTypeApplication type="XCUIElementTypeApplication" name="App" '
        'label="App" x="0" y="0" width="390" height="844">'
    ]
    for i in range(cells):
        parts.append(
            f'<XCUIElementTypeCell type="XCUIElementTypeCell" name="" label="Row" '
            f'x="0" y="{i * 60}" width="390" height="60">'
            f'<XCUIElementTypeStaticText type="XCUIElementTypeStaticText" name="Title" '
            f'label="Title" value="{i}" x="8" y="{i * 60}" width="200" height="20"/>'
            f'<XCUIElementTypeButton type="XCUIElementTypeButton" name="Buy" label="Buy" '
            f'x="300" y="{i * 60}" width="60" height="20"/>'
            f"</XCUIElementTypeCell>"
        )
    parts.append("</XCUIElementTypeApplication>")
    return "".join(parts)


ANDROID_TREE = (
    '<hierarchy rotation="0">'
    '<android.widget.FrameLayout class="android.widget.FrameLayout" text="" '
    'resource-id="com.app:id/root" bounds="[0,0][1080,2400]">'
    '<android.widget.Button class="android.widget.Button" text="OK" '
    'resource-id="com.app:id/ok" content-desc="Confirm" bounds="[10,20][110,80]"/>'
    '<android.widget.TextView class="android.widget.TextView" text="Repeated" '
    'bounds="[10,100][300,140]"/>'
    '<android.widget.TextView class="android.widget.TextView" text="Repeated" '
    'bounds="[10,150][300,190]"/>'
    "</android.widget.FrameLayout>"
    "</hierarchy>"
)


class _FullScanIndex:
    """Stand-in index that answers every probe with a real full-document ``xpath()``.

    Reproduces the pre-fix behaviour -- including its ``matches.index(node)``
    positioning -- so the real index is held to what XPath itself reports rather than
    to hard-coded expected strings.
    """

    def __init__(self, helper: UIHelper, doc_tree):
        self._helper = helper
        self._doc_tree = doc_tree
        self.xpath_cache: dict = {}

    def matches(self, tag, conditions):
        if conditions:
            predicate = " and ".join(
                f"@{a}={self._helper._escape_for_xpath_literal(v)}" for a, v in conditions
            )
            query = f"//{tag}[{predicate}]"
        else:
            query = f"//{tag}"
        try:
            return self._doc_tree.xpath(query)
        except (etree.XPathError, ValueError, TypeError):
            return []

    def position_of(self, matches, node):
        try:
            return matches.index(node)
        except ValueError:
            return 0


def _xpath_by_full_scan(helper: UIHelper, node) -> str:
    return helper.get_xpath(node, _FullScanIndex(helper, node.getroottree()))


class TestXPathEquivalence:
    """Indexed uniqueness must agree with real XPath evaluation, node for node."""

    @pytest.mark.parametrize(
        "page_source",
        [ANDROID_TREE, _ios_tree(3), _ios_tree(25)],
        ids=["android", "ios-small", "ios-repeated-labels"],
    )
    def test_indexed_xpath_matches_full_document_scan(self, page_source):
        helper = _helper(page_source)
        root = etree.fromstring(page_source.encode("utf-8"))
        index = XPathUniquenessIndex(root)

        for node in root.xpath(".//*"):
            assert helper.get_xpath(node, index) == _xpath_by_full_scan(helper, node)

    def test_every_generated_xpath_resolves_to_its_own_node(self):
        """A returned XPath is only useful if it actually selects the node it labels."""
        page_source = _ios_tree(12)
        helper = _helper(page_source)
        root = etree.fromstring(page_source.encode("utf-8"))
        tree = root.getroottree()

        for element in helper.get_interactive_elements(None):
            matched = tree.xpath(element["xpath"])
            assert len(matched) == 1, f"{element['xpath']} matched {len(matched)} nodes"

    def test_repeated_labels_are_positionally_disambiguated(self):
        """Nodes sharing a label fall back to ``(probe)[n]`` -- and n must be the
        node's own 1-based position among the matches, in document order."""
        helper = _helper(ANDROID_TREE)
        root = etree.fromstring(ANDROID_TREE.encode("utf-8"))
        tree = root.getroottree()
        repeated = root.xpath('.//android.widget.TextView[@text="Repeated"]')

        xpaths = [helper.get_xpath(n, XPathUniquenessIndex(root)) for n in repeated]

        assert len(set(xpaths)) == len(repeated), "duplicate XPaths for distinct nodes"
        for node, xpath in zip(repeated, xpaths):
            assert tree.xpath(xpath) == [node]


class TestNamespacedTags:
    """Namespaced tags must reach the hierarchical builder, as they always did.

    lxml reports a namespaced tag in Clark notation (``{uri}local``), which is not
    valid XPath. The pre-fix code discovered that by *evaluating* each probe and
    swallowing the parse error as "not unique"; an index that answers from a dict
    would instead hand back an attribute probe that no XPath engine can parse.
    """

    NS_TREES = {
        "prefixed": (
            '<root xmlns:foo="http://example.com">'
            '<foo:bar id="1" bounds="[0,0][10,10]"/></root>'
        ),
        "default-on-every-node": (
            '<root xmlns="http://ex.com">'
            '<child resource-id="a" bounds="[0,0][5,5]"/>'
            '<child resource-id="a" bounds="[0,6][5,9]"/></root>'
        ),
        "mixed-with-plain": (
            '<root xmlns:n="http://e.com">'
            '<n:x id="1" bounds="[0,0][2,2]"/>'
            '<plain id="1" bounds="[0,3][2,5]"/>'
            '<plain id="2" bounds="[0,6][2,8]"/></root>'
        ),
    }

    @pytest.mark.parametrize("page_source", NS_TREES.values(), ids=NS_TREES.keys())
    def test_namespaced_nodes_match_full_document_scan(self, page_source):
        helper = _helper(page_source)
        root = etree.fromstring(page_source.encode("utf-8"))
        index = XPathUniquenessIndex(root)

        for node in root.xpath(".//*"):
            assert helper.get_xpath(node, index) == _xpath_by_full_scan(helper, node)

    def test_plain_siblings_still_get_attribute_xpaths(self):
        """The guard must skip only the namespaced nodes, not poison the document."""
        page_source = self.NS_TREES["mixed-with-plain"]
        xpaths = [e["xpath"] for e in _helper(page_source).get_interactive_elements(None)]

        assert xpaths == [
            "/root/{http://e.com}x",
            '//plain[@id="1"]',
            '//plain[@id="2"]',
        ]


class TestXPathIndex:
    """The index answers the probes get_xpath issues, in document order."""

    def test_matches_single_attribute_and_pair(self):
        root = etree.fromstring(ANDROID_TREE.encode("utf-8"))
        index = XPathUniquenessIndex(root)

        assert len(index.matches("android.widget.Button", (("text", "OK"),))) == 1
        assert len(index.matches("android.widget.TextView", (("text", "Repeated"),))) == 2
        # A pair narrows to the node carrying both values.
        assert len(
            index.matches(
                "android.widget.Button", (("text", "OK"), ("content-desc", "Confirm"))
            )
        ) == 1
        # A pair no node satisfies matches nothing.
        assert index.matches(
            "android.widget.Button", (("text", "OK"), ("content-desc", "Nope"))
        ) == []

    def test_matches_are_in_document_order(self):
        root = etree.fromstring(ANDROID_TREE.encode("utf-8"))
        expected = root.xpath('.//android.widget.TextView[@text="Repeated"]')

        assert XPathUniquenessIndex(root).matches(
            "android.widget.TextView", (("text", "Repeated"),)
        ) == expected

    def test_tag_only_probe_includes_the_root_element(self):
        """``//tag`` is evaluated from the document root, so the root itself counts."""
        root = etree.fromstring(ANDROID_TREE.encode("utf-8"))

        assert XPathUniquenessIndex(root).matches("hierarchy", ()) == [root]

    def test_comments_are_not_indexed(self):
        """Comments and processing instructions carry a callable tag, not a name."""
        source = '<root><!-- note --><child name="a"/></root>'
        root = etree.fromstring(source.encode("utf-8"))

        index = XPathUniquenessIndex(root)

        assert index.matches("child", (("name", "a"),)) == root.xpath('.//child')


class TestExtractionCost:
    """Extraction must stay linear in tree size -- the actual subject of #455."""

    def test_cost_growth_is_linear_not_quadratic(self):
        """Quadrupling the node count must not multiply the cost by ~16.

        The pre-fix implementation grew as O(nodes x probes) full-document scans; the
        bound here is loose enough to absorb timing noise on shared CI while still
        failing outright if per-node full-document scanning comes back.
        """
        small, large = _ios_tree(50), _ios_tree(200)

        def _elapsed(page_source: str) -> float:
            helper = _helper(page_source)
            helper.get_interactive_elements(None)  # warm import/parse paths
            start = time.perf_counter()
            helper.get_interactive_elements(None)
            return time.perf_counter() - start

        ratio = _elapsed(large) / max(_elapsed(small), 1e-6)

        assert ratio < 8, f"4x the nodes cost {ratio:.1f}x the time -- superlinear"
