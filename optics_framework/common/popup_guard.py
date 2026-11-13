"""What a step does about a popup on screen: match it to a rule, act, confirm it is gone, and say what happened.

The guard is built from plain callables, so it runs against fake screens with no device.
"""
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from lxml import etree  # type: ignore[import-untyped]

from optics_framework.common.error import Code, OpticsError
from optics_framework.common.error_detection import MOBILE_TEXT_ATTRS
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.models import PopupDetect, PopupRule

GONE_TIMEOUT = 5.0
GONE_POLL = 0.5
TEXT_LIMIT = 200

# Nodes that only a dialog or sheet has; a class name is no help because the dialog's own views are ordinary ones.
_ANDROID_DIALOG_IDS = ("alertTitle", "parentPanel", "button1", "design_bottom_sheet")
_ANDROID_SYSTEM_PACKAGES = (
    "com.android.permissioncontroller", "com.google.android.permissioncontroller",
    "com.android.packageinstaller", "com.google.android.packageinstaller",
)
_IOS_ALERT_TAGS = ("XCUIElementTypeAlert", "XCUIElementTypeSheet", "XCUIElementTypePopover")
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class Verdict(str, Enum):
    HANDLED = "handled"
    STOP = "stopped"
    LIMIT = "limit"
    UNKNOWN_POPUP = "unknown"
    NONE = "none"


COUNTED = (Verdict.HANDLED, Verdict.LIMIT, Verdict.STOP)


@dataclass
class Outcome:
    verdict: Verdict
    code: Optional[Code] = None
    stop_run: bool = False
    message: str = ""

    def error(self) -> OpticsError:
        return OpticsError(self.code or Code.E0208, message=self.message, details={"stop_run": self.stop_run})


NO_POPUP = Outcome(Verdict.NONE)


@dataclass
class PopupIO:
    """What the guard needs from the device and the output folder."""
    read_source: Callable[[], Optional[str]]
    tap: Callable[[str], bool]
    tap_at: Callable[[int, int], None]
    back: Callable[[], None]
    alert: Callable[[str], None]
    read_alert: Callable[[], Optional[Tuple[str, List[str]]]]
    screenshot: Callable[[], Any]
    save_image: Callable[[Any, str], Optional[str]]
    save_text: Callable[[str, str], Optional[str]]


@dataclass
class PopupRun:
    """The popups of one run. The runner owns it, publishes its events and prints its summary at the end."""
    counts: Dict[str, Counter] = field(default_factory=dict)
    attempts: Counter = field(default_factory=Counter)
    unknown: List[dict] = field(default_factory=list)
    events: List[dict] = field(default_factory=list)
    stop: Optional[str] = None
    inspections: int = 0

    def count(self, rule: str, verdict: Verdict = Verdict.HANDLED) -> int:
        return self.counts.get(rule, Counter())[verdict.value]

    def drain(self) -> List[dict]:
        events, self.events = self.events, []
        return events

    def summary_lines(self) -> List[str]:
        lines = [
            f"Popup '{name}': " + ", ".join(f"{v.value} {c[v.value]}" for v in COUNTED)
            for name, c in self.counts.items()
        ]
        lines += [f"Unknown popup: {u['popup_text']!r} at {u['keyword']} {u['element']}" for u in self.unknown]
        return lines


class _Screen:
    """One page source, read once: its visible text, what a rule can match and whether a dialog is on it."""

    def __init__(self, raw: Optional[str]):
        self.raw = raw or ""
        self.root = None
        if raw:
            try:
                self.root = etree.fromstring(raw.encode("utf-8"))
            except Exception as e:  # noqa: BLE001 - an unreadable source is a screen with nothing on it
                internal_logger.debug("Popup guard: page source is not XML: %s", e)
        self.nodes = [n for n in self.root.iter() if isinstance(n.tag, str)] if self.root is not None else []

    def add_alert(self, text: str, buttons: Sequence[str]) -> None:
        """Show a system alert on this screen. iOS leaves it out of the page source, so it is read through the driver."""
        if self.root is None:
            self.root = etree.Element("AppiumAUT")
        alert = etree.SubElement(self.root, "XCUIElementTypeAlert", name=text, label=text)
        for button in buttons:
            etree.SubElement(alert, "XCUIElementTypeButton", name=button, label=button)
        self.nodes = [n for n in self.root.iter() if isinstance(n.tag, str)]
        self.raw = etree.tostring(self.root, encoding="unicode")

    @staticmethod
    def _texts(nodes) -> List[str]:
        seen: Dict[str, None] = {}
        for node in nodes:
            for attr in MOBILE_TEXT_ATTRS:
                value = " ".join((node.get(attr) or "").split())
                if value:
                    seen.setdefault(value)
        return list(seen)

    @property
    def text(self) -> str:
        return " ".join(self._texts(self.nodes))

    @property
    def platform(self) -> Optional[str]:
        if self.root is None:
            return None
        return "ios" if self.root.tag == "AppiumAUT" or self.nodes[0].tag.startswith("XCUIElementType") else "android"

    @property
    def app_id(self) -> Optional[str]:
        """The app under test: the first Android package that is not the system's, or the iOS application's name."""
        for node in self.nodes:
            if node.tag == "XCUIElementTypeApplication":
                return node.get("name")
            package = node.get("package")
            if package and package not in _ANDROID_SYSTEM_PACKAGES:
                return package
        return None

    def dialog(self):
        """The node a dialog, sheet or alert hangs from, or None."""
        for node in self.nodes:
            rid = node.get("resource-id") or ""
            if (
                node.tag in _IOS_ALERT_TAGS
                or node.get("package") in _ANDROID_SYSTEM_PACKAGES
                or rid.rsplit("/", 1)[-1].rsplit(":", 1)[-1] in _ANDROID_DIALOG_IDS
            ):
                return node
        return None

    def kind(self) -> str:
        dialog = self.dialog()
        system = dialog is not None and (dialog.tag == "XCUIElementTypeAlert" or dialog.get("package") in _ANDROID_SYSTEM_PACKAGES)
        return "system_alert" if system else "in_app"

    def popup_text(self) -> str:
        dialog = self.dialog()
        return " ".join(self._texts(dialog.iter() if dialog is not None else self.nodes))[:TEXT_LIMIT]

    def point_of(self, target: str) -> Optional[Tuple[int, int]]:
        """The centre of the node ``target`` names (an xpath, a resource-id or its visible text), from its own bounds."""
        node = self._node_of(target)
        if node is None:
            return None
        bounds = _BOUNDS.fullmatch(node.get("bounds") or "")
        if bounds:
            x1, y1, x2, y2 = map(int, bounds.groups())
            return (x1 + x2) // 2, (y1 + y2) // 2
        try:
            x, y, width, height = (int(float(node.get(a))) for a in ("x", "y", "width", "height"))
        except (TypeError, ValueError):
            return None
        return x + width // 2, y + height // 2

    def _node_of(self, target: str):
        if self.root is None:
            return None
        if target.startswith(("//", "(", "xpath=")):
            try:
                found = self.root.xpath(target.removeprefix("xpath="))
            except etree.XPathError:
                return None
            return next((n for n in found if hasattr(n, "get")), None)
        wanted = " ".join(target.removeprefix("text=").split()).casefold()
        for node in self.nodes:
            if node.get("resource-id") == target or any(
                " ".join((node.get(a) or "").split()).casefold() == wanted for a in MOBILE_TEXT_ATTRS
            ):
                return node
        return None

    def matches(self, detect: PopupDetect) -> Optional[List[str]]:
        """The detect keys that matched when every one given did, else None."""
        found: List[str] = []
        if detect.text_contains:
            lowered = self.text.lower()
            if not any(t.lower() in lowered for t in detect.text_contains):
                return None
            found.append("text_contains")
        if detect.resource_id:
            if not any(n.get("resource-id") == detect.resource_id for n in self.nodes):
                return None
            found.append("resource_id")
        if detect.xpath:
            try:
                hit = bool(self.root.xpath(detect.xpath)) if self.root is not None else False
            except etree.XPathError as e:
                internal_logger.debug("Popup guard: xpath %r is not valid: %s", detect.xpath, e)
                hit = False
            if not hit:
                return None
            found.append("xpath")
        return found


class PopupGuard:
    def __init__(
        self,
        rules: Callable[[], Sequence[PopupRule]],
        io: PopupIO,
        run: Callable[[], PopupRun],
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._rules, self._io, self._run = rules, io, run
        self._sleep, self._clock = sleep, clock
        self._step: dict = {}
        self._step_counts: Counter = Counter()
        self._screen: Optional[_Screen] = None

    @property
    def active(self) -> bool:
        return bool(list(self._rules()))

    def begin_step(self, keyword: str, element: Any) -> None:
        self._step = {"keyword": keyword, "element": str(element)}
        self._step_counts = Counter()
        self._screen = None

    def before_step(self) -> Outcome:
        """Try the rules that opt in to the check before each step. One page-source read, none without such a rule."""
        rules = [r for r in self._rules() if r.check == "before_every_step"]
        return self._inspect(rules, "before_every_step", None) if rules else NO_POPUP

    def on_failure(self, screenshot: Any = None) -> Outcome:
        """Try every rule after the lookup failed. ``screenshot`` is the step's own, kept as the before image."""
        return self._inspect(list(self._rules()), "on_failure", screenshot, may_be_unknown=True)

    def _read(self) -> _Screen:
        if self._screen is None:
            try:
                screen = _Screen(self._io.read_source())
            except Exception as e:  # noqa: BLE001 - a failed read must not replace the step's own error
                internal_logger.debug("Popup guard: page source unavailable: %s", e)
                screen = _Screen(None)
            try:
                alert = self._io.read_alert() if screen.platform != "android" else None
                if alert:
                    screen.add_alert(*alert)
            except Exception as e:  # noqa: BLE001 - a driver may not offer the read
                internal_logger.debug("Popup guard: no system alert read: %s", e)
            self._screen = screen
        return self._screen

    def _inspect(self, rules: List[PopupRule], check: str, screenshot: Any, may_be_unknown: bool = False) -> Outcome:
        screen = self._read()
        for rule in sorted(rules, key=lambda r: r.priority):
            matched = screen.matches(rule.detect)
            if matched is not None:
                return self._handle(rule, screen, check, matched, screenshot)
            internal_logger.debug("Popup guard: rule '%s' did not match the screen", rule.name)
        if may_be_unknown and screen.dialog() is not None:
            return self._unknown(screen, check, screenshot)
        self._event(check, Verdict.NONE.value, screen=screen)
        return NO_POPUP

    def _handle(self, rule: PopupRule, screen: _Screen, check: str, matched: List[str], screenshot: Any) -> Outcome:
        run = self._run()
        step_n, run_n = self._step_counts[rule.name] + 1, run.attempts[rule.name] + 1
        end = partial(self._end, screen=screen, check=check, rule=rule, matched=matched)
        before = self._save(screenshot if screenshot is not None else self._io.screenshot(), "before")
        if rule.sensitive or (rule.then == "stop_run" and rule.action.type == "none"):
            return end(
                Verdict.STOP, Code.E0208, f"Popup '{rule.name}' ended the run; nothing was pressed",
                stop_run=True, step_count=step_n, run_count=run_n, images=[before], source=True,
            )
        if step_n > rule.max_per_step or run_n > rule.max_per_run:
            return end(
                Verdict.STOP, Code.E0207,
                f"Popup '{rule.name}' came back too often (step {step_n - 1}/{rule.max_per_step}, "
                f"run {run_n - 1}/{rule.max_per_run})",
                step_count=step_n - 1, run_count=run_n - 1, images=[before],
            )
        self._step_counts[rule.name] = step_n
        run.attempts[rule.name] += 1
        started = self._clock()
        acted = self._act(rule, screen)
        self._screen = None
        stop, described = rule.then == "stop_run", self._describe(rule)
        acting = partial(end, step_count=step_n, run_count=run_n, action=described)
        if not acted or not self._gone(rule):
            return acting(
                Verdict.STOP, Code.E0208, f"Popup '{rule.name}' is still on screen after '{described}'",
                stop_run=stop, images=[before, self._save(self._io.screenshot(), "after")], source=True, gone=False,
            )
        gone_ms = int((self._clock() - started) * 1000)
        after = [before, self._save(self._io.screenshot(), "after")]
        if rule.then == "retry_step":
            return acting(Verdict.HANDLED, None, "", images=after, gone_ms=gone_ms, gone=True)
        return acting(
            Verdict.STOP, Code.E0208, f"Popup '{rule.name}' was dismissed and then ended the {'run' if stop else 'step'}",
            stop_run=stop, images=after, gone_ms=gone_ms, gone=True,
        )

    def _act(self, rule: PopupRule, screen: _Screen) -> bool:
        action = rule.action
        try:
            if action.type == "back":
                self._io.back()
            elif action.type == "alert":
                self._io.alert(action.target or "")
            elif action.type == "tap":
                return any(self._tap(screen, t) for t in (action.target, action.fallback) if t)
            return action.type != "none"
        except Exception as e:  # noqa: BLE001 - an action that fails leaves the popup up, which the caller reports
            internal_logger.info("Popup guard: '%s' action failed: %s", rule.name, e)
            return False

    def _tap(self, screen: _Screen, target: str) -> bool:
        """Tap where the page source already says ``target`` is; only if it does not, ask the locate ladder (OCR, image)."""
        point = screen.point_of(target)
        if point is None:
            return self._io.tap(target)
        self._io.tap_at(*point)
        return True

    def _gone(self, rule: PopupRule) -> bool:
        """True once the popup is off the screen; waits up to ``GONE_TIMEOUT`` for it to go."""
        deadline = self._clock() + GONE_TIMEOUT
        while True:
            self._screen = None
            if self._read().matches(rule.detect) is None:
                self._screen = None
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(GONE_POLL)

    @staticmethod
    def _describe(rule: PopupRule) -> str:
        return f"{rule.action.type} {rule.action.target or ''}".strip()

    def _unknown(self, screen: _Screen, check: str, screenshot: Any) -> Outcome:
        image = self._save(screenshot if screenshot is not None else self._io.screenshot(), "before")
        text = screen.popup_text()
        outcome = self._end(
            Verdict.UNKNOWN_POPUP, Code.E0206, f"A popup with no rule is on screen: {text!r}",
            screen=screen, check=check, images=[image], source=True, text=text,
        )
        self._run().unknown.append({**self._step, "popup_text": text})
        return outcome

    def _save(self, image: Any, name: str) -> Optional[str]:
        run = self._run()
        return self._io.save_image(image, f"popup_{run.inspections + 1}_{name}") if image is not None else None

    def _end(
        self, verdict: Verdict, code: Optional[Code], message: str, *, screen: _Screen, check: str,
        rule: Optional[PopupRule] = None, matched: Optional[List[str]] = None, stop_run: bool = False,
        step_count: int = 0, run_count: int = 0, action: str = "", images: Sequence[Optional[str]] = (),
        source: bool = False, gone_ms: Optional[int] = None, gone: Optional[bool] = None, text: Optional[str] = None,
    ) -> Outcome:
        run = self._run()
        name = Verdict.LIMIT if code == Code.E0207 else verdict
        if rule is not None and name in COUNTED:
            run.counts.setdefault(rule.name, Counter())[name.value] += 1
        saved_source = self._io.save_text(screen.raw, f"popup_{run.inspections + 1}_source") if source else None
        if stop_run:
            run.stop = message
        self._event(
            check, name.value, screen=screen, rule=rule, matched=matched, action=action, popup_gone=gone,
            dismissed_in_ms=gone_ms, step_count=step_count, run_count=run_count, screenshots=[i for i in images if i],
            page_source=saved_source, code=code.value if code else None, popup_text=text,
        )
        internal_logger.info(
            "Popup %s: rule=%s action=%s%s", name.value, rule.name if rule else "-", action or "-",
            f" in {gone_ms}ms" if gone_ms is not None else "",
        )
        return Outcome(verdict, code, stop_run, message)

    def _event(self, check: str, verdict: str, *, screen: _Screen, rule: Optional[PopupRule] = None, **fields: Any) -> None:
        run = self._run()
        run.inspections += 1
        run.events.append({
            "type": "popup", **self._step, "platform": screen.platform, "app_id": screen.app_id, "check": check,
            "rule": rule.name if rule else None, "priority": rule.priority if rule else None,
            "matched": fields.pop("matched", None) or [],
            "popup_kind": screen.kind() if verdict != Verdict.NONE else None,
            "popup_text": fields.pop("popup_text", None) or (screen.popup_text() if verdict != Verdict.NONE else None),
            "verdict": verdict,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **fields,
        })
