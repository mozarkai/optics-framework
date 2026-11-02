"""Screen rows with stable refs, diffs, expectation checks and locator ladders."""
import bisect
import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import cv2
import numpy as np

from optics_framework.common import utils
from optics_framework.common.error import OpticsError
from optics_framework.common.logging_config import internal_logger, is_secret_name
from optics_framework.common.session_liveness import raise_if_session_dead

REF_RE = re.compile(r"^e\d+$")
REDACTED = "<redacted>"
_ACTIONS = ("tap", "long", "toggle", "input", "scroll")
_CSS_ID_RE = re.compile(r"^[A-Za-z_-][\w-]*$")
_BARE_TAG_RE = re.compile(r"(?:xpath=|css=)?/{0,2}[\w.:*-]+")
_QUOTED_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'')
_POSITIONAL_RE = re.compile(r"\[\s*\d+\s*\]|position\(|last\(|:nth-")
_VALUE_ATTR_RE = re.compile(r"@(?:text|label|value)\b|text\(\)|normalize-space\(")
_WINDOW_BOUNDS = "window_bounds"
_XPATH = "xpath="
_CONTEXT_ROWS = 2
_CAPTION_MAX_WORDS = 3
_NAME_MAX_WORDS = 4

Verified = Literal["pass", "fail", "unknown"]


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
    hint: str = ""
    holds_value: bool = False
    web: bool = False
    name: str | None = None

    def __post_init__(self) -> None:
        if self.name is None:
            object.__setattr__(self, "name", self.label)


@dataclass(frozen=True)
class Snapshot:
    """The rows shown to the agent; ``all_rows`` holds every row on the screen."""

    rows: tuple[Row, ...]
    digest: str
    all_rows: tuple[Row, ...] = field(repr=False, compare=False)
    hidden: int = 0

    def row(self, ref: str) -> Row | None:
        return next((r for r in self.all_rows if r.ref == ref), None)

    def showing(self, rows: tuple[Row, ...], hidden: int) -> "Snapshot":
        """This screen with ``rows`` shown and ``hidden`` more left out."""
        return Snapshot(rows=rows, digest=self.digest, all_rows=self.all_rows, hidden=hidden)


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


@dataclass(frozen=True)
class Verdict:
    verified: Verified
    checks: tuple[Check, ...]
    untested: tuple[Check, ...]
    diff: Diff | None
    vision: bool


@dataclass(frozen=True)
class Ladder:
    """Locators for one row: ``act`` acts on it now, ``save`` goes into a recording, ``replay`` is what a replay uses."""

    act: list[str]
    save: list[str]
    by_label: bool
    replay: str


class TargetError(ValueError):
    """A target the agent cannot act on; the message tells it what to do next."""

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def _bounds(raw: Any) -> tuple[int, int, int, int] | None:
    if isinstance(raw, dict):
        raw = [raw.get(k) for k in ("x1", "y1", "x2", "y2")]
    try:
        x1, y1, x2, y2 = (int(float(v)) for v in raw) if isinstance(raw, (list, tuple)) else ()
    except (TypeError, ValueError):
        return None
    return x1, y1, x2, y2


def _masked(act: tuple[str, ...], label: str) -> bool:
    """A field showing a masked value such as ``••••••``."""
    return "input" in act and bool(label.strip()) and not any(c.isalnum() for c in label)


def _source_fields(el: dict, extra: dict) -> tuple[str, str, str]:
    """Label, class and id from a compact row or a full element."""
    if "label" in el and "cls" in el:
        label, raw_cls, rid = el.get("label"), el.get("cls"), el.get("rid")
    else:
        label, raw_cls, rid = el.get("text"), extra.get("class"), extra.get("resource-id")
    return str(label or "").strip(), str(raw_cls or ""), str(rid or "")


def _secret_caption(act: tuple[str, ...], before: dict | None) -> bool:
    """An input right under a short read-only caption such as "PIN"."""
    return (
        before is not None and "input" in act and not before["act"]
        and len(before["label"].split()) <= _CAPTION_MAX_WORDS and is_secret_name(before["label"])
    )


def _row_hint(hint: str, label: str, holds_value: bool, before: dict | None) -> str:
    """A value field right under a caption with the same text takes that text as its hint."""
    if hint or not (before and label and holds_value) or before["holds_value"]:
        return hint
    return label if fold(before["label"]) == fold(label) else hint


def _acts(act: Any) -> tuple[str, ...]:
    return tuple(a for a in act if a in _ACTIONS) if isinstance(act, (list, tuple)) else ()


def _is_web(loc: str, cls: str) -> bool:
    """A web row: a CSS or XPath locator, or a lower-case tag that no native path names."""
    return loc.startswith(("css=", _XPATH)) or (not loc.startswith(("/", "(")) and cls == cls.lower())


def _holds_value(act: tuple[str, ...], cls: str, label: str) -> bool:
    """An input, or a toggle whose label is its state."""
    return "input" in act or ("toggle" in act and (cls == "ToggleButton" or fold(label) in ("on", "off")))


def normalize(el: dict, before: dict | None = None) -> dict | None:
    """One element from any element-list shape as Row fields, or None to drop it; ``before`` is the row above it."""
    if not isinstance(el, dict):
        return None
    extra = el.get("extra") if isinstance(el.get("extra"), dict) else {}
    label, raw_cls, rid = _source_fields(el, extra)
    short = raw_cls.rsplit(".", 1)[-1]
    cls = short.removeprefix("XCUIElementType") or short
    loc = str(el.get("loc") or ("" if "act" in el else el.get("xpath")) or "")
    web = _is_web(loc, cls)
    if not web and label in {rid, rid.rsplit("/", 1)[-1], raw_cls, cls} - {""}:
        label = ""
    act = _acts(el.get("act"))
    if not label and not act:
        return None
    holds_value = _holds_value(act, cls, label)
    hint = _row_hint(str(el.get("hint") or extra.get("hint") or "").strip(), label, holds_value, before)
    secret = (
        extra.get("password") is True or "SecureTextField" in cls or _masked(act, label)
        or _secret_caption(act, before) or any(is_secret_name(text) for text in (label, rid, loc, hint))
    )
    # A native label that differs from its hint is the typed value, so the hint (even empty) names the row.
    name = hint if label and not web and holds_value and fold(label) != fold(hint) else label
    return {
        "label": label, "cls": cls, "act": act, "rid": rid, "loc": loc, "bounds": _bounds(el.get("bounds")),
        "secret": secret, "hint": hint, "holds_value": holds_value, "web": web, "name": name,
    }


def loc_stable(loc: str) -> bool:
    """False for an indexed ``(...)[n]`` or absolute ``/a/b`` path, which break when the tree changes."""
    path = loc.removeprefix(_XPATH)
    return bool(path) and not path.startswith("(") and (path.startswith("//") or not path.startswith("/"))


def _names_one_element(loc: str) -> bool:
    """False when ``loc`` is unique only by chance on this screen: indexed, absolute or a bare tag."""
    return loc_stable(loc) and not _BARE_TAG_RE.fullmatch(loc)


def _unquoted(loc: str) -> str:
    return _QUOTED_RE.sub('""', loc)


def _strong_loc(loc: str) -> bool:
    return _names_one_element(loc) and not _POSITIONAL_RE.search(_unquoted(loc))


def _names_by_text(loc: str, label: str) -> bool:
    """True when ``loc`` quotes the row's own text, so a new text gives a new loc."""
    return bool(label) and any(fold(a or b) == fold(label) for a, b in _QUOTED_RE.findall(loc))


def digest(rows: Iterable[Row]) -> str:
    lines = sorted(f"{r.cls}|{r.label}|{','.join(r.act)}" for r in rows)
    return hashlib.sha1("\n".join(lines).encode("utf-8"), usedforsecurity=False).hexdigest()[:6]


def _identity(fields: dict, window_bounds: tuple | None, context: tuple, following: tuple[str, ...]) -> tuple:
    """A row's key: its strong loc plus the rows before it (ids repeat), else its place and neighbours; never a value."""
    loc, label, holds_value = fields["loc"], fields["label"], fields["holds_value"]
    if _strong_loc(loc) and not (holds_value and _VALUE_ATTR_RE.search(_unquoted(loc))):
        return ("loc", loc, fields["cls"], fields["rid"], () if _names_by_text(loc, label) else context, "")
    if not label:
        context = (*context, "", *following)
    return ("pos", window_bounds, fields["cls"], fields["rid"], context, "" if holds_value else fold(label))


def _context_label(fields: dict) -> str:
    """The text a row lends to the keys of the rows near it, or "" when that text can change."""
    label, loc = fields["label"], fields["loc"]
    if not label or fields["holds_value"] or (_strong_loc(loc) and not _names_by_text(loc, label)):
        return ""
    return fold(label)


def _contexts(labels: list[str]) -> list[tuple[tuple, tuple[str, ...]]]:
    """Each row's context: the unique texts above it plus a count of repeated ones, and those below it."""
    counts = Counter(labels)
    marks = [i for i, text in enumerate(labels) if text and counts[text] == 1]
    before: list[str] = []
    since = 0
    out: list[tuple[tuple, tuple[str, ...]]] = []
    for i, text in enumerate(labels):
        after = bisect.bisect_right(marks, i)
        out.append(((*before[-_CONTEXT_ROWS:], since), tuple(labels[j] for j in marks[after:after + _CONTEXT_ROWS])))
        if text and counts[text] == 1:
            before.append(text)
            since = 0
        elif text:
            since += 1
    return out


def _ref_ranges(refs: Iterable[str]) -> str:
    runs: list[list[int]] = []
    for n in sorted(int(r[1:]) for r in refs):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(f"e{a}" if a == b else f"e{a}–e{b}" for a, b in runs)


def _describe(row: Row) -> str:
    return f'"{clean_label(row.label)}" {row.cls}' if row.label else f"{row.cls}, no text"


class RefTable:
    """Allocates refs for one session from each row's look in the current read; refs only grow."""

    def __init__(self) -> None:
        self._next = 1
        self._refs_by_key: dict[tuple, list[str]] = {}
        self._issued: dict[str, Row] = {}
        self.latest: Snapshot | None = None

    def _ref(self, key: tuple, occurrence: int) -> str:
        keyed = self._refs_by_key.setdefault(key, [])
        if occurrence == len(keyed):
            keyed.append(f"e{self._next}")
            self._next += 1
        return keyed[occurrence]

    def snapshot(self, elements: list[dict], *, max_rows: int = 80, query: str = "") -> Snapshot:
        read: list[tuple[dict, dict]] = []
        for el in elements or []:
            fields = normalize(el, read[-1][1] if read else None)
            if fields is not None:
                read.append((el, fields))
        seen: Counter = Counter()
        every: list[Row] = []
        for (el, fields), (context, following) in zip(read, _contexts([_context_label(f) for _, f in read])):
            key = _identity(fields, _bounds(el.get(_WINDOW_BOUNDS, el.get("bounds"))), context, following)
            row = Row(ref=self._ref(key, seen[key]), **fields)
            seen[key] += 1
            self._issued[row.ref] = row
            every.append(row)
        needle = fold(query)
        pool = [r for r in every if needle in fold(r.label) or needle in r.rid.casefold()] if query else every
        actionable_first = sorted(range(len(pool)), key=lambda i: not pool[i].act)
        rows = tuple(pool[i] for i in sorted(actionable_first[:max_rows]))
        self.latest = Snapshot(rows=rows, digest=digest(every), all_rows=tuple(every), hidden=len(pool) - len(rows))
        return self.latest

    def _resolve_ref(self, ref: str) -> Row:
        snap = self.latest
        row = snap.row(ref) if snap else None
        if row is not None:
            return row
        if ref in self._issued:
            raise TargetError(f"{ref} ({_describe(self._issued[ref])}) is not on the current screen.", kind="stale_ref")
        refs = _ref_ranges(r.ref for r in (snap.all_rows if snap else ()))
        hint = f"current refs are {refs}" if refs else "the current screen has no refs"
        raise TargetError(f"No {ref}; {hint}.", kind="unknown_ref")

    def resolve(self, target: str) -> Row | None:
        """The row a ref or an exact label names; None means: pass the raw target through."""
        target = (target or "").strip()
        snap = self.latest
        current = snap.all_rows if snap else ()
        if REF_RE.match(target):
            return self._resolve_ref(target)
        if not target:
            return None
        matches = [r for r in current if fold(r.name) == fold(target)]
        if len(matches) > 1:
            matches = [r for r in matches if r.act] or matches
        if len(matches) > 1:
            listed = ", ".join(f'{r.ref} "{clean_label(r.label)}"' for r in matches[:8])
            raise TargetError(f'"{target}" matches {len(matches)} rows: {listed}. Pass one ref.', kind="ambiguous")
        return matches[0] if matches else None


def diff(prev: Snapshot | None, cur: Snapshot) -> Diff:
    if prev is None:
        return Diff(True, True, cur.all_rows, (), ())
    before = {r.ref: r for r in prev.all_rows}
    after = {r.ref: r for r in cur.all_rows}
    added = tuple(r for r in cur.all_rows if r.ref not in before)
    removed = tuple(r for r in prev.all_rows if r.ref not in after)
    relabelled = tuple(
        (r, before[r.ref].label) for r in cur.all_rows if r.ref in before and before[r.ref].label != r.label
    )
    changed = bool(added or removed or relabelled) or prev.digest != cur.digest
    if prev.all_rows:
        new_screen = len(removed) * 2 > len(prev.all_rows) and len(added) * 2 > len(cur.all_rows)
    else:
        new_screen = bool(cur.all_rows)
    return Diff(changed, new_screen, added, removed, relabelled)


def _on_screen(snap: Snapshot, text: str) -> bool:
    if REF_RE.match(text):
        return snap.row(text) is not None
    wanted = fold(text)
    return any(re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", fold(r.label)) for r in snap.all_rows if r.label)


def verify(
    ok: bool, before: Snapshot | None, after: Snapshot | None, present: Sequence[str] = (),
    absent: Sequence[str] = (), *, typed: str = "", strategy: str | None = None, exact: bool = True,
) -> Verdict:
    """Judge one action; ``after`` is None when unreadable, and an inexact target found by vision never passes."""
    vision = not exact and strategy in ("OCR", "Image")
    if after is None:
        return Verdict("unknown" if ok else "fail", (), (), None, vision)
    checks = [
        Check(text, expect, _on_screen(after, text.strip()) == (expect == "present"),
              before is not None and _on_screen(before, text.strip()) == (expect == "present"))
        for expect, texts in (("present", present), ("absent", absent)) for text in texts or [] if text.strip()
    ]
    untested = tuple(
        c for c in checks if fold(typed) and not c.met and c.expect == "present" and fold(c.text) == fold(typed)
    )
    kept = tuple(c for c in checks if c not in untested)
    d = diff(before, after)
    return Verdict(_verified(ok, kept, d, vision), kept, untested, d, vision)


def _verified(ok: bool, kept: tuple[Check, ...], d: Diff, vision: bool) -> Verified:
    if not ok:
        return "fail"
    if not kept:
        return "unknown"
    if not all(c.met for c in kept):
        return "unknown" if d.changed else "fail"
    # A check already met before the action proves nothing about it.
    proven = d.changed or any(c.expect == "present" and not c.met_before for c in kept)
    return "pass" if proven and not vision else "unknown"


def _locator_rungs(row: Row, snap: Snapshot) -> list[str]:
    # A loc that finds an input by its typed value will not match on a replay.
    value_loc = (
        "input" in row.act and row.name != row.label
        and bool(_VALUE_ATTR_RE.search(_unquoted(row.loc))) and _names_by_text(row.loc, row.label)
    )
    rungs = [row.loc] if row.loc and not value_loc else []
    # A native loc that is not unique by name means the rid is on other nodes too.
    rid_shared = bool(row.loc) and not row.web and not _names_one_element(row.loc)
    if row.rid and not rid_shared and sum(r.rid == row.rid for r in snap.all_rows) == 1:
        if row.web:
            rung = f"css=#{row.rid}" if _CSS_ID_RE.match(row.rid) else f'css=[id="{row.rid}"]'
        else:
            rung = f'//*[@resource-id="{row.rid}"]'
        if rung not in rungs and rung.removeprefix("css=") not in rungs:
            rungs.append(rung)
    return rungs


def _holds_secret(row: Row) -> bool:
    return row.secret and "input" in row.act


def _saved_rungs(row: Row, rungs: list[str]) -> list[str]:
    """``rungs`` with a stable one first, then the row's name as a text rung unless it is a secret input's own label."""
    rungs = list(rungs)
    stable = next((r for r in rungs[1:] if loc_stable(r)), None) if rungs and not loc_stable(rungs[0]) else None
    if stable:
        rungs.remove(stable)
        rungs.insert(0, stable)
    if row.name and not (row.name == row.label and _holds_secret(row)) and _text_rung(row.name) not in rungs:
        rungs.append(_text_rung(row.name))
    return rungs


def _no_locator(row: Row) -> TargetError:
    return TargetError(f"{row.ref} ({_describe(row)}) has no unique locator; observe again or pass a locator.",
                       kind="no_locator")


def _label_rung(row: Row, snap: Snapshot) -> str:
    """One rung from the label of a row the source gave no locator; never from a typed value."""
    if not row.label or row.name != row.label:
        raise _no_locator(row)
    same = [r for r in snap.all_rows if r.label == row.label]
    if len(same) == 1:
        return _text_rung(row.label)
    if not row.web:
        raise TargetError(f"{row.ref} has no unique locator; pick another row or pass a locator.", kind="no_locator")
    index = next(i for i, r in enumerate((r for r in same if r.cls == row.cls), 1) if r.ref == row.ref)
    return f"xpath=(//{row.cls}[normalize-space()={_xpath_literal(' '.join(row.label.split()))}])[{index}]"


def ladder(row: Row, snap: Snapshot, act_only: bool = False) -> Ladder:
    """The locators of ``row``; without a locator it acts by its label, unless ``act_only``."""
    live = _locator_rungs(row, snap)
    if not live and act_only:
        raise _no_locator(row)
    act = live or [_label_rung(row, snap)]
    save = _saved_rungs(row, live)
    if not live:
        save = [*act, *(r for r in save if r not in act)]
    unique = bool(row.label) and sum(r.label == row.label for r in snap.all_rows) == 1
    replay = save[-1] if unique and not loc_stable(save[0]) else save[0]
    return Ladder(act, save, not live, replay)


def _locator_key(locator: str) -> str:
    return "".join(locator.strip().removeprefix(_XPATH).removeprefix("css=").replace("'", '"').split())


def is_secret(row: Row | None, snap: Snapshot | None, raw: str = "") -> bool:
    """True when a value typed into ``row``, ``raw`` or, with neither, an unknown field must not be recorded."""
    if snap is not None:
        key = _locator_key(raw)
        if row is None and key:
            row = next(
                (r for r in snap.all_rows if key in {_locator_key(x) for x in (r.rid, *_locator_rungs(r, snap)) if x}),
                None,
            )
        if row is not None:
            unknown = "input" not in row.act
        else:
            unknown = not key or utils.determine_element_type(raw) in ("XPath", "CSS")
        # A value typed into no known input goes into the focused field, so any secret field on the screen counts.
        if unknown and any(_holds_secret(r) for r in snap.all_rows):
            return True
    return (row is not None and row.secret) or "SecureTextField" in raw or is_secret_name(raw)


def _text_rung(label: str) -> str:
    return label if utils.determine_element_type(label) == "Text" else f"text={label}"


def _xpath_literal(text: str) -> str:
    if '"' not in text:
        return f'"{text}"'
    if "'" not in text:
        return f"'{text}'"
    return "concat(" + ", '\"', ".join(f'"{p}"' for p in text.split('"')) + ")"


def recordable_present(present: Sequence[str], snap: Snapshot) -> tuple[list[str], list[str]]:
    """``present`` items as ``assert_presence`` elements, and notes for the items left out."""
    items: list[str] = []
    notes: list[str] = []
    for item in (raw.strip() for raw in present):
        if item:
            element, note = _recordable(item, snap)
            items += [element] if element else []
            notes += [note] if note else []
    return items, notes


def _recordable(item: str, snap: Snapshot) -> tuple[str, str]:
    """One ``present`` item as an element, or the note on why it is left out."""
    if REF_RE.match(item):
        row = snap.row(item)
        rungs = _saved_rungs(row, _locator_rungs(row, snap)) if row is not None else []
        if rungs:
            return rungs[0], ""
        why = "is not on the screen" if row is None else "has no locator or text"
        return "", f"{item} {why}, so it was not recorded"
    if utils.determine_element_type(item) != "Text":
        return item, ""
    text = fold(item.removeprefix("text="))
    row = next((r for r in snap.all_rows if r.label and fold(r.label) == text), None)
    if row is not None:
        return _text_rung(row.label), ""
    return "", f'"{item}" is not the whole text of a row, so it was not recorded'


def _snake_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text).lower())


def element_name(row: Row, taken: set[str]) -> str:
    """A unique snake_case element name such as ``sign_in_button``; never a secret input's own label."""
    own = "" if _holds_secret(row) and row.name == row.label else row.name
    words = [w for w in re.findall(r"[a-z0-9]+", own.lower()) if not w.isdigit()][:_NAME_MAX_WORDS]
    words = words or _snake_words(row.rid.rsplit("/", 1)[-1]) or _snake_words(row.hint)
    suffix = _snake_words(row.cls)
    if suffix and words[-len(suffix):] != suffix:
        words += suffix
    base = "_".join(words) or "element"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name


def clean_label(label: str, limit: int = 80) -> str:
    text = " ".join(label.split()).replace('"', "'")
    return text[:limit] + "…" if len(text) > limit else text


def _label_part(label: str) -> str:
    return f'"{clean_label(label)}"' if label.strip() else "(no text)"


def hide(text: str, secret: str) -> str:
    return text.replace(secret, REDACTED) if secret else text


def effect(d: Diff, secret: str) -> str:
    """What a step changed, for the NL history: relabelled rows first, then rows added and removed."""
    if d.new_screen:
        return "new screen"
    if not d.changed:
        return "screen unchanged"

    def shown(text: str) -> str:
        return f'"{clean_label(hide(text, secret), 40)}"'

    items = [f"{shown(old)}→{shown(row.label)}" for row, old in d.relabelled]
    items += [f"+{shown(r.label)}" for r in d.added if r.label] + [f"-{shown(r.label)}" for r in d.removed if r.label]
    rest = len(d.added) + len(d.removed) + len(d.relabelled) - len(items[:3])
    return "changed " + ", ".join([*items[:3], *([f"{rest} more"] if rest else [])])


def render_row(row: Row) -> str:
    act = f"[{','.join(row.act)}]" if row.act else ""
    rid = f"#{row.rid.rsplit('/', 1)[-1]}" if row.rid else ""
    return " ".join(p for p in (row.ref, row.cls, _label_part(row.label), act, rid) if p)


def _row_lines(snap: Snapshot) -> list[str]:
    lines = [render_row(r) for r in snap.rows]
    if snap.hidden:
        lines.append(f'… {snap.hidden} more rows hidden; observe(query="…") to find one')
    return lines


def render_rows(snap: Snapshot) -> str:
    count = len(snap.rows)
    return "\n".join([f"screen {snap.digest} · {count} row{'' if count == 1 else 's'}", *_row_lines(snap)])


def prompt_rows(snap: Snapshot) -> list[str]:
    """``snap`` for an LLM prompt: its rows, then a line for the rows left out."""
    lines = [render_rows(snap.showing(snap.rows, 0))]
    if snap.hidden:
        lines.append(f"… {snap.hidden} more rows not listed; `target` also takes an element's exact visible text.")
    return lines


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


def read_screen(action_keyword: Any, png: bytes | None = None) -> list[dict] | None:
    """Compact screen elements, each with unscaled ``window_bounds`` for ref identity, or None; E0106 if dead."""
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
        screenshot_np = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    utils.scale_interactive_element_bounds(elements, element_source, screenshot_np)
    return elements
