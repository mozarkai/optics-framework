"""One pass over a document that answers XPath uniqueness probes without rescanning it."""
from bisect import bisect_left
from collections.abc import Callable, Iterable
from typing import Any

Conditions = tuple[tuple[str, str], ...]


class XPathIndex:
    """Document-order posting lists answering "how many nodes match this probe?".

    Choosing a locator asks, per candidate attribute, whether ``//tag[@attr=value]``
    matches exactly one node. Evaluating that with ``xpath()`` rescans the whole
    document per probe, so labelling every node costs O(nodes x probes) full scans.
    One indexing pass answers every probe from a dict instead.

    Match lists are in document order because callers position a non-unique node
    among its peers (``(xpath)[n]``), which must agree with what XPath returns.
    """

    __slots__ = (
        "_by_tag", "_by_tag_attr", "_derived", "_doc_pos", "_multi_cache", "_siblings", "xpath_cache",
    )

    def __init__(self, root: Any, attributes: Iterable[str]):
        attributes = tuple(attributes)
        self._by_tag: dict[str, list[Any]] = {}
        self._by_tag_attr: dict[tuple[str, str, str], list[Any]] = {}
        self._doc_pos: dict[Any, int] = {}
        self._multi_cache: dict[tuple[Any, ...], list[Any]] = {}
        self._siblings: dict[Any, int | None] = {}
        self._derived: dict[tuple[str, str], dict[str, list[Any]]] = {}
        self.xpath_cache: dict[Any, str] = {}
        for position, node in enumerate(root.iter()):
            tag = node.tag
            if not isinstance(tag, str):
                continue  # comments and processing instructions carry a callable tag
            if "{" in tag:
                # A namespaced tag ("{uri}local") is not valid XPath, so every probe built
                # from one fails to parse; unindexed, its probes stay empty as they did.
                continue
            self._doc_pos[node] = position
            self._by_tag.setdefault(tag, []).append(node)
            attrib = node.attrib
            for attr in attributes:
                val = attrib.get(attr)
                if val:
                    self._by_tag_attr.setdefault((tag, attr, val), []).append(node)

    def matches(self, tag: str, conditions: Conditions) -> list[Any]:
        """Nodes matching ``//tag[@a=v and ...]``, in document order."""
        if not conditions:
            return self._by_tag.get(tag, [])
        if len(conditions) == 1:
            attr, val = conditions[0]
            return self._by_tag_attr.get((tag, attr, val), [])
        # Sibling rows sharing a label issue identical probes; the memo keeps that linear.
        cache_key = (tag, conditions)
        cached = self._multi_cache.get(cache_key)
        if cached is not None:
            return cached
        postings = [self._by_tag_attr.get((tag, a, v), []) for a, v in conditions]
        shortest = min(postings, key=len)
        result = [n for n in shortest if all(n.attrib.get(a) == v for a, v in conditions)]
        self._multi_cache[cache_key] = result
        return result

    def derived_matches(self, tag: str, kind: str, key: str, keys_of: Callable[[Any], Iterable[str]]) -> list[Any]:
        """Nodes of ``tag`` for which ``keys_of(node)`` yields ``key``; built once per ``(tag, kind)``."""
        table = self._derived.get((tag, kind))
        if table is None:
            table = {}
            for node in self._by_tag.get(tag, []):
                for node_key in dict.fromkeys(keys_of(node)):
                    table.setdefault(node_key, []).append(node)
            self._derived[(tag, kind)] = table
        return table.get(key, [])

    def position_of(self, matches: list[Any], node: Any) -> int:
        """0-based position of ``node`` within a document-ordered match list (binary search)."""
        pos = self._doc_pos.get(node)
        if pos is None:
            return 0
        found = bisect_left(matches, pos, key=lambda n: self._doc_pos[n])
        return found if found < len(matches) and matches[found] is node else 0

    def sibling_position(self, node: Any) -> int | None:
        """1-based position among same-tag siblings, or None when the tag is unique under its parent."""
        if node not in self._siblings:
            parent = node.getparent()
            if parent is None:
                return None
            by_tag: dict[Any, list[Any]] = {}
            for child in parent:
                by_tag.setdefault(child.tag, []).append(child)
            for peers in by_tag.values():
                for position, peer in enumerate(peers, start=1):
                    self._siblings[peer] = position if len(peers) > 1 else None
        return self._siblings[node]
