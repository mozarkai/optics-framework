"""What a step does about a popup on screen: match it to a rule, act, confirm it is gone, and say what happened.

The guard is built from plain callables, so it runs against fake screens with no device.
"""
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from pydantic import BaseModel

from optics_framework.common.error import Code, OpticsError
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.models import PopupRule
from optics_framework.common.popup_screen import PopupScreen

GONE_TIMEOUT = 5.0
GONE_POLL = 0.5



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
        return OpticsError(self.code or Code.E0408, message=self.message, details={"stop_run": self.stop_run})


NO_POPUP = Outcome(Verdict.NONE)


@dataclass
class PopupIO:
    """What the guard needs from the device and the output folder."""
    read_source: Callable[[], Optional[str]]
    screen_of: Callable[[Optional[str]], PopupScreen]
    tap: Callable[[str], bool]
    tap_at: Callable[[int, int], None]
    back: Callable[[], None]
    alert: Callable[[str], None]
    read_alert: Callable[[], Optional[Tuple[str, List[str]]]]
    screenshot: Callable[[], Any]
    save_image: Callable[[Any, str], Optional[str]]
    save_text: Callable[[str, str], Optional[str]]


class PopupEvent(BaseModel):
    """One inspection for a popup: what the runner, the report and App Studio read."""
    type: str = "popup"
    keyword: Optional[str] = None
    element: Optional[str] = None
    platform: Optional[str] = None
    app_id: Optional[str] = None
    check: str
    rule: Optional[str] = None
    matched: List[str] = []
    popup_kind: Optional[str] = None
    popup_text: Optional[str] = None
    popup_title: Optional[str] = None
    verdict: str
    timestamp: str
    action: str = ""
    popup_gone: Optional[bool] = None
    dismissed_in_ms: Optional[int] = None
    step_count: int = 0
    run_count: int = 0
    screenshots: List[str] = []
    page_source: Optional[str] = None
    code: Optional[str] = None
    stop_run: bool = False


@dataclass
class Evidence:
    """What a popup inspection saw and did, beyond which rule matched."""
    step_count: int = 0
    run_count: int = 0
    action: str = ""
    images: Sequence[Optional[str]] = ()
    source: bool = False
    gone_ms: Optional[int] = None
    gone: Optional[bool] = None
    text: Optional[str] = None


@dataclass
class PopupRun:
    """The popups of one run. The runner owns it, publishes its events and prints its summary at the end."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
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
        self._screen: Optional[PopupScreen] = None

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
        return self._inspect(rules, "before_every_step") if rules else NO_POPUP

    def on_failure(self) -> Outcome:
        """Try every rule after the lookup failed. The page source is read again, because a popup may have come up
        during the lookup."""
        self._screen = None
        return self._inspect(list(self._rules()), "on_failure", may_be_unknown=True)

    def _read(self) -> PopupScreen:
        if self._screen is None:
            try:
                source = self._io.read_source()
            except Exception as e:  # noqa: BLE001 - a failed read must not replace the step's own error
                internal_logger.debug("Popup guard: page source unavailable: %s", e)
                source = None
            screen = self._io.screen_of(source)
            try:
                alert = self._io.read_alert() if screen.alerts_outside_source else None
                if alert:
                    screen.add_alert(*alert)
            except Exception as e:  # noqa: BLE001 - a driver may not offer the read
                internal_logger.debug("Popup guard: no system alert read: %s", e)
            self._screen = screen
        return self._screen

    def _inspect(self, rules: List[PopupRule], check: str, may_be_unknown: bool = False) -> Outcome:
        screen = self._read()
        for rule in rules:
            matched = screen.matches(rule.detect)
            if matched is not None:
                return self._handle(rule, screen, check, matched)
            internal_logger.debug("Popup guard: rule '%s' did not match the screen (%s)", rule.name, screen.missed)
        if may_be_unknown and screen.has_dialog():
            return self._unknown(screen, check)
        self._event(check, Verdict.NONE.value, screen=screen)
        return NO_POPUP

    def _handle(self, rule: PopupRule, screen: PopupScreen, check: str, matched: List[str]) -> Outcome:
        run = self._run()
        step_n, run_n = self._step_counts[rule.name] + 1, run.attempts[rule.name] + 1
        end = partial(self._end, screen=screen, check=check, rule=rule, matched=matched)
        before = self._save(self._io.screenshot(), "before")
        seen = Evidence(step_count=step_n, run_count=run_n, images=[before])
        if rule.sensitive or rule.action.type == "none":
            ends = rule.sensitive or rule.then == "stop_run"
            return end(
                Verdict.STOP, Code.E0408, f"Popup '{rule.name}' ended the {'run' if ends else 'step'}; nothing was pressed",
                stop_run=ends, evidence=replace(seen, source=True),
            )
        if step_n > rule.max_per_step or run_n > rule.max_per_run:
            return end(
                Verdict.STOP, Code.E0407,
                f"Popup '{rule.name}' came back too often (step {step_n - 1}/{rule.max_per_step}, "
                f"run {run_n - 1}/{rule.max_per_run})",
                evidence=replace(seen, step_count=step_n - 1, run_count=run_n - 1),
            )
        self._step_counts[rule.name] = step_n
        run.attempts[rule.name] += 1
        started = self._clock()
        acted = self._act(rule, screen)
        self._screen = None
        stop, described = rule.then == "stop_run", acted or self._describe(rule)
        seen = replace(seen, action=described)
        if acted is None or not self._gone(rule):
            return end(
                Verdict.STOP, Code.E0408, f"Popup '{rule.name}' is still on screen after '{described}'", stop_run=stop,
                evidence=replace(seen, images=[before, self._save(self._io.screenshot(), "after")], source=True, gone=False),
            )
        seen = replace(
            seen, images=[before, self._save(self._io.screenshot(), "after")],
            gone_ms=int((self._clock() - started) * 1000), gone=True,
        )
        if rule.then == "retry_step":
            return end(Verdict.HANDLED, None, "", evidence=seen)
        return end(
            Verdict.STOP, Code.E0408, f"Popup '{rule.name}' was dismissed and then ended the {'run' if stop else 'step'}",
            stop_run=stop, evidence=seen,
        )

    def _act(self, rule: PopupRule, screen: PopupScreen) -> Optional[str]:
        """What the rule did, or None when it did nothing that can dismiss the popup."""
        action = rule.action
        if action.type == "tap":
            return next((f"tap {t}" for t in (action.target, action.fallback) if t and self._tapped(screen, t, rule)), None)
        try:
            if action.type == "back":
                self._io.back()
            elif action.type == "alert":
                self._io.alert(action.target or "")
            else:
                return None
        except Exception as e:  # noqa: BLE001 - an action that fails leaves the popup up and the caller reports that
            internal_logger.info("Popup guard: '%s' action failed: %s", rule.name, e)
            return None
        return self._describe(rule)

    def _tapped(self, screen: PopupScreen, target: str, rule: PopupRule) -> bool:
        try:
            return self._tap(screen, target)
        except Exception as e:  # noqa: BLE001 - a tap that fails leaves the popup up and the fallback may still dismiss it
            internal_logger.info("Popup guard: '%s' tap on '%s' failed: %s", rule.name, target, e)
            return False

    def _tap(self, screen: PopupScreen, target: str) -> bool:
        """Tap where the page source already says ``target`` is; only if it does not, ask the locate ladder (OCR, image)."""
        point = screen.point_of(target)
        if point is None:
            return self._io.tap(target)
        self._io.tap_at(*point)
        return True

    def _gone(self, rule: PopupRule) -> bool:
        """True once a readable screen no longer matches the rule; waits up to ``GONE_TIMEOUT`` for that."""
        deadline = self._clock() + GONE_TIMEOUT
        while True:
            self._screen = None
            screen = self._read()
            if screen.readable and screen.matches(rule.detect) is None:
                self._screen = None
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(GONE_POLL)

    @staticmethod
    def _describe(rule: PopupRule) -> str:
        return f"{rule.action.type} {rule.action.target or ''}".strip()

    def _unknown(self, screen: PopupScreen, check: str) -> Outcome:
        image = self._save(self._io.screenshot(), "before")
        text = screen.popup_text()
        outcome = self._end(
            Verdict.UNKNOWN_POPUP, Code.E0206, f"A popup with no rule is on screen: {text!r}",
            screen=screen, check=check, evidence=Evidence(images=[image], source=True, text=text),
        )
        self._run().unknown.append({**self._step, "popup_text": text})
        return outcome

    def _save(self, image: Any, name: str) -> Optional[str]:
        run = self._run()
        return self._io.save_image(image, f"popup_{run.id}_{run.inspections + 1}_{name}") if image is not None else None

    def _end(
        self, verdict: Verdict, code: Optional[Code], message: str, *, screen: PopupScreen, check: str,
        rule: Optional[PopupRule] = None, matched: Optional[List[str]] = None, stop_run: bool = False,
        evidence: Evidence = Evidence(),
    ) -> Outcome:
        run = self._run()
        name = Verdict.LIMIT if code == Code.E0407 else verdict
        if rule is not None and name in COUNTED:
            run.counts.setdefault(rule.name, Counter())[name.value] += 1
        saved_source = self._io.save_text(screen.raw, f"popup_{run.id}_{run.inspections + 1}_source") if evidence.source else None
        if stop_run:
            run.stop = message
        self._event(
            check, name.value, screen=screen, rule=rule, matched=matched, action=evidence.action,
            popup_gone=evidence.gone, dismissed_in_ms=evidence.gone_ms, step_count=evidence.step_count,
            run_count=evidence.run_count, screenshots=[i for i in evidence.images if i], page_source=saved_source,
            code=code.value if code else None, popup_text=evidence.text, stop_run=stop_run,
        )
        internal_logger.info(
            "Popup %s: rule=%s action=%s%s", name.value, rule.name if rule else "-", evidence.action or "-",
            f" in {evidence.gone_ms}ms" if evidence.gone_ms is not None else "",
        )
        return Outcome(verdict, code, stop_run, message)

    def _event(self, check: str, verdict: str, *, screen: PopupScreen, rule: Optional[PopupRule] = None, **fields: Any) -> None:
        run = self._run()
        run.inspections += 1
        run.events.append(PopupEvent(
            **self._step, platform=screen.platform, app_id=screen.app_id, check=check,
            rule=rule.name if rule else None,
            matched=fields.pop("matched", None) or [],
            popup_kind=screen.kind() if verdict != Verdict.NONE else None,
            popup_text=fields.pop("popup_text", None) or (screen.popup_text() if verdict != Verdict.NONE else None),
            popup_title=screen.popup_title() if verdict != Verdict.NONE else None,
            verdict=verdict, timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **fields,
        ).model_dump())
