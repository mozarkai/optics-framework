"""The verbs an agent acts with, the keyword calls they map to, and error kinds with advice.

Shared by the MCP server and the live NL agent. Everything here is pure.
"""
import difflib
import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from optics_framework.common.screen_snapshot import Row, Snapshot

VERBS = ("tap", "type", "select", "scroll", "key", "open", "check", "keyword")
DIRECTIONS = ("up", "down", "left", "right")

# Keywords that act without locating a named target, so a pass does not mean the goal was
# reached: coordinate taps and swipes hit nothing and still pass, scroll/swipe/keycode/sleep
# always succeed, *_from_element locates only the gesture anchor, detect_and_press swallows
# "not found", and *_until_element_appears pass on timeout. A run of them is flailing.
NON_VERIFYING_KEYWORDS: tuple[str, ...] = (
    "press_by_percentage",
    "press_by_coordinates",
    "swipe",
    "swipe_by_percentage",
    "swipe_from_element",
    "scroll",
    "scroll_from_element",
    "press_keycode",
    "sleep",
    "detect_and_press",
    "scroll_until_element_appears",
    "swipe_until_element_appears",
    "enter_text_direct",
    "enter_text_using_keyboard",
)
HIDDEN_KEYWORDS = frozenset({
    "start_appium_session", "initialise_setup", "get_driver_session_id",
    "capture_screenshot", "capture_pagesource", "get_screen_elements", "get_interactive_elements",
})
COORDINATE_KEYWORDS = frozenset({"press_by_coordinates", "press_by_percentage", "swipe"})
TYPED_VALUE_PARAMS = {
    "enter_text": "text", "enter_number": "number", "enter_text_direct": "text", "enter_text_using_keyboard": "text_input",
}


def is_deprecated(fn: Callable[..., Any] | None) -> bool:
    return ".. deprecated::" in (getattr(fn, "__doc__", None) or "")
HIDDEN_PARAMS = ("event_name", "fail", "aoi_x", "aoi_y", "aoi_width", "aoi_height")

ADVICE: dict[str, str] = {
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
    "other": "Read the error, then try a different action.",
}

_CODE_KINDS = {
    "E0105": "unsupported",
    "E0101": "session_lost",
    "E0103": "session_lost",
    "E0106": "session_lost",
    "E0402": "invalid_params",
    "E0403": "invalid_params",
    "E0205": "invalid_params",
    "E0901": "invalid_params",
    "E0401": "silent_failure",
}
_FINGER = {"left": "right", "right": "left"}
_WEB_DRIVERS = frozenset({"selenium", "playwright"})
_TAPPABLE = frozenset({"tap", "long", "toggle", "input"})
_ELEMENT_SLOTS = ("element", "elements")
_TARGETED = frozenset({"tap", "type", "select"})

Value = str | list[str]
Params = Mapping[str, Value] | Sequence[str]
Keywords = Mapping[str, Callable[..., Any]]


@dataclass(frozen=True)
class KeywordCall:
    keyword: str
    params: dict[str, Value]
    element_param: str | None


def typed_value(calls: Sequence[KeywordCall]) -> str:
    """The text the calls type into a field, or "" when none of them types."""
    for call in calls:
        value = call.params.get(TYPED_VALUE_PARAMS.get(call.keyword, ""))
        if isinstance(value, list):
            value = value[0] if value else ""
        if value:
            return value
    return ""


def error_kind(code: Any) -> str:
    """The kind of an optics error code, such as ``not_found`` for ``E0201``."""
    value = str(getattr(code, "value", code) or "").removeprefix("Code.")
    if value in _CODE_KINDS:
        return _CODE_KINDS[value]
    if value.startswith("E02") or value == "X0201":
        return "not_found"
    return "other"


def param_names(func: Callable[..., Any] | None) -> list[str]:
    """Every param name of ``func`` in signature order, without ``self`` and variadics."""
    if func is None:
        return []
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return []
    variadic = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    return [p.name for p in params if p.name != "self" and p.kind not in variadic]


def runner_params(names: Sequence[str], values: Mapping[str, Any]) -> list[str]:
    """``values`` as a CSV step binds them: positional up to the first skipped param, then ``name=value``."""
    if not names:
        return [str(v) for v in values.values()]
    out: list[str] = []
    gap = False
    for name in names:
        if name not in values:
            gap = True
            continue
        out.append(f"{name}={values[name]}" if gap else str(values[name]))
    out += [f"{name}={value}" for name, value in values.items() if name not in names]
    return out


def visible_params(func: Callable[..., Any]) -> list[inspect.Parameter]:
    """The params an agent may pass to ``func``: no ``self``, hidden or variadic params."""
    variadic = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    return [
        p for p in inspect.signature(func).parameters.values()
        if p.name != "self" and p.name not in HIDDEN_PARAMS and p.kind not in variadic
    ]


def _element_slot(spec: list[inspect.Parameter]) -> str | None:
    return next((p.name for p in spec if p.name in _ELEMENT_SLOTS), None)


def _bind(
    spec: list[inspect.Parameter], slot: str | None, element: Value | None,
    params: Params | None, identities: set[str],
) -> dict[str, Value]:
    """Keyword params in signature order; raises ValueError naming what does not fit."""
    names = [p.name for p in spec]
    fill = element is not None and slot is not None
    given: dict[str, Value] = {}
    if isinstance(params, Mapping):
        unknown = [name for name in params if name not in names]
        if unknown:
            raise ValueError(f'no param "{unknown[0]}"')
        given = {k: v if isinstance(v, list) else str(v) for k, v in params.items()}
    elif params:
        free = [n for n in names if not (fill and n == slot)]
        values = [str(v) for v in params]
        if fill and values and values[0] in identities:
            values = values[1:]
        if len(values) > len(free):
            raise ValueError(f"too many params ({len(values)}); it takes {len(free)} besides the element")
        given = dict(zip(free, values))
    if fill:
        given[slot] = element
    missing = [p.name for p in spec if p.default is inspect.Parameter.empty and p.name not in given]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")
    return {n: given[n] for n in names if n in given}


def _keyword_hint(keyword: str, keywords: Keywords | None, row: Row | None,
                  raw_target: str, params: Params | None) -> str | None:
    if not keyword:
        return "keyword needs a keyword name"
    if keywords is None:
        return None
    visible = sorted(k for k, fn in keywords.items() if k not in HIDDEN_KEYWORDS and not is_deprecated(fn))
    if keyword not in visible:
        closest = difflib.get_close_matches(keyword, visible, n=3, cutoff=0.5)
        suggestion = f"; closest: {', '.join(closest)}" if closest else ""
        return f'unknown keyword "{keyword}"{suggestion}'
    spec = visible_params(keywords[keyword])
    element = raw_target.strip() if row is not None or raw_target.strip() else None
    try:
        _bind(spec, _element_slot(spec), element, params, _identities(row, raw_target))
    except ValueError as exc:
        return f"{keyword}: {exc}; params: {', '.join(p.name for p in spec) or 'none'}"
    return None


def _identities(row: Row | None, raw_target: str) -> set[str]:
    names = {raw_target.strip()}
    if row is not None:
        names |= {row.ref, row.label}
    return names - {""}


def _center(bounds: tuple[int, int, int, int]) -> tuple[float, float]:
    return (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2


def _input_rows(row: Row, snap: Snapshot | None) -> str:
    if snap is None:
        return ""
    inputs = [r for r in snap.rows if "input" in r.act]
    if not inputs:
        return "; no text field is on this screen"
    if row.bounds and all(r.bounds for r in inputs):
        x, y = _center(row.bounds)
        inputs.sort(key=lambda r: abs(_center(r.bounds)[0] - x) + abs(_center(r.bounds)[1] - y))
    listed = ", ".join(f'{r.ref} "{r.label}"' if r.label else r.ref for r in inputs[:3])
    return f"; text fields: {listed}"


def validate(
    verb: str, row: Row | None, raw_target: str = "", text: str = "", direction: str = "",
    until: str = "", *, present: Sequence[str] = (), absent: Sequence[str] = (),
    keyword: str = "", params: Params | None = None, keywords: Keywords | None = None,
    snap: Snapshot | None = None,
) -> str | None:
    """One hint that says what is wrong with the call, or None when it can run."""
    if verb not in VERBS:
        return f'unknown action "{verb}"; use one of {", ".join(VERBS)}'
    if verb in _TARGETED and row is None and not raw_target.strip():
        return f"{verb} needs a target"
    if verb == "tap" and row is not None and row.act and not _TAPPABLE & set(row.act):
        return f"{row.ref} cannot be tapped; it accepts {', '.join(row.act)}"
    if verb == "type":
        if not text:
            return "type needs the text to enter"
        if row is not None and "input" not in row.act:
            return f"{row.ref} is not a text field{_input_rows(row, snap)}"
    if verb == "select" and not text.strip():
        return "select needs the option to choose"
    if verb == "scroll":
        if direction not in DIRECTIONS:
            return f"scroll needs a direction: {', '.join(DIRECTIONS)}"
        if until.strip() and direction in _FINGER:
            return "until works with up/down only"
    if verb == "key" and not text.strip():
        return "key needs a key: back, or a driver key such as 66"
    if verb == "check" and not any(t.strip() for t in [*present, *absent]):
        return "check needs at least one text in present or absent"
    if verb == "keyword":
        return _keyword_hint(keyword, keywords, row, raw_target, params)
    return None


def plan_call(
    verb: str, row: Row | None, raw_target: str, *, text: str = "", direction: str = "",
    until: str = "", timeout_s: int = 10, repeat: int = 1, clear_first: bool = False,
    ladder: list[str] | None = None, present: Sequence[str] = (), absent: Sequence[str] = (),
    keyword: str = "", params: Params | None = None, keywords: Keywords | None = None,
    driver: str = "",
) -> list[KeywordCall]:
    """The keyword calls that carry out one verb, in order; ``check`` gives its recordable form.

    ``ladder`` replaces ``raw_target`` as the element when given. ``direction`` is the way the
    content moves: "down" reveals what is below. ``driver`` is the session's driver name
    (``appium``, ``selenium``, ``playwright``); web drivers scroll sideways with ``scroll``.
    """
    hint = validate(
        verb, row, raw_target, text, direction, until, present=present, absent=absent,
        keyword=keyword, params=params, keywords=keywords,
    )
    if hint:
        raise ValueError(hint)
    element: Value = list(ladder) if ladder else raw_target.strip()
    if verb == "tap":
        tap: dict[str, Value] = {"element": element}
        if repeat != 1:
            tap["repeat"] = str(repeat)
        return [KeywordCall("press_element", tap, "element")]
    if verb == "type":
        calls = [KeywordCall("clear_element_text", {"element": element}, "element")] if clear_first else []
        return [*calls, KeywordCall("enter_text", {"element": element, "text": text}, "element")]
    if verb == "select":
        return [KeywordCall("select_dropdown_option", {"element": element, "option": text}, "element")]
    if verb == "scroll":
        if until.strip():
            scroll_until = {"element": until.strip(), "direction": direction, "timeout": str(timeout_s)}
            return [KeywordCall("scroll_until_element_appears", scroll_until, "element")]
        if direction in _FINGER and driver not in _WEB_DRIVERS:
            swipe = {"percent_x": "50", "percent_y": "50", "direction": _FINGER[direction], "swipe_length": "50"}
            return [KeywordCall("swipe_by_percentage", swipe, None)]
        return [KeywordCall("scroll", {"direction": direction}, None)]
    if verb == "key":
        if text.strip().lower() == "back":
            return [KeywordCall("go_back", {}, None)]
        return [KeywordCall("press_keycode", {"keycode": text.strip()}, None)]
    if verb == "open":
        return [KeywordCall("launch_app", {"app_identifier": text.strip()} if text.strip() else {}, None)]
    if verb == "check":
        items = [t.strip() for t in present if t.strip()]
        return [KeywordCall("assert_presence", {"elements": "|".join(items), "rule": "all"}, None)] if items else []
    if keywords is None:
        raise ValueError("keyword needs the keyword catalog")
    spec = visible_params(keywords[keyword])
    slot = _element_slot(spec)
    has_target = row is not None or bool(raw_target.strip())
    bound = _bind(spec, slot, element if has_target else None, params, _identities(row, raw_target))
    return [KeywordCall(keyword, bound, slot if has_target and slot in bound else None)]
