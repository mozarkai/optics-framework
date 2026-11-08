"""Choosing which test cases a run executes, by name.

A selection is an ``include`` and/or ``exclude`` list of name patterns. Names
compare case-insensitively with surrounding whitespace trimmed and inner runs
collapsed, and ``*`` / ``?`` are wildcards; every other character is literal.
Test cases the caller marks as not selectable (setup/teardown) are always kept.
"""
import difflib
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

from optics_framework.common.error import Code, OpticsError

_MAX_LISTED_NAMES = 20


def normalize_name(name: str) -> str:
    return " ".join(name.split()).casefold()


@dataclass(frozen=True)
class NamePattern:
    text: str
    normalized: str

    @classmethod
    def parse(cls, text: str) -> "NamePattern":
        normalized = normalize_name(text)
        if not normalized:
            raise OpticsError(Code.E0503, message="Test case names to include/exclude must not be empty.")
        return cls(text, normalized)

    @property
    def has_wildcards(self) -> bool:
        return any(char in self.text for char in "*?")

    def matches(self, name: str) -> bool:
        return wildcard_match(self.normalized, normalize_name(name))


def wildcard_match(pattern: str, name: str) -> bool:
    """Whole-name match where ``*`` is any run and ``?`` any one character; each piece between ``*`` is placed leftmost, never retried."""
    first, *middle = pattern.split("*")
    if not middle:
        return len(name) == len(first) and _fits(first, name, 0)
    *middle, last = middle
    if not _fits(first, name, 0):
        return False
    position = len(first)
    for piece in middle:
        position = _find(piece, name, position)
        if position < 0:
            return False
        position += len(piece)
    return len(name) - len(last) >= position and _fits(last, name, len(name) - len(last))


def _fits(piece: str, name: str, at: int) -> bool:
    return len(name) - at >= len(piece) and all(char in ("?", name[at + i]) for i, char in enumerate(piece))


def _find(piece: str, name: str, start: int) -> int:
    if "?" not in piece:
        return name.find(piece, start)
    return next((at for at in range(start, len(name) - len(piece) + 1) if _fits(piece, name, at)), -1)


def select_test_cases[T](
    test_cases: dict[str, T],
    include: Iterable[str] | None,
    exclude: Iterable[str] | None,
    selectable: Collection[str],
) -> dict[str, T]:
    """Return ``test_cases`` narrowed to the selection, preserving order.

    Only names in ``selectable`` are filtered; the rest are always kept. Every
    pattern must match at least one selectable test case and the result must
    keep at least one, otherwise ``OpticsError(E0702)`` explains why.
    """
    include_patterns = _parse(include)
    exclude_patterns = _parse(exclude)
    if not include_patterns and not exclude_patterns:
        return dict(test_cases)

    selectable_names = [name for name in test_cases if name in selectable]
    problems = [
        *_unmatched("include", include_patterns, test_cases, selectable_names),
        *_unmatched("exclude", exclude_patterns, test_cases, selectable_names),
    ]
    if problems:
        raise OpticsError(
            Code.E0702,
            message="\n".join([*problems, "", _available(selectable_names)]),
        )

    selectable_set = set(selectable_names)
    selected = {
        name: steps
        for name, steps in test_cases.items()
        if name not in selectable_set or _is_selected(name, include_patterns, exclude_patterns)
    }
    if not selectable_set.intersection(selected):
        raise OpticsError(
            Code.E0702,
            message=(
                "The include/exclude selection leaves no test cases to run.\n\n"
                + _available(selectable_names)
            ),
        )
    return selected


def _parse(patterns: Iterable[str] | None) -> list[NamePattern]:
    return [NamePattern.parse(pattern) for pattern in patterns or ()]


def _is_selected(
    name: str, include: Sequence[NamePattern], exclude: Sequence[NamePattern]
) -> bool:
    if include and not any(pattern.matches(name) for pattern in include):
        return False
    return not any(pattern.matches(name) for pattern in exclude)


def _unmatched(
    option: str,
    patterns: Sequence[NamePattern],
    test_cases: Collection[str],
    selectable_names: Sequence[str],
) -> list[str]:
    selectable = set(selectable_names)
    problems = []
    for pattern in patterns:
        if any(pattern.matches(name) for name in selectable_names):
            continue
        fixtures = [name for name in test_cases if name not in selectable and pattern.matches(name)]
        if fixtures:
            problems.append(
                f"{option} '{pattern.text}' only matches setup/teardown test cases "
                f"({', '.join(fixtures)}), which always run and cannot be selected or excluded."
            )
        else:
            problems.append(f"{option} '{pattern.text}' matches no test case.{_suggestion(pattern, selectable_names)}")
    return problems


def _suggestion(pattern: NamePattern, names: Sequence[str]) -> str:
    if pattern.has_wildcards:
        return ""
    by_normalized = {normalize_name(name): name for name in names}
    close = difflib.get_close_matches(normalize_name(pattern.text), by_normalized, n=3, cutoff=0.6)
    if not close:
        return ""
    return " Did you mean " + " or ".join(f"'{by_normalized[match]}'" for match in close) + "?"


def _available(names: Sequence[str]) -> str:
    if not names:
        return "The project has no test cases besides setup/teardown."
    listed = [f"  - {name}" for name in names[:_MAX_LISTED_NAMES]]
    if len(names) > _MAX_LISTED_NAMES:
        listed.append(f"  ... and {len(names) - _MAX_LISTED_NAMES} more")
    return "\n".join(["Available test cases:", *listed])
