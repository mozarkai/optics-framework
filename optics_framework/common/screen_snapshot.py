"""Screen rows with stable refs, diffs, expectation checks and locator ladders.

Shared by the MCP server and the live NL agent. Everything here is pure except
``read_screen``, which reads the current screen through an ``ActionKeyword``.
"""
import bisect
import hashlib
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import cv2
import numpy as np

from optics_framework.common import utils
from optics_framework.common.error import OpticsError
from optics_framework.common.logging_config import internal_logger, is_secret_name
from optics_framework.common.session_liveness import raise_if_session_dead

REF_RE = re.compile(r"^e\d+$")

_LABEL_MAX_CHARS = 80
_ACTIONS = ("tap", "long", "toggle", "input", "scroll")
_CSS_ID_RE = re.compile(r"^[A-Za-z_-][\w-]*$")
_NAME_MAX_WORDS = 4
_BARE_TAG_RE = re.compile(r"(?:xpath=|css=)?/{0,2}[\w.:*-]+")
_WINDOW_BOUNDS = "window_bounds"
_QUOTED_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'')
_POSITIONAL_RE = re.compile(r"\[\s*\d+\s*\]|position\(|last\(|:nth-")
_VALUE_ATTR_RE = re.compile(r"@(?:text|label|value)\b|text\(\)|normalize-space\(")
_STATE_LABELS = frozenset({"on", "off"})
_CONTEXT_ROWS = 2
_CAPTION_MAX_WORDS = 3

TargetKind = Literal["stale_ref", "unknown_ref", "ambiguous", "no_locator"]


@dataclass(frozen=True)
class Row:
    ref: str
    label: str
    cls: str
    act: tuple[str, ...]
    rid: str = ""
    loc: str = ""
    bounds: tuple[int, int, int, int] | None = None
    secret: bool = False


@dataclass(frozen=True)
class Snapshot:
    """The rows shown to the agent; ``all_rows`` holds every row on the screen."""

    rows: tuple[Row, ...]
    digest: str
    hidden: int = 0
    all_rows: tuple[Row, ...] = field(default=(), repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.all_rows:
            object.__setattr__(self, "all_rows", self.rows)

    def row(self, ref: str) -> Row | None:
        return next((r for r in self.all_rows if r.ref == ref), None)


@dataclass(frozen=True)
class Diff:
    changed: bool
    new_screen: bool
    added: tuple[Row, ...]
    removed: tuple[Row, ...]
    relabelled: tuple[tuple[Row, str], ...]


@dataclass(frozen=True)
class Check:
    text: str
    expect: Literal["present", "absent"]
    met: bool
    met_before: bool = False


class TargetError(ValueError):
    """A target the agent cannot act on; the message tells it what to do next."""

    def __init__(self, message: str, *, kind: TargetKind) -> None:
        super().__init__(message)
        self.kind = kind


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def _short_class(cls: str) -> str:
    short = cls.rsplit(".", 1)[-1]
    return short.removeprefix("XCUIElementType") or short


def _bounds(raw: Any) -> tuple[int, int, int, int] | None:
    if isinstance(raw, dict):
        raw = [raw.get("x1"), raw.get("y1"), raw.get("x2"), raw.get("y2")]
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(float(v)) for v in raw)
    except (TypeError, ValueError):
        return None
    return x1, y1, x2, y2


def normalize(el: dict) -> dict | None:
    """One element from any element-list shape as Row fields, or None to drop it."""
    if not isinstance(el, dict):
        return None
    extra = el.get("extra") if isinstance(el.get("extra"), dict) else {}
    if "label" in el and "cls" in el:
        label, raw_cls, rid = el.get("label"), el.get("cls"), el.get("rid")
    else:
        label, raw_cls, rid = el.get("text"), extra.get("class"), extra.get("resource-id")
    label = str(label or "").strip()
    raw_cls = str(raw_cls or "")
    rid = str(rid or "")
    cls = _short_class(raw_cls)
    loc = str(el.get("loc") or ("" if "act" in el else el.get("xpath")) or "")
    web_compact = "act" in el and (raw_cls.islower() or loc.startswith(("css=", "xpath=")))
    if not web_compact and label in {rid, rid.rsplit("/", 1)[-1], raw_cls, cls} - {""}:
        label = ""
    act = el.get("act")
    act = tuple(a for a in act if a in _ACTIONS) if isinstance(act, (list, tuple)) else ()
    if not label and not act:
        return None
    return {
        "label": label,
        "cls": cls,
        "act": act,
        "rid": rid,
        "loc": loc,
        "bounds": _bounds(el.get("bounds")),
        "secret": extra.get("password") is True,
    }


def loc_stable(loc: str) -> bool:
    """False for an indexed ``(...)[n]`` or absolute ``/a/b`` path, which break when the tree changes."""
    path = loc.removeprefix("xpath=")
    if not path or path.startswith("("):
        return False
    return not (path.startswith("/") and not path.startswith("//"))


def digest(rows: Iterable[Row]) -> str:
    lines = sorted(f"{r.cls}|{r.label}|{','.join(r.act)}" for r in rows)
    return hashlib.sha1("\n".join(lines).encode("utf-8"), usedforsecurity=False).hexdigest()[:6]


def _names_one_element(loc: str) -> bool:
    """False when ``loc`` is unique only by chance on this screen: indexed, absolute or a bare tag."""
    return loc_stable(loc) and not _BARE_TAG_RE.fullmatch(loc)


def _unquoted(loc: str) -> str:
    return _QUOTED_RE.sub('""', loc)


def _strong_loc(loc: str) -> bool:
    return _names_one_element(loc) and not _POSITIONAL_RE.search(_unquoted(loc))


def _literals(loc: str) -> list[str]:
    return [a or b for a, b in _QUOTED_RE.findall(loc)]


def _names_by_text(loc: str, label: str) -> bool:
    """True when ``loc`` quotes the row's own text, so a new text gives a new loc."""
    wanted = _fold(label)
    return bool(wanted) and any(_fold(lit) == wanted for lit in _literals(loc))


def _holds_value(fields: dict) -> bool:
    """True when the row's text is its value or state: an input, or a toggle that shows on/off."""
    if "input" in fields["act"]:
        return True
    return "toggle" in fields["act"] and (
        fields["cls"] == "ToggleButton" or _fold(fields["label"]) in _STATE_LABELS
    )


def _identity(
    fields: dict,
    window_bounds: tuple[int, int, int, int] | None,
    context: tuple,
    following: tuple[str, ...],
) -> tuple:
    """A row's key from this read alone: equal keys mean the same element.

    A loc that names one element keys the row; when the loc names it by an id rather than by
    its text, the rows before it are part of the key, because an id can repeat across screens.
    Any other row is keyed by place, the rows before it and its text; a row with no text also
    by the rows after it. The text of an input or a state toggle is never part of a key.
    """
    loc, label = fields["loc"], fields["label"]
    holds_value = _holds_value(fields)
    if _strong_loc(loc) and not (holds_value and _VALUE_ATTR_RE.search(_unquoted(loc))):
        return ("loc", loc, fields["cls"], fields["rid"], () if _names_by_text(loc, label) else context, "")
    if not label:
        context = (*context, "", *following)
    return ("pos", window_bounds, fields["cls"], fields["rid"], context, "" if holds_value else _fold(label))


def _context_label(fields: dict) -> str:
    """The text a row lends to the keys of the rows near it, or "" when that text can change."""
    if not fields["label"] or _holds_value(fields):
        return ""
    if _strong_loc(fields["loc"]) and not _names_by_text(fields["loc"], fields["label"]):
        return ""
    return _fold(fields["label"])


def _contexts(labels: list[str]) -> list[tuple[tuple, tuple[str, ...]]]:
    """Each row's context: the unique texts above it plus a count of repeated ones, and those below it.

    A text that several rows show (a subtitle, "In stock") cannot tell one list item from another.
    """
    counts = Counter(labels)
    marks = [i for i, text in enumerate(labels) if text and counts[text] == 1]
    before: list[str] = []
    since = 0
    out: list[tuple[tuple, tuple[str, ...]]] = []
    for i, text in enumerate(labels):
        after = bisect.bisect_right(marks, i)
        following = tuple(labels[j] for j in marks[after:after + _CONTEXT_ROWS])
        out.append(((*before[-_CONTEXT_ROWS:], since), following))
        if text and counts[text] == 1:
            before.append(text)
            since = 0
        elif text:
            since += 1
    return out


def _keep(rows: list[Row], max_rows: int) -> tuple[Row, ...]:
    actionable = [i for i, r in enumerate(rows) if r.act]
    text = [i for i, r in enumerate(rows) if not r.act]
    return tuple(rows[i] for i in sorted((actionable + text)[:max_rows]))


def _ref_ranges(refs: Iterable[str]) -> str:
    runs: list[list[int]] = []
    for n in sorted(int(r[1:]) for r in refs):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(f"e{a}" if a == b else f"e{a}–e{b}" for a, b in runs)


def _describe(row: Row) -> str:
    return f'"{_clean_label(row.label)}" {row.cls}' if row.label else f"{row.cls}, no text"


class RefTable:
    """Allocates refs for one MCP session or one NL run; refs only grow.

    A row's ref depends only on how the row looks in the current read (see ``_identity``),
    so a row that did not change keeps its ref whatever happened around it, and a row
    that could be another element gets a new ref. Rows with equal keys take the refs of
    that key in order.
    """

    def __init__(self) -> None:
        self._next = 1
        self._refs_by_key: dict[tuple, list[str]] = {}
        self._issued: dict[str, Row] = {}
        self._latest: Snapshot | None = None

    def _ref(self, key: tuple, occurrence: int) -> str:
        keyed = self._refs_by_key.setdefault(key, [])
        if occurrence == len(keyed):
            keyed.append(f"e{self._next}")
            self._next += 1
        return keyed[occurrence]

    def snapshot(self, elements: list[dict], *, max_rows: int = 80, query: str = "") -> Snapshot:
        read = [(el, fields) for el in elements or [] if (fields := normalize(el)) is not None]
        contexts = _contexts([_context_label(fields) for _, fields in read])
        seen: dict[tuple, int] = {}
        every: list[Row] = []
        for (el, fields), (context, following) in zip(read, contexts):
            window_bounds = _bounds(el.get(_WINDOW_BOUNDS, el.get("bounds")))
            key = _identity(fields, window_bounds, context, following)
            occurrence = seen.get(key, 0)
            seen[key] = occurrence + 1
            row = Row(ref=self._ref(key, occurrence), **fields)
            self._issued[row.ref] = row
            every.append(row)
        pool = every
        if query:
            needle = _fold(query)
            pool = [r for r in every if needle in _fold(r.label) or needle in r.rid.casefold()]
        rows = _keep(pool, max_rows)
        snap = Snapshot(
            rows=rows, digest=digest(every), hidden=len(pool) - len(rows), all_rows=tuple(every)
        )
        self._latest = snap
        return snap

    def latest(self) -> Snapshot | None:
        return self._latest

    def resolve(self, target: str) -> Row | None:
        """The row a ref or an exact label names; None means: pass the raw target through."""
        target = (target or "").strip()
        snap = self._latest
        current = snap.all_rows if snap else ()
        if REF_RE.match(target):
            row = snap.row(target) if snap else None
            if row is not None:
                return row
            if target in self._issued:
                raise TargetError(
                    f"{target} ({_describe(self._issued[target])}) is not on the current screen.",
                    kind="stale_ref",
                )
            refs = _ref_ranges(r.ref for r in current)
            hint = f"current refs are {refs}" if refs else "the current screen has no refs"
            raise TargetError(f"No {target}; {hint}.", kind="unknown_ref")
        if not target:
            return None
        wanted = _fold(target)
        matches = [r for r in current if r.label and _fold(r.label) == wanted]
        if len(matches) > 1:
            matches = [r for r in matches if r.act] or matches
        if len(matches) == 1:
            return matches[0]
        if matches:
            listed = ", ".join(f'{r.ref} "{_clean_label(r.label)}"' for r in matches[:8])
            raise TargetError(
                f'"{target}" matches {len(matches)} rows: {listed}. Pass one ref.',
                kind="ambiguous",
            )
        return None


def diff(prev: Snapshot | None, cur: Snapshot) -> Diff:
    if prev is None:
        return Diff(True, True, cur.all_rows, (), ())
    before = {r.ref: r for r in prev.all_rows}
    after = {r.ref: r for r in cur.all_rows}
    added = tuple(r for r in cur.all_rows if r.ref not in before)
    removed = tuple(r for r in prev.all_rows if r.ref not in after)
    relabelled = tuple(
        (r, before[r.ref].label)
        for r in cur.all_rows
        if r.ref in before and before[r.ref].label != r.label
    )
    changed = bool(added or removed or relabelled) or prev.digest != cur.digest
    if prev.all_rows:
        new_screen = len(removed) * 2 > len(prev.all_rows) and len(added) * 2 > len(cur.all_rows)
    else:
        new_screen = bool(cur.all_rows)
    return Diff(changed, new_screen, added, removed, relabelled)


def _contains_words(label: str, text: str) -> bool:
    return label == text or re.search(rf"(?<!\w){re.escape(text)}(?!\w)", label) is not None


def _on_screen(snap: Snapshot, text: str) -> bool:
    if REF_RE.match(text):
        return snap.row(text) is not None
    wanted = _fold(text)
    return any(_contains_words(_fold(r.label), wanted) for r in snap.all_rows if r.label)


def check_expectations(
    snap: Snapshot, present: Sequence[str], absent: Sequence[str], *, before: Snapshot | None = None
) -> list[Check]:
    """Each expectation against ``snap``; ``before`` is the screen ahead of the action, if any."""
    checks: list[Check] = []
    for expect, texts in (("present", present), ("absent", absent)):
        for text in texts or []:
            if not text.strip():
                continue
            wanted = expect == "present"
            met = _on_screen(snap, text.strip()) == wanted
            met_before = before is not None and _on_screen(before, text.strip()) == wanted
            checks.append(Check(text, expect, met, met_before))
    return checks


def judge_step(ok: bool, diff: Diff, checks: list[Check]) -> Literal["pass", "fail", "unknown"]:
    """A check already met before the action proves nothing about it."""
    if not ok:
        return "fail"
    if not checks:
        return "unknown"
    if all(c.met for c in checks):
        if diff.changed or any(c.expect == "present" and not c.met_before for c in checks):
            return "pass"
        return "unknown"
    return "unknown" if diff.changed else "fail"


def without_typed_value(checks: list[Check], typed: str) -> list[Check]:
    """Drop unmet present checks that only repeat ``typed``: rows often miss a typed value."""
    if not typed.strip():
        return checks
    value = _fold(typed)
    return [c for c in checks if c.met or c.expect != "present" or _fold(c.text) != value]


def _is_web(row: Row) -> bool:
    if row.loc.startswith(("css=", "xpath=")):
        return True
    if row.loc.startswith(("/", "(")):
        return False
    return row.cls == row.cls.lower()


def _rid_rung(row: Row) -> str:
    if _is_web(row):
        return f"css=#{row.rid}" if _CSS_ID_RE.match(row.rid) else f'css=[id="{row.rid}"]'
    return f'//*[@resource-id="{row.rid}"]'


def _rid_shared(row: Row) -> bool:
    """A native loc that is not unique by name means get_xpath found the rid on other nodes too."""
    return bool(row.loc) and not _is_web(row) and not _names_one_element(row.loc)


def _locator_rungs(row: Row, snap: Snapshot) -> list[str]:
    rungs = [row.loc] if row.loc else []
    if row.rid and not _rid_shared(row) and sum(r.rid == row.rid for r in snap.all_rows) == 1:
        rid_rung = _rid_rung(row)
        if rid_rung not in rungs and rid_rung.removeprefix("css=") not in rungs:
            rungs.append(rid_rung)
    return rungs


def _no_locator(row: Row) -> TargetError:
    return TargetError(
        f"{row.ref} ({_describe(row)}) has no unique locator; observe again or pass a locator.",
        kind="no_locator",
    )


def live_ladder(row: Row, snap: Snapshot) -> list[str]:
    """Locators for acting on ``row`` now: XPath/CSS only, never a bare-text rung."""
    rungs = _locator_rungs(row, snap)
    if not rungs:
        raise _no_locator(row)
    return rungs


def masked_value(row: Row) -> bool:
    """A field showing a masked value such as ``••••••``: it holds a secret."""
    return "input" in row.act and bool(row.label.strip()) and not any(c.isalnum() for c in row.label)


def holds_secret(row: Row, snap: Snapshot | None = None) -> bool:
    """A field marked as a password, masked, or an input right after a caption such as "PIN"."""
    if row.secret or "SecureTextField" in row.cls or masked_value(row):
        return True
    if "input" not in row.act or snap is None:
        return False
    i = next((n for n, r in enumerate(snap.all_rows) if r.ref == row.ref), 0)
    caption = snap.all_rows[i - 1] if i else None
    return (
        caption is not None and not caption.act
        and len(caption.label.split()) <= _CAPTION_MAX_WORDS and is_secret_name(caption.label)
    )


def saved_ladder(row: Row, snap: Snapshot) -> list[str]:
    """Locators for a recording: the live rungs, a stable one first, then the label."""
    rungs = _locator_rungs(row, snap)
    if rungs and not loc_stable(rungs[0]):
        stable = next((r for r in rungs[1:] if loc_stable(r)), None)
        if stable:
            rungs.remove(stable)
            rungs.insert(0, stable)
    if row.label and not masked_value(row):
        text = _text_rung(row.label)
        if text not in rungs:
            rungs.append(text)
    if not rungs:
        raise _no_locator(row)
    return rungs


def _text_rung(label: str) -> str:
    return label if utils.determine_element_type(label) == "Text" else f"text={label}"


def _xpath_literal(text: str) -> str:
    if '"' not in text:
        return f'"{text}"'
    if "'" not in text:
        return f"'{text}'"
    parts = text.split('"')
    return "concat(" + ", '\"', ".join(f'"{p}"' for p in parts) + ")"


def acting_ladder(row: Row, snap: Snapshot) -> list[str]:
    """The live ladder, or one built from the label when the source gave ``row`` no locator.

    A label shared with other rows is indexed among its web twins; on a native screen it
    names no one element, so ``TargetError`` is raised.
    """
    try:
        return live_ladder(row, snap)
    except TargetError:
        if not row.label:
            raise
    same = [r for r in snap.all_rows if r.label == row.label]
    if len(same) == 1:
        return [_text_rung(row.label)]
    if row.cls != row.cls.lower():
        raise TargetError(f"{row.ref} has no unique locator; pick another row or pass a locator.", kind="no_locator")
    twins = [r for r in same if r.cls == row.cls]
    index = next(i for i, r in enumerate(twins, 1) if r.ref == row.ref)
    return [f"xpath=(//{row.cls}[normalize-space()={_xpath_literal(' '.join(row.label.split()))}])[{index}]"]


def recordable_present(present: Sequence[str], snap: Snapshot) -> tuple[list[str], list[str]]:
    """``present`` items as ``assert_presence`` elements, and notes for the items left out.

    A ref becomes its row's first saved rung. A text becomes the label of the row it
    names in full; a text that is only part of a longer label cannot be replayed.
    """
    items: list[str] = []
    notes: list[str] = []
    for raw in present:
        item = raw.strip()
        if not item:
            continue
        if REF_RE.match(item):
            row = snap.row(item)
            if row is None:
                notes.append(f"{item} is not on the screen, so it was not recorded")
                continue
            try:
                items.append(saved_ladder(row, snap)[0])
            except TargetError:
                notes.append(f"{item} has no locator or text, so it was not recorded")
            continue
        if utils.determine_element_type(item) != "Text":
            items.append(item)
            continue
        text = _fold(item.removeprefix("text="))
        row = next((r for r in snap.all_rows if r.label and _fold(r.label) == text), None)
        if row is not None:
            items.append(_text_rung(row.label))
        else:
            notes.append(f'"{item}" is not the whole text of a row, so it was not recorded')
    return items, notes


def _snake_words(text: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return re.findall(r"[a-z0-9]+", spaced.lower())


def element_name(row: Row, taken: set[str]) -> str:
    """A snake_case element name such as ``sign_in_button``, unique against ``taken``."""
    words = [w for w in re.findall(r"[a-z0-9]+", row.label.lower()) if not w.isdigit()][:_NAME_MAX_WORDS]
    if not words:
        words = _snake_words(row.rid.rsplit("/", 1)[-1])
    suffix = _snake_words(row.cls)
    if suffix and words[-len(suffix):] != suffix:
        words += suffix
    base = "_".join(words) or "element"
    name, n = base, 2
    while name in taken:
        name = f"{base}_{n}"
        n += 1
    return name


def _clean_label(label: str) -> str:
    text = " ".join(label.split()).replace('"', "'")
    return text[:_LABEL_MAX_CHARS] + "…" if len(text) > _LABEL_MAX_CHARS else text


def _label_part(label: str) -> str:
    return f'"{_clean_label(label)}"' if label.strip() else "(no text)"


def render_row(row: Row) -> str:
    parts = [row.ref, row.cls, _label_part(row.label)]
    if row.act:
        parts.append(f"[{','.join(row.act)}]")
    if row.rid:
        parts.append(f"#{row.rid.rsplit('/', 1)[-1]}")
    return " ".join(p for p in parts if p)


def _row_lines(snap: Snapshot) -> list[str]:
    lines = [render_row(r) for r in snap.rows]
    if snap.hidden:
        lines.append(f'… {snap.hidden} more rows hidden; observe(query="…") to find one')
    return lines


def render_rows(snap: Snapshot) -> str:
    count = len(snap.rows)
    header = f"screen {snap.digest} · {count} row{'' if count == 1 else 's'}"
    return "\n".join([header, *_row_lines(snap)])


def render_diff(d: Diff, cur: Snapshot) -> str:
    if d.new_screen:
        return "\n".join([f"screen {cur.digest} · new screen", *_row_lines(cur)])
    if not d.changed:
        return f"screen {cur.digest} · unchanged"
    lines = [f"screen {cur.digest} · changed (+{len(d.added)} -{len(d.removed)} ~{len(d.relabelled)})"]
    lines += [f"+ {render_row(r)}" for r in d.added]
    lines += [f"- {r.ref} {_label_part(r.label)}" for r in d.removed]
    lines += [f"~ {render_row(r)} (was {_label_part(old)})" for r, old in d.relabelled]
    return "\n".join(lines)


def settle(
    read: Callable[[], Snapshot], prev_digest: str, *, pause_s: float = 0.6, extra_reads: int = 2
) -> Snapshot:
    """Read the screen, re-reading while its digest still equals ``prev_digest``."""
    snap = read()
    for _ in range(extra_reads):
        if snap.digest != prev_digest:
            break
        time.sleep(pause_s)
        snap = read()
    return snap


def read_screen(action_keyword: Any, png: bytes | None = None) -> list[dict] | None:
    """Compact elements of the current screen, or None when the source cannot list them.

    None means the screen could not be read, never an empty screen. A dead driver session
    raises ``E0106``. Bounds are scaled against ``png`` only when it is given and the active
    source is Appium; each element keeps its unscaled bounds as ``window_bounds`` so ref
    identity does not depend on the scaling.
    """
    if action_keyword is None:
        return None
    try:
        elements = action_keyword.strategy_manager.get_interactive_elements(None, compact=True)
    except OpticsError as exc:
        raise_if_session_dead(exc)
        internal_logger.debug("Structured screen elements unavailable: %s", exc)
        return None
    for el in elements or []:
        if isinstance(el, dict) and "bounds" in el:
            el[_WINDOW_BOUNDS] = el["bounds"]
    element_source = action_keyword.element_source
    active_source = getattr(element_source, "active_instance", None) or element_source
    screenshot_np = None
    if png and getattr(active_source, "REQUIRED_DRIVER_TYPE", None) == "appium":
        try:
            screenshot_np = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception as exc:  # noqa: BLE001
            internal_logger.debug("Screenshot decode for bounds scaling failed: %s", exc)
    utils.scale_interactive_element_bounds(elements, element_source, screenshot_np)
    return elements
