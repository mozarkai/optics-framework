"""Appium's element actions on a TV web app (webOS, Tizen): D-pad focus walks to an element and ENTER selects it."""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from selenium.webdriver.common.keys import Keys  # type: ignore

from optics_framework.common import utils
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.logging_config import internal_logger, is_secret_value
from optics_framework.common.utils import SpecialKey

MAX_PRESSES = 60
MAX_CLEAR_PRESSES = 80
MAX_LAYERS = 3
MAX_BURST = 8
SETTLE_TIMEOUT_S = 2.0
KEYBOARD_WAIT_S = 5.0
POLL_S = 0.15
FIELD_TAGS = ("INPUT", "TEXTAREA")
SPACE_NAMES = ("space", "spacebar", "␣")
BACKSPACE = re.compile(r"back\s*space|⌫|(^|[-_])del(ete)?($|[-_])", re.IGNORECASE)
ENTER_KEY = re.compile(r"^(enter|search|done|go|submit)$|↵|⏎", re.IGNORECASE)
LAYER_KEY = re.compile(r"^[?&]?123$|^abc$|^#\+=$|^!#\$$|^sym(bols?)?$", re.IGNORECASE)
MASK_CHARS = frozenset("•●∙·*")
REMOTE_KEYS = {
    SpecialKey.ESCAPE: "BACK", SpecialKey.UP: "UP", SpecialKey.DOWN: "DOWN",
    SpecialKey.LEFT: "LEFT", SpecialKey.RIGHT: "RIGHT",
}
FIELD_KEYS = {
    SpecialKey.ENTER: Keys.ENTER, SpecialKey.TAB: Keys.TAB, SpecialKey.BACKSPACE: Keys.BACKSPACE,
    SpecialKey.SPACE: Keys.SPACE, SpecialKey.ESCAPE: Keys.ESCAPE, SpecialKey.UP: Keys.ARROW_UP,
    SpecialKey.DOWN: Keys.ARROW_DOWN, SpecialKey.LEFT: Keys.ARROW_LEFT, SpecialKey.RIGHT: Keys.ARROW_RIGHT,
}

# ES5 for webOS 4 (Chrome 53) and Tizen 5.5 (Chrome 69). Focus is data-focused, else the class names that moved
# on the last press (learned per component), else the DOM's own focus.
_FOCUS_JS = r"""
var target = arguments[0], markers = arguments[1] || [];
function cls(el) { var c = el.className; if (c && typeof c === 'object') c = c.baseVal; return String(c || ''); }
function classTokens(s) { return s.split(/\s+/).filter(function (t) { return t; }); }
function without(a, b) { return a.filter(function (t) { return b.indexOf(t) < 0; }); }
function rect(el) {
  for (; el && el.getBoundingClientRect; el = el.parentElement) {
    var r = el.getBoundingClientRect();
    if (r.width && r.height) return [r.left, r.top, r.width, r.height];
  }
  return null;
}
var all = document.getElementsByTagName('*'), gained = [], lost = [], i, j;
for (i = 0; i < all.length; i++) {
  var el = all[i], now = cls(el), before = el.__opticsClass;
  el.__opticsClass = now;
  if (before === undefined || before === now) continue;
  var g = without(classTokens(now), classTokens(before)), l = without(classTokens(before), classTokens(now));
  if (g.length) gained.push([el, g]);
  if (l.length) lost.push([el, l]);
}
// Focus is on one element at a time, so a focus marker is on one element only; an unfocused style moves too, but is on many.
function unique(m) { return m.length > 0 && document.getElementsByClassName(m.join(' ')).length === 1; }
var moved = [];
for (i = 0; i < gained.length; i++) for (j = 0; j < lost.length; j++) {
  if (gained[i][0] === lost[j][0] || gained[i][0].tagName !== lost[j][0].tagName) continue;
  var common = gained[i][1].filter(function (t) { return lost[j][1].indexOf(t) >= 0; });
  if (common.length > moved.length && unique(common)) moved = common;
}
// Components can style focus differently: when one element only lost names, the one that gained most has the focus.
function unpaired(a, b) { return a.filter(function (x) { return !b.some(function (y) { return y[0] === x[0]; }); }); }
var gainers = unpaired(gained, lost), losers = unpaired(lost, gained);
if (!moved.length && losers.length === 1) {
  for (i = 0; i < gainers.length; i++) if (gainers[i][1].length > moved.length && unique(gainers[i][1])) moved = gainers[i][1];
}
function within(a, b) { return a.every(function (t) { return b.indexOf(t) >= 0; }); }
// markers[0] is the focus marker: the one an element gained last. One that stays put (the selected page) never leads.
for (j = 0; !moved.length && j < markers.length; j++)
  for (i = 0; i < gained.length; i++) if (within(markers[j], gained[i][1]) && unique(markers[j])) { moved = markers[j]; break; }
if (moved.length) {
  var key = moved.join(' ');
  markers = [moved].concat(markers.filter(function (m) { return m.join(' ') !== key; }));
}
function hasMarker(el, marker) {
  return !!el.classList && within(marker, classTokens(cls(el)));
}
var focused = null, how = '';
var flagged = document.querySelectorAll('[data-focused]:not([data-focused="false"])');
for (i = flagged.length - 1; !focused && i >= 0; i--) if (rect(flagged[i])) { focused = flagged[i]; how = 'data-focused'; }
for (i = 0; !focused && markers.length && i < all.length; i++)
  if (hasMarker(all[i], markers[0]) && rect(all[i])) { focused = all[i]; how = 'class'; }
var active = document.activeElement;
if (!focused && active && active !== document.body && active !== document.documentElement) { focused = active; how = 'dom'; }
return {
  focused: focused ? rect(focused) : null, how: how, markers: markers,
  text: focused ? (focused.innerText || focused.value || '').trim().slice(0, 40) : '',
  on_target: !!focused && !!target && (focused === target || focused.contains(target) || target.contains(focused)),
  target: target ? rect(target) : null,
  centre: [window.innerWidth / 2, window.innerHeight / 2, 1, 1]
};
"""

# The key nearest the focus whose own text, label, title or id names the character (an icon-font key such as
# backspace can be named by a class only). The search stays near the on-screen keyboard: the deepest element
# that holds most single-character keys, when the screen shows at least six.
_KEY_JS = r"""
var want = arguments[0], names = arguments[1], from = arguments[2];
function own(el) {
  var t = '';
  for (var i = 0; i < el.childNodes.length; i++) if (el.childNodes[i].nodeType === 3) t += el.childNodes[i].nodeValue;
  return t.trim();
}
function shown(el) { var r = el.getBoundingClientRect(); return r.width && r.height ? r : null; }
function keyboard() {
  var all = document.getElementsByTagName('*'), keys = [], seen = [], counts = [], i;
  for (i = 0; i < all.length; i++) if (own(all[i]).length === 1 && shown(all[i])) keys.push(all[i]);
  if (keys.length < 6) return null;
  for (i = 0; i < keys.length; i++) for (var a = keys[i].parentElement; a; a = a.parentElement) {
    var n = seen.indexOf(a);
    if (n < 0) { seen.push(a); counts.push(1); } else counts[n]++;
  }
  var need = Math.max(6, Math.ceil(keys.length * 0.8)), best = null, depth = -1;
  for (i = 0; i < seen.length; i++) {
    if (counts[i] < need) continue;
    var d = 0;
    for (var p = seen[i]; p; p = p.parentElement) d++;
    if (d > depth) { depth = d; best = seen[i]; }
  }
  return best;
}
function label(el) {
  return [own(el), el.getAttribute('aria-label'), el.getAttribute('title'), el.id].filter(function (v) { return v; });
}
function classes(el) {
  var c = el.className;
  return String(c && typeof c === 'object' ? c.baseVal : c || '').split(/\s+/);
}
function matches(el) {
  var vals = want.classes ? label(el).concat(classes(el)) : label(el);
  for (var i = 0; i < vals.length; i++) {
    var v = String(vals[i]).trim();
    if (!want.exact) v = v.toLowerCase();
    if (names.indexOf(v) >= 0) return true;
    if (want.regex && new RegExp(want.regex, 'i').test(v)) return true;
  }
  return false;
}
function nearest(scope) {
  var best = null, bestD = Infinity, all = scope.getElementsByTagName('*');
  for (var i = 0; i < all.length; i++) {
    var el = all[i], r;
    if (!matches(el) || !(r = shown(el))) continue;
    var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    var d = from ? Math.pow(cx - from[0] - from[2] / 2, 2) + Math.pow(cy - from[1] - from[3] / 2, 2) : 0;
    if (d < bestD) { best = el; bestD = d; }
  }
  return best;
}
var board = keyboard(), scope = board || document, best = nearest(scope);
// A row of wide keys (space, 123) can sit beside the grid of letters, so look a little further out too.
for (var up = 0; board && !best && up < 2 && scope.parentElement; up++) { scope = scope.parentElement; best = nearest(scope); }
return [best, !!board];
"""

_FIELD_TEXT_JS = (
    "var e = arguments[0]; return e.tagName === 'INPUT' || e.tagName === 'TEXTAREA' ? e.value"
    " : (e.innerText || '').replace(/\\s+/g, ' ').trim();"
)

# The field a caption names: the element itself, the field its label is for, else the one field in its nearest
# container; an app-drawn field with none of these marks is the element itself.
_FIELD_FOR_JS = r"""
var el = arguments[0];
var FIELDS = 'input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]),'
  + 'textarea,[role=textbox],[contenteditable=""],[contenteditable=true]';
function isField(e) { return e.matches ? e.matches(FIELDS) : e.webkitMatchesSelector(FIELDS); }
if (isField(el)) return el;
if (el.tagName === 'LABEL' && el.htmlFor && document.getElementById(el.htmlFor)) return document.getElementById(el.htmlFor);
for (var a = el.parentElement; a && a !== document.body; a = a.parentElement) {
  var fields = a.querySelectorAll(FIELDS);
  if (fields.length === 1) return fields[0];
  if (fields.length > 1) break;
}
return el;
"""

Rect = tuple[float, float, float, float]
W3C_ELEMENT = "element-6066-11e4-a52e-4f735466cecf"


@dataclass(frozen=True)
class DpadFocus:
    rect: Rect | None
    on_target: bool
    target: Rect | None
    markers: tuple[tuple[str, ...], ...]
    text: str
    centre: Rect | None = None


def directions(focused: Rect, target: Rect) -> list[str]:
    """Remote keys that bring focus closer to ``target``, the axis with the larger gap first.

    Keys that touch have no gap on either axis, so the centre offset then picks the axis.
    """
    fl, ft, fw, fh = focused
    tl, tt, tw, th = target
    dx = tl + tw / 2 - (fl + fw / 2)
    dy = tt + th / 2 - (ft + fh / 2)
    axes = [
        (max(tl - (fl + fw), fl - (tl + tw), 0), abs(dx), "RIGHT" if dx > 0 else "LEFT"),
        (max(tt - (ft + fh), ft - (tt + th), 0), abs(dy), "DOWN" if dy > 0 else "UP"),
    ]
    needed = [a for a in axes if a[0] > 0] or [a for a in axes if a[1] > 0]
    return [key for _, _, key in sorted(needed, reverse=True)]


def _centre(rect: Rect, key: str) -> float:
    return rect[0] + rect[2] / 2 if key in ("LEFT", "RIGHT") else rect[1] + rect[3] / 2


@dataclass
class _Route:
    """What one focus walk learned: keys that did not move focus, the key that left known focus, and key spacing."""

    blocked: dict[Rect | None, set[str]] = field(default_factory=dict)
    visits: dict[Rect | None, int] = field(default_factory=dict)
    stride: dict[str, float] = field(default_factory=dict)
    left_known: str | None = None

    def next_key(self, state: DpadFocus) -> str | None:
        """A key toward the target, else a detour (or, with focus not known, any key that shows where it is)."""
        self.visits[state.rect] = self.visits.get(state.rect, 0) + 1
        if state.rect is not None and self.visits[state.rect] > 3:
            return None
        if state.rect is None and self.left_known:
            # The next move stays inside the component focus entered, so it shows that component's focus names.
            return self.left_known
        options = directions(state.rect or state.centre or state.target, state.target)
        options += [k for k in ("RIGHT", "DOWN", "LEFT", "UP") if k not in options]
        return next((k for k in options if k not in self.blocked.get(state.rect, set())), None)

    def presses(self, state: DpadFocus, key: str) -> int:
        """How often to press ``key`` before the next read: as many key spacings as fit the gap on that axis."""
        stride = self.stride.get(key)
        if not stride or state.rect is None or state.target is None or self.blocked.get(state.rect):
            return 1
        gap = abs(_centre(state.target, key) - _centre(state.rect, key))
        return max(1, min(MAX_BURST, round(gap / stride)))

    def record(self, before: DpadFocus, key: str, after: DpadFocus, count: int) -> None:
        if after.rect == before.rect:
            self.blocked.setdefault(before.rect, set()).add(key)
        elif count == 1 and before.rect is not None and after.rect is not None:
            self.stride[key] = abs(_centre(after.rect, key) - _centre(before.rect, key))
        if after.rect is not None:
            self.left_known = None
        elif before.rect is not None:
            self.left_known = key


class TvWebApp:
    """The element keywords on one TV session; remembers the class names the app's components mark focus with."""

    def __init__(self, script: Callable[..., Any], press: Callable[[str], None], event_sdk: Any) -> None:
        self._execute = script
        self._press = press
        self._events = event_sdk
        self._markers: tuple[tuple[str, ...], ...] = ()
        self._last: DpadFocus | None = None

    def press_element(self, element: Any, repeat: int, event_name: str | None = None) -> None:
        self.focus(element)
        timestamp = self._events.get_current_time_for_events()
        for _ in range(repeat):
            self._press("ENTER")
        if event_name:
            self._events.capture_event_with_time_input(event_name, timestamp)

    def enter_text_element(self, element: Any, text: str | SpecialKey, event_name: str | None = None) -> None:
        if event_name:
            self._events.capture_event(event_name)
        target = self._script(_FIELD_FOR_JS, element) or element
        if isinstance(text, SpecialKey):
            self._press_special(target, text)
        else:
            self._type(target, utils.strip_sensitive_prefix(text), secret=is_secret_value(text))

    def clear_text_element(self, element: Any, event_name: str | None = None) -> None:
        if event_name:
            self._events.capture_event(event_name)
        self._clear(self._script(_FIELD_FOR_JS, element) or element)

    def get_text_element(self, element: Any) -> str:
        return str(self._script(_FIELD_TEXT_JS, element) or "")

    def _script(self, js: str, *args: Any) -> Any:
        """The TVs' chromedrivers read only the legacy ``ELEMENT`` key, which Selenium no longer sends."""
        return self._execute(js, *(_element_ref(a) for a in args))

    def read(self, target: Any = None) -> DpadFocus:
        state = self._script(_FOCUS_JS, target, [list(m) for m in self._markers]) or {}
        self._markers = tuple(tuple(m) for m in state.get("markers") or ()) or self._markers
        self._last = DpadFocus(_rect(state.get("focused")), bool(state.get("on_target")), _rect(state.get("target")),
                               self._markers, str(state.get("text") or ""), _rect(state.get("centre")))
        return self._last

    def focus(self, target: Any, hide_labels: bool = False) -> None:
        """Move focus onto ``target`` (or an element that holds it); ``hide_labels`` keeps the labels it passes out of logs."""
        state = self.read(target)
        route = _Route()
        presses = 0
        while presses < MAX_PRESSES:
            if state.on_target:
                return
            if state.target is None:
                raise OpticsError(Code.E0201, message="The element is not shown on the screen, so focus cannot reach it")
            key = route.next_key(state)
            if key is None:
                break
            count = route.presses(state, key)
            internal_logger.debug(f"TV focus on '{_shown(state.text, hide_labels)}' at {state.rect}, pressing {key} x{count}")
            for _ in range(count):
                self._press(key)
            after = _settle(lambda: self.read(target), lambda now, was=state.rect: now.rect != was)
            route.record(state, key, after, count)
            state, presses = after, presses + count
        raise OpticsError(
            Code.E0201,
            message=f"D-pad focus did not reach the element; focus stays on '{_shown(state.text, hide_labels) or state.rect}'",
        )

    def _select(self, target: Any, hide_labels: bool = False) -> None:
        self.focus(target, hide_labels)
        self._press("ENTER")

    def _find_key(self, names: list[str], pattern: re.Pattern[str] | None = None, classes: bool = False,
                  exact: bool = False) -> tuple[Any, bool]:
        """The key nearest the focus, and whether the screen shows a keyboard."""
        want = {"regex": pattern.pattern if pattern else None, "classes": classes, "exact": exact}
        near = (self._last or self.read()).rect
        found = self._script(_KEY_JS, want, names, list(near) if near else None) or [None, False]
        return found[0], bool(found[1])

    def _char_key(self, char: str) -> tuple[Any, bool]:
        """The key that types ``char``, in the same letter case when the keyboard has it."""
        key, keyboard = self._find_key([char], exact=True)
        if key is None:
            key, keyboard = self._find_key([char.lower()] + (list(SPACE_NAMES) if char == " " else []))
        return key, keyboard

    def _key_for(self, char: str) -> Any:
        """The key for ``char``, waiting for the keyboard to open and switching its layers (``123``, ``abc``)."""
        key, _ = _settle(lambda: self._char_key(char), lambda found: found[0] is not None or found[1], KEYBOARD_WAIT_S)
        for _ in range(MAX_LAYERS):
            if key is not None:
                return key
            layer, _ = self._find_key([], LAYER_KEY)
            if layer is None:
                break
            # Focus may still rest on the key of the character typed before.
            self._select(layer, hide_labels=True)
            key, _ = self._char_key(char)
        if key is None:
            raise OpticsError(Code.E0201, message="No on-screen key types the next character")
        return key

    def _backspace(self) -> Any:
        key, _ = self._find_key([], BACKSPACE, classes=True)
        if key is None:
            raise OpticsError(Code.E0201, message="No backspace or delete key on the on-screen keyboard")
        return key

    def _type(self, target: Any, text: str, secret: bool) -> None:
        """A real input takes the keys; an app keyboard is walked key by key and the field read before and after."""
        if _tag(target) in FIELD_TAGS:
            target.send_keys(text)
            return
        before = self.get_text_element(target)
        started = time.monotonic()
        typed = 0
        try:
            for char in text:
                self.focus(self._key_for(char), hide_labels=True)
                typed += 1
                self._press("ENTER")
            shown = _settle(lambda: self.get_text_element(target), lambda now: _holds(now, before, text, fold=True))
        except Exception as e:
            # Once a key is selected, a retry by another strategy or fallback would type the text a second time.
            if typed == 0:
                raise
            raise OpticsError(Code.E0405, message=f"Typing stopped after {typed} of {len(text)} characters", cause=e) from e
        internal_logger.debug(f"Typed {len(text)} characters in {time.monotonic() - started:.1f} s")
        if _holds(shown, before, text):
            return
        if not _holds(shown, before, text, fold=True):
            raise OpticsError(Code.E0405, message=_typing_mismatch(shown, before, text, secret))
        # Some app keyboards have one set of letters and let the app pick the case (a capital first letter).
        values = "" if secret else f": asked '{text}', the field shows '{shown}'"
        internal_logger.warning(f"The field shows the text in another letter case{values}")

    def _clear(self, target: Any) -> None:
        """A real input is cleared; an app keyboard's backspace is pressed until the text stops changing."""
        if _tag(target) in FIELD_TAGS:
            target.clear()
            return
        self.focus(self._backspace())
        original = shown = self.get_text_element(target)
        deletes = 0
        while deletes < MAX_CLEAR_PRESSES:
            self._press("ENTER")
            now = _settle(lambda: self.get_text_element(target), lambda text, last=shown: text != last)
            if now == shown:
                break
            shown, deletes = now, deletes + 1
        else:
            raise OpticsError(Code.E0405, message=f"The field still has text after {MAX_CLEAR_PRESSES} deletes")
        if deletes == 0 and original:
            internal_logger.warning("Backspace did not change the field: it is empty, or not the field the keyboard writes to")
        # Deletes that stop early leave the text less one letter each; a placeholder shown once it is empty does not.
        if 0 < deletes < len(original) and shown == original[: len(original) - deletes]:
            raise OpticsError(Code.E0405, message="Deletes stopped with part of the text still in the field")

    def _press_special(self, target: Any, key: SpecialKey) -> None:
        """A real input takes the key; on an app keyboard space, backspace and enter are its keys, the rest remote keys."""
        if _tag(target) in FIELD_TAGS and key in FIELD_KEYS:
            target.send_keys(FIELD_KEYS[key])
        elif key is SpecialKey.SPACE:
            self._select(self._key_for(" "))
        elif key is SpecialKey.BACKSPACE:
            self._select(self._backspace())
        elif key is SpecialKey.ENTER:
            # The remote's ENTER selects the focused key again, which on a keyboard types the last character.
            enter, _ = self._find_key([], ENTER_KEY)
            if enter is None:
                raise OpticsError(Code.E0201, message="No enter or search key on the on-screen keyboard")
            self._select(enter)
        elif key in REMOTE_KEYS:
            self._press(REMOTE_KEYS[key])
        else:
            raise OpticsError(Code.E0105, message=f"A TV remote and an app keyboard have no '{key.value}' key")


def _settle[T](read: Callable[[], T], done: Callable[[T], bool], timeout: float = SETTLE_TIMEOUT_S) -> T:
    """The app redraws after the remote command returns, so read until ``done`` or ``timeout`` ends."""
    deadline = time.monotonic() + timeout
    value = read()
    while not done(value) and time.monotonic() < deadline:
        time.sleep(POLL_S)
        value = read()
    return value


def _bare(text: str) -> str:
    return "".join(text.split())


def _masked(text: str) -> bool:
    return bool(text) and set(_bare(text)) <= MASK_CHARS


def _holds(shown: str, before: str, text: str, fold: bool = False) -> bool:
    """Whether the field shows ``text`` added to its old value or to a placeholder it dropped, as text or as masks."""
    if shown == before:
        return False
    if fold:
        shown, before, text = shown.casefold(), before.casefold(), text.casefold()
    if _masked(shown):
        held = len(_bare(before)) if _masked(before) else 0
        return len(_bare(shown)) - held == len(text)
    # Fields drop a trailing space until the next character comes, so spaces are left out of the comparison.
    return _bare(shown) in (_bare(before + text), _bare(text))


def _typing_mismatch(shown: str, before: str, text: str, secret: bool) -> str:
    if shown == before:
        return "The field did not change; press the field first when the screen has more than one"
    if secret:
        return "The field does not show the typed value; press the field first when the screen has more than one"
    return f"The field shows '{shown}', not '{text}'; press the field first when the screen has more than one"


def _shown(text: str, hidden: bool) -> str:
    return "****" if hidden and text else text


def _element_ref(value: Any) -> Any:
    element_id = getattr(value, "id", None)
    return {"ELEMENT": element_id, W3C_ELEMENT: element_id} if isinstance(element_id, str) else value


def _tag(element: Any) -> str:
    return str(getattr(element, "tag_name", "") or "").upper()


def _rect(value: Any) -> Rect | None:
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3])) if value else None
