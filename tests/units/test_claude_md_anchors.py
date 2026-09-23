"""CLAUDE.md `path:line` anchors must still point at the symbol they name.

Coding agents navigate this repo by the anchors in CLAUDE.md, so a stale one sends
them to the wrong code with full confidence. This checks every
"`symbol` (`path.py:line`)" pair: the named symbol's definition (def, class,
or assignment) must sit within a few lines of the anchor.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "optics_framework"
TOLERANCE = 3

_PAIR = re.compile(
    r"`@?(?P<symbol>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?:\([^`]*\))?`"
    r" \(`(?P<path>[\w./]+\.py):(?P<line>\d+)`"
)


def _anchor_pairs():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for md_line, line in enumerate(text.splitlines(), 1):
        for match in _PAIR.finditer(line):
            yield md_line, match["symbol"], match["path"], int(match["line"])


def _resolve(path: str) -> pathlib.Path | None:
    for candidate in (ROOT / path, PACKAGE / path):
        if candidate.is_file():
            return candidate
    matches = [p for p in PACKAGE.rglob("*.py") if str(p).endswith("/" + path)]
    return matches[0] if len(matches) == 1 else None


def _defines(source_line: str, name: str) -> bool:
    n = re.escape(name)
    return bool(re.search(
        rf"^\s*(?:async\s+)?def {n}\b|^\s*class {n}\b|^\s*(?:self\.)?{n}\s*(?::[^=]*)?=",
        source_line,
    ))


def test_claude_md_has_anchor_pairs():
    assert len(list(_anchor_pairs())) > 50


def test_claude_md_anchors_point_at_their_symbols():
    stale = []
    for md_line, symbol, path, line in _anchor_pairs():
        source = _resolve(path)
        if source is None:
            stale.append(f"CLAUDE.md:{md_line} `{symbol}` -> {path} (file not found)")
            continue
        lines = source.read_text(encoding="utf-8").splitlines()
        name = symbol.split(".")[-1]
        window = lines[max(line - 1 - TOLERANCE, 0): line + TOLERANCE]
        if not any(_defines(src, name) for src in window):
            actual = [i for i, src in enumerate(lines, 1) if _defines(src, name)]
            stale.append(f"CLAUDE.md:{md_line} `{symbol}` -> {path}:{line} (defined at {actual})")
    assert not stale, "Stale CLAUDE.md anchors:\n" + "\n".join(stale)


def test_agents_md_is_claude_md():
    agents = ROOT / "AGENTS.md"
    if not agents.exists():
        pytest.skip("no AGENTS.md in this checkout")
    assert agents.is_symlink() and agents.resolve() == (ROOT / "CLAUDE.md").resolve()
