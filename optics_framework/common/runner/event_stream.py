"""Run progress written as lines, for output that is not an interactive terminal."""
import json
import sys
from datetime import datetime
from enum import Enum
from typing import TextIO

from optics_framework.common.events import Event, EventStatus, EventSubscriber

EVENT_STREAM_SUBSCRIBER_ID = "event_stream_printer"

_LABELS = {
    "execution": "run",
    "test_case": "test case",
    "module": "module",
    "keyword": "keyword",
    "popup": "popup",
}
_DEPTH = {"execution": 0, "test_case": 0, "module": 1, "keyword": 2, "popup": 3}
_PROBLEMS = (EventStatus.FAIL, EventStatus.ERROR)
_HEALED = "HEALED"
_STATUS_WIDTH = max(len(_HEALED), *(len(status.value) for status in EventStatus))


def _popup_fields(event: Event) -> dict:
    return event.args if event.entity_type == "popup" and isinstance(event.args, dict) else {}


class OutputMode(str, Enum):
    """How ``optics execute`` and ``optics dry_run`` show a run's progress."""
    AUTO = "auto"
    TREE = "tree"
    PLAIN = "plain"
    JSONL = "jsonl"
    NONE = "none"


def resolve_output_mode(mode: OutputMode, stream: TextIO | None = None) -> OutputMode:
    """The mode to use: ``auto`` is the tree on an interactive terminal and plain lines otherwise."""
    if mode is not OutputMode.AUTO:
        return mode
    stream = stream or sys.stdout
    isatty = getattr(stream, "isatty", None)
    return OutputMode.TREE if callable(isatty) and isatty() else OutputMode.PLAIN


def output_mode_for(output: OutputMode | None, use_printer: bool) -> OutputMode:
    """The resolved mode: ``output`` when given, else ``use_printer`` picks auto or none."""
    if output is None:
        output = OutputMode.AUTO if use_printer else OutputMode.NONE
    return resolve_output_mode(output)


def prints_lines(mode: OutputMode) -> bool:
    return mode in (OutputMode.PLAIN, OutputMode.JSONL)


class EventStreamPrinter(EventSubscriber):
    """Writes one line per test case, module, keyword and acted-on popup event, flushed as it arrives.

    ``plain`` lines are for people reading a CI log; ``jsonl`` lines are the events
    themselves, for tools. Events reach subscribers already redacted.
    """

    def __init__(self, mode: OutputMode, stream: TextIO | None = None):
        if not prints_lines(mode):
            raise ValueError(f"EventStreamPrinter writes plain or jsonl lines, not {mode.value}")
        self.mode = mode
        # Bound now: pytest swaps sys.stdout while the pytest runner's cases run.
        self.stream = stream or sys.stdout
        self._run_reported = False
        self._last_reason: str | None = None

    async def on_event(self, event: Event) -> None:
        if event.entity_type not in _LABELS or _popup_fields(event).get("verdict") == "none":
            return
        if event.entity_type == "execution":
            if event.status not in _PROBLEMS or self._run_reported:
                return
            self._run_reported = True
        line = self.format_jsonl(event) if self.mode is OutputMode.JSONL else self.format_plain(event)
        self._write(line)

    @staticmethod
    def format_jsonl(event: Event) -> str:
        return json.dumps(event.model_dump(mode="json"), ensure_ascii=True)

    def format_plain(self, event: Event) -> str:
        clock = datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S")
        indent = "  " * _DEPTH[event.entity_type]
        healed = event.status is EventStatus.PASS and event.extra.get("healed") == "true"
        status = _HEALED if healed else event.status.value
        popup = _popup_fields(event)
        subject = (popup.get("rule") or "no rule") if event.entity_type == "popup" else event.name
        line = f'{clock} {status:<{_STATUS_WIDTH}} {indent}{_LABELS[event.entity_type]} "{subject}"'
        if event.entity_type == "popup":
            line += f" {popup.get('verdict')}"
        if event.elapsed is not None and event.status is not EventStatus.RUNNING:
            line += f" ({event.elapsed:.1f}s)"
        if healed and event.extra.get("heal_summary"):
            return line + ": " + event.extra["heal_summary"]
        if event.status in _PROBLEMS:
            line += self._reason(event)
        return line

    def _reason(self, event: Event) -> str:
        """The failure's code and message, once: a parent that failed for the same reason shows only the code."""
        message = event.message
        if message and message == self._last_reason:
            return f": {event.code}" if event.code else ""
        self._last_reason = message or self._last_reason
        code = event.code if event.code and event.code not in message else None
        reason = " ".join(part for part in (code, message) if part)
        return f": {reason}" if reason else ""

    def _write(self, line: str) -> None:
        try:
            self.stream.write(line + "\n")
        except UnicodeEncodeError:
            encoding = getattr(self.stream, "encoding", None) or "ascii"
            self.stream.write(line.encode(encoding, "replace").decode(encoding) + "\n")
        self.stream.flush()
