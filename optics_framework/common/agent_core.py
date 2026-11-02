"""One agent pipeline for every front end: prepare a verb, run its keywords, settle, judge and record."""
import asyncio
import csv
import difflib
import inspect
import io
import math
import re
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from optics_framework.common import utils
from optics_framework.common.error import OpticsError
from optics_framework.common.logging_config import is_secret_name
from optics_framework.common.runner.data_reader import DataReader
from optics_framework.common.screen_snapshot import (
    REDACTED,
    REF_RE,
    Check,
    Ladder,
    RefTable,
    Row,
    Snapshot,
    TargetError,
    Verdict,
    element_name,
    is_secret,
    ladder,
    recordable_present,
    verify,
)

VERBS = ("tap", "type", "select", "scroll", "key", "open", "check", "keyword")
DIRECTIONS = ("up", "down", "left", "right")
# Keywords that pass without proving the goal was reached; a run of them is flailing.
NON_VERIFYING_KEYWORDS = (
    "press_by_percentage", "press_by_coordinates", "swipe", "swipe_by_percentage", "swipe_from_element",
    "scroll", "scroll_from_element", "press_keycode", "sleep", "detect_and_press",
    "scroll_until_element_appears", "swipe_until_element_appears",
    "enter_text_direct", "enter_text_using_keyboard",
)
# FlowControl is left out: its keywords need the runner's module and data context.
KEYWORD_CLASSES = ("ActionKeyword", "AppManagement", "Verifier")
RESOURCE_KEYWORDS = frozenset({"capture_screenshot", "capture_pagesource", "get_screen_elements"})
HIDDEN_KEYWORDS = RESOURCE_KEYWORDS | {
    "start_appium_session", "initialise_setup", "get_driver_session_id", "get_interactive_elements",
}
COORDINATE_TAPS = frozenset({"press_by_coordinates", "press_by_percentage"})
COORDINATE_KEYWORDS = COORDINATE_TAPS | {"swipe"}
TYPED_VALUE_PARAMS = {
    "enter_text": "text", "enter_number": "number", "enter_text_direct": "text", "enter_text_using_keyboard": "text_input",
}
VALUE_PARAMS = {**TYPED_VALUE_PARAMS, "assert_text": "expected"}
_FOCUSING = frozenset({"press_element", "clear_element_text", "select_dropdown_option", *TYPED_VALUE_PARAMS})
HIDDEN_PARAMS = ("event_name", "fail", "aoi_x", "aoi_y", "aoi_width", "aoi_height")
ADVICE = {
    "not_found": "Pick a ref from the current screen.",
    "unsupported": "This platform does not support it; use another keyword.",
    "session_lost": "The driver session ended; start a new session.",
    "invalid_params": "Fix the call as the hint says.",
    "silent_failure": "The action may not have happened; read the screen before you go on.",
    "stale_ref": "Pick a ref from the current screen.",
    "unknown_ref": "Observe the screen, then use a ref from it.",
    "ambiguous": "Use one of the listed refs.",
    "no_locator": "Pass a locator for it, or pick another row.",
    "timeout": "The device may still be busy; wait, then observe the screen.",
    "busy": "The previous action is still running; retry shortly.",
    "unknown_session": "Start a session.",
    "loop": "Call observe, then try a different target or approach.",
    "other": "Read the error, then try a different action.",
}
_CODE_KINDS = {
    "X0201": "not_found", "E0105": "unsupported", "E0101": "session_lost", "E0103": "session_lost",
    "E0106": "session_lost", "E0402": "invalid_params", "E0403": "invalid_params", "E0205": "invalid_params",
    "E0901": "invalid_params", "E0401": "silent_failure",
}
_FATAL_KINDS = frozenset({"session_lost", "unknown_session"})
_READ_ONLY_PREFIXES = ("get_", "assert_", "validate_")
_UNGUARDED_KEYWORDS = frozenset({"press_keycode", "sleep"})
_TAPPABLE = frozenset({"tap", "long", "toggle", "input"})
ELEMENT_SLOTS = ("element", "elements")
_TARGETED = frozenset({"tap", "type", "select"})
_TIMEOUT_PARAMS = ("timeout", "timeout_str")
_LOCATOR_TYPES = frozenset({"Image", "CSS", "XPath"})
_NO_CHECK = "check needs at least one text in present or absent"
KEYWORD_TIMEOUT_MAX = 60
ERROR_ROWS = 40
UNREADABLE = "The screen could not be read."
_POLL_S = 0.5
_SETTLE_PAUSE_S = 0.6
_SETTLE_READS = 2
VAR_RE = re.compile(r"\$\{([^}]*)\}")

Value = str | list[str]
Params = Mapping[str, Value] | Sequence[str]
Keywords = Mapping[str, Callable[..., Any]]
Read = Callable[[bytes | None], Awaitable[list[dict] | None]]
Run = Callable[[str, dict[str, Value]], Awaitable[tuple[Any, str | None]]]


class Fail(Exception):
    """A call that cannot go on; ``kind`` is an ``ADVICE`` key and ``message`` the error text as raised."""

    def __init__(
        self, kind: str, fact: str, *, code: str | None = None, message: str = "", rows: Snapshot | None = None
    ) -> None:
        super().__init__(fact)
        self.kind, self.code, self.rows = kind, code, rows
        self.message = message or fact


@dataclass(frozen=True)
class Action:
    """One verb an agent asks for; ``params`` is a name map or a list of CSV-style tokens."""
    verb: str
    target: str = ""
    text: str = ""
    direction: str = ""
    until: str = ""
    timeout_s: int = 10
    repeat: int = 1
    clear_first: bool = False
    keyword: str = ""
    params: Params = ()
    present: Sequence[str] = ()
    absent: Sequence[str] = ()


@dataclass(frozen=True)
class Call:
    keyword: str
    params: dict[str, Value]


@dataclass(frozen=True)
class Prepared:
    """A validated action: its keyword calls and the rows its element params name."""
    action: Action
    calls: tuple[Call, ...]
    targets: dict[str, tuple[Row, Ladder]] = field(default_factory=dict)
    tried: tuple[str, ...] = ()

    @property
    def row(self) -> Row | None:
        return next((row for row, _ in self.targets.values()), None)

    @property
    def exact(self) -> bool:
        return any(not found.by_label for _, found in self.targets.values())


@dataclass(frozen=True)
class Step:
    """One acting call or check as it ran; ``key`` is its attempt key."""
    action: Action
    ok: bool
    verdict: Verdict
    before: Snapshot | None = None
    after: Snapshot | None = None
    prepared: Prepared | None = None
    result: Any = None
    strategy: str | None = None
    error: Fail | None = None
    key: tuple[str, str, str] | None = None
    typed: str = ""
    secret: bool = False

    @property
    def unchanged(self) -> bool:
        return self.verdict.diff is not None and not self.verdict.diff.changed

    @property
    def progress(self) -> bool:
        return self.ok and (self.verdict.verified == "pass" or not self.unchanged)


def error_kind(code: str | None) -> str:
    code = code or ""
    return _CODE_KINDS.get(code) or ("not_found" if code.startswith("E02") else "other")


def error_parts(exc: BaseException) -> tuple[str | None, str]:
    """The code and message of an OpticsError, or of the HTTP error that wraps one."""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        code, message = detail.get("code"), str(detail.get("message") or detail.get("detail") or detail)
    elif detail is not None:
        code, message = None, str(detail)
    else:
        code, message = getattr(exc, "code", None), getattr(exc, "message", None) or str(exc)
    return (str(getattr(code, "value", code)).removeprefix("Code.") if code else None), message


def as_fail(exc: BaseException, keyword: str = "", tried: Sequence[str] = ()) -> Fail:
    """``exc`` as a Fail whose text names what ``keyword`` tried."""
    if isinstance(exc, Fail):
        return exc
    code, message = error_parts(exc)
    kind = error_kind(code)
    facts = {
        "not_found": f"not found: tried {' | '.join(tried) or keyword}.",
        "unsupported": f"{keyword} is not supported on this platform.",
        "session_lost": "the driver session ended.",
        "silent_failure": f"the keyword logged an error and did not raise: {message}.",
    }
    return Fail(kind, facts.get(kind, message), code=code, message=message)


def _invalid(hint: str) -> Fail:
    return Fail("invalid_params", hint)


def is_read_only(keyword: str) -> bool:
    return keyword.startswith(_READ_ONLY_PREFIXES) or keyword == "is_element"


def keyword_catalog(keywords: Keywords, coordinates: bool = False) -> dict[str, Callable[..., Any]]:
    """The keywords an agent may call: API keywords that are not hidden or deprecated; coordinate ones on request."""
    return {
        name: fn for name, fn in keywords.items()
        if fn.__qualname__.partition(".")[0] in KEYWORD_CLASSES and name not in HIDDEN_KEYWORDS
        and ".. deprecated::" not in (fn.__doc__ or "") and (coordinates or name not in COORDINATE_KEYWORDS)
    }


def keyword_params(fn: Callable[..., Any]) -> list[inspect.Parameter]:
    variadic = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    return [p for p in inspect.signature(fn).parameters.values() if p.name != "self" and p.kind not in variadic]


def visible_params(fn: Callable[..., Any]) -> list[inspect.Parameter]:
    return [p for p in keyword_params(fn) if p.name not in HIDDEN_PARAMS]


def element_slot(spec: Sequence[inspect.Parameter]) -> str | None:
    return next((p.name for p in spec if p.name in ELEMENT_SLOTS), None)


def _given_by_name(
    fn: Callable[..., Any], params: Mapping[str, Any], free: list[inspect.Parameter],
) -> dict[str, Value]:
    unknown = [key for key in params if key not in {p.name for p in free}]
    if unknown:
        raise ValueError(f'{fn.__name__} has no param "{unknown[0]}"')
    return {k: v if isinstance(v, list) else str(v) for k, v in params.items()}


def _given_by_position(fn: Callable[..., Any], tokens: list[str], free: list[inspect.Parameter]) -> dict[str, Value]:
    positional, keywords = DataReader.split_params_by_signature(fn, tokens)
    if len(positional) > len(free):
        raise ValueError(f"Too many params for {fn.__name__}")
    try:
        return dict(inspect.Signature(free).bind_partial(*positional, **keywords).arguments)
    except TypeError as exc:
        raise ValueError(f"{fn.__name__}: {exc}") from exc


def bind(
    fn: Callable[..., Any], params: Params, element: Value | None = None, drop_first: Collection[str] = (),
    hidden: bool = False,
) -> dict[str, Value]:
    """``params`` bound as the CSV runner binds them to the visible params of ``fn``, or to all with ``hidden``."""
    spec = keyword_params(fn) if hidden else visible_params(fn)
    slot = element_slot(spec) if element is not None else None
    free = [p for p in spec if p.name != slot]
    if isinstance(params, Mapping):
        given = _given_by_name(fn, params, free)
    else:
        tokens = [str(t) for t in params]
        if slot and tokens and tokens[0] in drop_first:
            tokens = tokens[1:]
        given = _given_by_position(fn, tokens, free)
    if slot:
        given[slot] = element
    missing = [p.name for p in spec if p.default is inspect.Parameter.empty and p.name not in given]
    if missing:
        raise ValueError(f"{fn.__name__} needs {', '.join(missing)}")
    return {p.name: given[p.name] for p in spec if p.name in given}


def runner_params(fn: Callable[..., Any] | None, values: Mapping[str, Any]) -> list[str]:
    """``values`` as a CSV step: positional while the runner reads each back as the same param, then ``name=value``."""
    if fn is None:
        return [str(v) for v in values.values()]
    names = [p.name for p in keyword_params(fn)]
    out: list[str] = []
    named = False
    for name in names:
        if name not in values:
            named = True
            continue
        token = str(values[name])
        if not named:
            positional, _ = DataReader.split_params_by_signature(fn, [*out, token])
            named = len(positional) != len(out) + 1
        out.append(f"{name}={token}" if named else token)
    return out + [f"{name}={value}" for name, value in values.items() if name not in names]


def secret_element(
    elements: dict[str, list[str]], secrets: set[str], hint: str, value: str, taken: Collection[str] = (),
) -> str:
    """The secret element that holds ``value``: one made before, else a new ``<hint>_value`` or ``secret_value``."""
    name = next((s for s in secrets if elements.get(s) == [value]), "")
    if not name:
        taken = {*taken, *elements}
        name = element_name(Row("", f"{hint} value", "", ()), taken)
        if not is_secret_name(name):
            name = element_name(Row("", "secret value", "", ()), taken)
        elements[name] = [value]
        secrets.add(name)
    return name


def cut(snap: Snapshot, rows: int = ERROR_ROWS) -> Snapshot:
    return snap.showing(snap.rows[:rows], snap.hidden + max(len(snap.rows) - rows, 0))


def _value(calls: Sequence[Call], table: Mapping[str, str]) -> str:
    values = (c.params.get(table.get(c.keyword, "")) for c in calls)
    return next((v for v in values if isinstance(v, str) and v), "")


def _is_locator(item: str) -> bool:
    return utils.determine_element_type(item.strip()) in _LOCATOR_TYPES


def _target(refs: RefTable, target: str, act_only: bool = False) -> tuple[Row, Ladder] | None:
    """The row ``target`` names with its ladder, or None for a raw target; a bad ref raises Fail."""
    try:
        row = refs.resolve(target)
        return None if row is None else (row, ladder(row, refs.latest, act_only))
    except TargetError as exc:
        rows = cut(refs.latest) if exc.kind == "stale_ref" and refs.latest else None
        raise Fail(exc.kind, str(exc), rows=rows) from exc


def _clamp(timeout: str) -> str:
    try:
        return str(min(float(timeout), KEYWORD_TIMEOUT_MAX)).removesuffix(".0")
    except ValueError:
        return timeout


def _expand(params: Mapping[str, Value], refs: RefTable) -> tuple[dict[str, Value], dict[str, tuple[Row, Ladder]]]:
    out: dict[str, Value] = {}
    targets: dict[str, tuple[Row, Ladder]] = {}
    for name, value in params.items():
        if name in ELEMENT_SLOTS and isinstance(value, str) and REF_RE.match(value.strip()):
            targets[name] = _target(refs, value, act_only=True)
            out[name] = list(targets[name][1].act)
        else:
            out[name] = _clamp(value) if name in _TIMEOUT_PARAMS and isinstance(value, str) else value
    return out, targets


def _center(bounds: tuple[int, int, int, int]) -> tuple[float, float]:
    return (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2


def _input_rows(row: Row, snap: Snapshot | None) -> str:
    """Up to 3 text fields on the screen, nearest to ``row`` first."""
    if snap is None:
        return ""
    inputs = [r for r in snap.rows if "input" in r.act]
    if not inputs:
        return "; no text field is on this screen"
    if row.bounds and all(r.bounds for r in inputs):
        here = _center(row.bounds)
        inputs.sort(key=lambda r: sum(abs(a - b) for a, b in zip(_center(r.bounds), here)))
    return "; text fields: " + ", ".join(f'{r.ref} "{r.label}"' if r.label else r.ref for r in inputs[:3])


def _check_vars(action: Action, elements: Mapping[str, Any] | None) -> None:
    if elements is None:
        return
    params = action.params.values() if isinstance(action.params, Mapping) else action.params
    for value in (action.target, action.text, action.until, *params):
        for name in VAR_RE.findall(str(value)):
            if name.strip() not in elements:
                raise _invalid(f"${{{name}}} is not a named element; named elements: {', '.join(elements) or 'none'}")


def _keyword_call(
    action: Action, refs: RefTable, keywords: Keywords, found: tuple[Row, Ladder] | None,
) -> tuple[Call, dict[str, tuple[Row, Ladder]]]:
    fn = keywords.get(action.keyword)
    if fn is None:
        closest = difflib.get_close_matches(action.keyword, sorted(keywords), n=3, cutoff=0.5)
        hint = f'unknown keyword "{action.keyword}"' + (f"; closest: {', '.join(closest)}" if closest else "")
        raise _invalid(hint if action.keyword else "keyword needs a keyword name")
    params, targets = _expand(action.params, refs) if isinstance(action.params, Mapping) else (action.params, {})
    element: Value | None = list(found[1].act) if found else action.target.strip() or None
    names = {action.target.strip(), *((found[0].ref, found[0].label) if found else ())} - {""}
    spec = visible_params(fn)
    try:
        bound = bind(fn, params, element, drop_first=names)
    except ValueError as exc:
        listed = ", ".join(p.name if p.default is inspect.Parameter.empty else f"{p.name}?" for p in spec)
        raise _invalid(f"{exc}\nParams: {listed or 'none'}") from exc
    slot = element_slot(spec)
    if found and slot in bound:
        targets[slot] = found
    return Call(action.keyword, bound), targets


def _tap_calls(action: Action, row: Row | None, element: Value, refs: RefTable) -> list[Call]:
    if row is not None and row.act and not _TAPPABLE & set(row.act):
        raise _invalid(f"{row.ref} cannot be tapped; it accepts {', '.join(row.act)}")
    repeat = {"repeat": str(action.repeat)} if action.repeat != 1 else {}
    return [Call("press_element", {"element": element} | repeat)]


def _type_calls(action: Action, row: Row | None, element: Value, refs: RefTable) -> list[Call]:
    if not action.text:
        raise _invalid("type needs the text to enter")
    if row is not None and "input" not in row.act:
        raise _invalid(f"{row.ref} is not a text field{_input_rows(row, refs.latest)}")
    calls = [Call("clear_element_text", {"element": element})] if action.clear_first else []
    return [*calls, Call("enter_text", {"element": element, "text": action.text})]


def _select_calls(action: Action, row: Row | None, element: Value, refs: RefTable) -> list[Call]:
    if not action.text.strip():
        raise _invalid("select needs the option to choose")
    return [Call("select_dropdown_option", {"element": element, "option": action.text})]


def _scroll_calls(action: Action, row: Row | None, element: Value, refs: RefTable) -> list[Call]:
    direction, until = action.direction, action.until.strip()
    if direction not in DIRECTIONS:
        raise _invalid(f"scroll needs a direction: {', '.join(DIRECTIONS)}")
    if not until:
        return [Call("scroll", {"direction": direction})]
    if direction in ("left", "right"):
        raise _invalid("until works with up/down only")
    scroll = {"element": until, "direction": direction, "timeout": str(action.timeout_s)}
    return [Call("scroll_until_element_appears", scroll)]


def _key_calls(action: Action, row: Row | None, element: Value, refs: RefTable) -> list[Call]:
    text = action.text.strip()
    if not text:
        raise _invalid("key needs a key: back, or a driver key such as 66")
    return [Call("go_back", {}) if text.lower() == "back" else Call("press_keycode", {"keycode": text})]


def _open_calls(action: Action, row: Row | None, element: Value, refs: RefTable) -> list[Call]:
    text = action.text.strip()
    return [Call("launch_app", {"app_identifier": text} if text else {})]


def _check_calls(action: Action, row: Row | None, element: Value, refs: RefTable) -> list[Call]:
    if not any(t.strip() for t in [*action.present, *action.absent]):
        raise _invalid(_NO_CHECK)
    return []


_CALLS: dict[str, Callable[[Action, Row | None, Value, RefTable], list[Call]]] = {
    "tap": _tap_calls, "type": _type_calls, "select": _select_calls, "scroll": _scroll_calls,
    "key": _key_calls, "open": _open_calls, "check": _check_calls,
}


def prepare(action: Action, refs: RefTable, keywords: Keywords, elements: Mapping[str, Any] | None = None) -> Prepared:
    """Resolve the action's target and build its keyword calls; with ``elements``, each ``${name}`` must name one."""
    verb = action.verb
    if verb not in VERBS:
        raise _invalid(f'unknown action "{verb}"; use one of {", ".join(VERBS)}')
    if verb in _TARGETED and not action.target.strip():
        raise _invalid(f"{verb} needs a target")
    _check_vars(action, elements)
    found = _target(refs, action.target) if verb in (*_TARGETED, "keyword") else None
    row = found[0] if found else None
    element: Value = list(found[1].act) if found else action.target.strip()
    targets = {"element": found} if found and verb in _TARGETED else {}
    if verb == "keyword":
        call, targets = _keyword_call(action, refs, keywords, found)
        calls = [call]
    else:
        calls = _CALLS[verb](action, row, element, refs)
    named = next((v for c in calls for n, v in c.params.items() if n in ELEMENT_SLOTS), None)
    tried: tuple[str, ...] = ()
    if verb != "scroll" and named:
        tried = tuple(named) if isinstance(named, list) else (named,)
    return Prepared(action, tuple(calls), targets, tried)


def attempt_key(p: Prepared) -> tuple[str, str, str] | None:
    """What makes two acting calls the same attempt; None when the rows cannot show its progress."""
    a, anchor = p.action, p.tried[0] if p.tried else ""
    if a.verb in ("key", "check"):
        return None
    if a.verb == "keyword":
        if is_read_only(a.keyword) or a.keyword in _UNGUARDED_KEYWORDS:
            return None
        return f"keyword:{a.keyword}", anchor, repr(sorted(p.calls[0].params.items()))
    return a.verb, anchor, f"{a.direction} {a.until}" if a.verb == "scroll" else a.text


def _write_csv(header: list[str], rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows([utils.escape_csv_value(str(v)) for v in row] for row in rows)
    return buffer.getvalue()


def _var(name: str) -> str:
    return "${" + name + "}"


def _title(keyword: str) -> str:
    return " ".join(word.capitalize() for word in keyword.split("_") if word)


class Recorder:
    """Successful steps as CSV-runner rows by step number; ``elements`` keep real values and the CSV redacts secrets."""

    def __init__(self, keywords: Keywords, existing: Mapping[str, Sequence[str]] | None = None) -> None:
        self.keywords = keywords
        self.existing = {} if existing is None else existing
        self.elements: dict[str, list[str]] = {}
        self.secrets: set[str] = set()
        self._rows: list[tuple[int, str, list[str]]] = []
        self._notes: list[tuple[int, str]] = []

    @property
    def steps(self) -> list[tuple[str, list[str]]]:
        return [(keyword, tokens) for _, keyword, tokens in self._rows]

    @property
    def notes(self) -> list[str]:
        return list(dict.fromkeys(note for _, note in self._notes))

    def _literal(self, value: str) -> str:
        """``value`` with each ``${name}`` that names no element escaped, so a replay types it as written."""
        known = {*self.existing, *self.elements}
        return VAR_RE.sub(lambda m: m.group(0) if m.group(1).strip() in known else "$" + m.group(0), value)

    def _element(self, row: Row, found: Ladder) -> str:
        for name, values in [*self.existing.items(), *self.elements.items()]:
            if values and values[0] == found.save[0] and name not in self.secrets:
                return name
        name = element_name(row, {*self.existing, *self.elements})
        self.elements[name] = list(found.save)
        return name

    def add(self, n: int, p: Prepared, secret: bool = False, hint: str = "") -> None:
        """Record the calls of step ``n``; with ``secret``, the value they write goes into a secret element."""
        for call in p.calls:
            values: dict[str, str] = {}
            for name, value in call.params.items():
                if name in p.targets:
                    values[name] = _var(self._element(*p.targets[name]))
                elif secret and name == VALUE_PARAMS.get(call.keyword):
                    held = secret_element(self.elements, self.secrets, hint, value, self.existing)
                    self._notes.append((n, (
                        f"replace {REDACTED} for {held} before running: "
                        f"write it as @:value or set it with Read Data,{_var(held)},ENV:NAME"
                    )))
                    values[name] = _var(held)
                else:
                    values[name] = self._literal(value)
            self._rows.append((n, call.keyword, runner_params(self.keywords.get(call.keyword), values)))

    def add_check(self, n: int, present: Sequence[str], absent: Sequence[str], snap: Snapshot) -> None:
        """Record a passed check as ``assert_presence``; what has no keyword form becomes a note."""
        items, notes = recordable_present(present, snap)
        if absent:
            notes.append("absent checks are not recordable as keywords")
        self._notes += [(n, note) for note in notes]
        if items:
            values = {"elements": self._literal("|".join(items)), "rule": "all"}
            self._rows.append((n, "assert_presence", runner_params(self.keywords.get("assert_presence"), values)))

    def keep(self, numbers: Collection[int]) -> None:
        """Keep only the rows of steps ``numbers`` and the elements they still use."""
        self._rows = [r for r in self._rows if r[0] in numbers]
        self._notes = [note for note in self._notes if note[0] in numbers]
        used = {name.strip() for _, _, tokens in self._rows for token in tokens for name in VAR_RE.findall(token)}
        self.elements = {name: values for name, values in self.elements.items() if name in used}
        self.secrets &= used

    def csv(self) -> tuple[str, str]:
        """The modules CSV (one module, ``recorded``) and the elements CSV, secret values ``<redacted>``."""
        modules = [["recorded", _title(keyword), *tokens] for _, keyword, tokens in self._rows]
        elements = [[name, *([REDACTED] if name in self.secrets else values)] for name, values in self.elements.items()]
        id_width = max([1, *(len(r) - 1 for r in elements)])
        param_width = max([2, *(len(r) - 2 for r in modules)])
        return (
            _write_csv(["module_name", "module_step", *(f"param_{i}" for i in range(1, param_width + 1))], modules),
            _write_csv(["Element_Name", "Element_ID", *(f"Element_ID_{i}" for i in range(2, id_width + 1))], elements),
        )


Field = tuple[Row | None, Snapshot | None, tuple[str, ...]]


class Focus:
    """Where a value typed without an element goes: the field the last step tapped or typed into, else unknown."""

    def __init__(self) -> None:
        self.field: Field | None = None

    def into(self, named: Field | None, screen: Callable[[], Snapshot | None]) -> Field:
        """The field a step's value goes into, the focused one judged on ``screen``; unknown stands for every field."""
        if named:
            return named
        row, _, raws = self.field or (None, None, ("",))
        return row, screen(), raws

    def moved(self, keyword: str, named: Field | None) -> None:
        """After a step ran: the field it tapped or typed into, kept by typing without one, else unknown."""
        if named and keyword in _FOCUSING:
            self.field = named
        elif keyword not in TYPED_VALUE_PARAMS:
            self.field = None


def typed_secret(field: Field, value: str, elements: Collection[str] = ()) -> bool:
    """True when ``value`` goes into a secret field and is not a ``${name}`` the caller fills from ``elements``."""
    var = VAR_RE.fullmatch(value.strip())
    if not value or (var is not None and var.group(1).strip() in elements):
        return False
    return any(is_secret(field[0], field[1], raw) for raw in field[2])


def secret_hint(field: Field) -> str:
    """The word of ``field`` that names it as secret, used to name the element that holds its value."""
    row = field[0]
    words = (row.name, row.label, row.rid.rsplit("/", 1)[-1], row.hint) if row is not None else ()
    names = (raw for raw in field[2] if not _is_locator(raw))
    return next((word for word in (*words, *names) if is_secret_name(word)), "")


class AgentSession:
    """One agent's screen, steps and recording; ``read`` and ``run`` reach the driver and every raise is a Fail."""

    def __init__(
        self, read: Read, run: Run, keywords: Keywords, elements: Mapping[str, Sequence[str]] | None = None,
        loop_limit: int | None = None,
    ) -> None:
        self.read, self.run, self.keywords, self.elements = read, run, keywords, elements
        self.loop_limit = loop_limit
        self.refs = RefTable()
        self.steps: list[Step] = []
        self.recorder = Recorder(keywords, elements)
        self._fresh = 0
        self._focus = Focus()

    async def _read(self, max_rows: int = 80, query: str = "", png: bytes | None = None) -> Snapshot | None:
        if self.refs.latest is None:
            self._focus = Focus()  # a keyword run outside this session cleared the screen and may have moved focus
        try:
            elements = await self.read(png)
        except OpticsError as exc:
            raise as_fail(exc) from exc
        return None if elements is None else self.refs.snapshot(elements, max_rows=max_rows, query=query)

    async def _settle(self, digest: str) -> Snapshot | None:
        """Read until the screen differs from ``digest`` and two reads in a row agree."""
        snap = await self._read()
        for _ in range(_SETTLE_READS):
            if snap is None:
                return None
            await asyncio.sleep(_SETTLE_PAUSE_S)
            last, snap = snap, await self._read()
            if snap is not None and last.digest != digest and snap.digest == last.digest:
                break
        return snap

    async def _run(
        self, keyword: str, params: dict[str, Value], tried: Sequence[str],
    ) -> tuple[Any, str | None, Fail | None]:
        """Run one keyword: its result, winning strategy and failure; a lost session raises at once."""
        try:
            result, strategy = await self.run(keyword, params)
        except Exception as exc:
            error = as_fail(exc, keyword, tried)
            if error.kind in _FATAL_KINDS:
                raise error from exc
            return None, None, error
        return result, strategy, None

    async def observe(self, query: str = "", max_rows: int = 80, png: bytes | None = None) -> Snapshot:
        snap = await self._read(max_rows, query, png)
        if snap is None:
            raise Fail("other", UNREADABLE)
        self._fresh = len(self.steps)
        return snap

    def expand_refs(self, params: Mapping[str, Value]) -> dict[str, Value]:
        return _expand(params, self.refs)[0]

    def repeats(self, key: tuple[str, str, str] | None) -> int:
        """How many earlier steps made the same attempt and left the screen unchanged."""
        return sum(1 for s in self.steps if key is not None and s.key == key and s.ok and s.unchanged)

    def trailing(self, pred: Callable[[Step], bool], since: int = 0) -> int:
        steps = self.steps[since:]
        return next((i for i, step in enumerate(reversed(steps)) if not pred(step)), len(steps))

    def _guard_loop(self, p: Prepared) -> tuple[str, str, str] | None:
        """The step's attempt key; raises ``loop`` when the same attempt ran too often without progress."""
        key = attempt_key(p)
        if key is not None and self.loop_limit:
            runs = self.trailing(lambda s: s.key == key and not s.progress, self._fresh)
            if runs >= self.loop_limit:
                raise Fail("loop", f"ran {runs} times in a row without progress; not run again.")
        return key

    async def _run_calls(self, p: Prepared) -> tuple[Any, str | None, Fail | None]:
        result, strategy, error = None, None, None
        for call in p.calls:
            result, won, error = await self._run(call.keyword, call.params, p.tried)
            if error is not None:
                break
            strategy = won or strategy
        return result, strategy, error

    async def _poll(self, texts: list[str], absent: list[str], deadline: float) -> tuple[Snapshot, list[Check]]:
        """Read until every text check is met or ``deadline`` passes."""
        while True:
            snap = await self._read()
            checks = list(verify(True, None, snap, texts, absent).checks) if snap is not None else []
            left = deadline - time.monotonic()
            if (snap is not None and all(c.met for c in checks)) or left <= 0:
                break
            await asyncio.sleep(min(_POLL_S, left))
        if snap is None:
            raise Fail("other", UNREADABLE)
        return snap, checks

    async def act(self, action: Action) -> Step:
        """Prepare, guard, run, settle, judge and record one action; a failed keyword is a step, not a raise."""
        before = self.refs.latest or await self._read()
        p = prepare(action, self.refs, self.keywords, self.elements)
        key = self._guard_loop(p)
        named = None
        if p.tried:
            named = (p.row, before, ("",) if p.row else p.tried)
        into = self._focus.into(named, lambda: before)
        written = _value(p.calls, VALUE_PARAMS)
        secret = typed_secret(into, written, self.elements or ())
        result, strategy, error = await self._run_calls(p)
        typed = _value(p.calls, TYPED_VALUE_PARAMS)
        if error is not None:
            after = await self._read(ERROR_ROWS)
        elif typed:
            after = await self._read()
        else:
            after = await self._settle(before.digest if before else "")
        ok = error is None
        verdict = verify(
            ok, before, after, action.present, action.absent, typed=typed, strategy=strategy, exact=p.exact,
        )
        step = Step(action, ok, verdict, before, after, p, result, strategy, error, key, typed, secret)
        if ok:
            self._focus.moved(p.calls[-1].keyword if p.calls else "", named)
            self.recorder.add(len(self.steps) + 1, p, secret, secret_hint(into))
        self.steps.append(step)
        return step

    async def check(
        self, present: Sequence[str], absent: Sequence[str] = (), timeout_s: float = 5, record: bool = True,
    ) -> Step:
        """Poll texts and refs until all are met or ``timeout_s`` passes, then give each locator one look."""
        present = [p.strip() for p in present if p.strip()]
        absent = [a.strip() for a in absent if a.strip()]
        if not present and not absent:
            raise _invalid(_NO_CHECK)
        bad = next((a for a in absent if _is_locator(a)), None)
        if bad:
            raise _invalid(f'absent "{bad}" is a locator; use visible text or a ref')
        texts = [p.removeprefix("text=") for p in present if not _is_locator(p)]
        deadline = time.monotonic() + timeout_s
        snap, checks = await self._poll(texts, absent, deadline)
        for item in filter(_is_locator, present):
            remaining = str(max(1, math.ceil(deadline - time.monotonic())))
            look = {"elements": item, "timeout_str": remaining, "rule": "any", "fail": "False"}
            found, _, _ = await self._run("assert_presence", look, (item,))
            checks.append(Check(item, "present", bool(found)))
        passed = all(c.met for c in checks)
        verdict = Verdict("pass" if passed else "fail", tuple(checks), (), None, False)
        step = Step(Action("check", present=tuple(present), absent=tuple(absent)), passed, verdict, after=snap)
        if record:
            if passed:
                self.recorder.add_check(len(self.steps) + 1, present, absent, snap)
            self.steps.append(step)
        self._fresh = len(self.steps)
        return step
