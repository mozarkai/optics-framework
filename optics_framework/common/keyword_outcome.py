"""Read a keyword's outcome from what it logged: a silent error and the winning locator strategy."""
import logging
import re
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar

_INTERNAL_LOGGER_NAME = "optics.internal"
_EXECUTION_LOGGER_NAME = "optics.execution"

_STRATEGY_LABELS: dict[str, str] = {
    "XPathStrategy": "XPath",
    "TextElementStrategy": "Text",
    "TextDetectionStrategy": "OCR",
    "ImageDetectionStrategy": "Image",
}
_NON_LOCATOR_STRATEGIES = frozenset({"ScreenshotStrategy", "PagesourceStrategy"})
_STRATEGY_RE = re.compile(r"Trying (\w+) on .*? \.\.\. (\w+)", re.DOTALL)
_ACT_RE = re.compile(r"^Action '\w+' (succeeded|failed) with (\w+)", re.DOTALL)

_captured: ContextVar[list[logging.LogRecord] | None] = ContextVar("_captured", default=None)


class _ContextCapture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        records = _captured.get()
        if records is not None:
            records.append(record)


_HANDLER = _ContextCapture(logging.DEBUG)
_lock = threading.Lock()
_active = 0
_saved_level: int | None = None


def _from_internal_logger(record: logging.LogRecord) -> bool:
    return record.name == _INTERNAL_LOGGER_NAME or record.name.startswith(f"{_INTERNAL_LOGGER_NAME}.")


def find_error_log(records: Iterable[logging.LogRecord]) -> str | None:
    """The message of the first ERROR record from the internal logger, or None.

    Some keywords (an Appium swipe in an unknown direction, for example) log an error and
    return instead of raising, so a message here means the keyword did not do what was asked.
    """
    for record in records:
        if not isinstance(record, logging.LogRecord) or record.levelno < logging.ERROR:
            continue
        if not _from_internal_logger(record):
            continue
        try:
            return record.getMessage()
        except (TypeError, ValueError):
            return str(record.msg)
    return None


def _message(record: logging.LogRecord) -> str | None:
    try:
        return record.getMessage() if isinstance(record, logging.LogRecord) else str(record)
    except (TypeError, ValueError):
        return None


def _label(cls_name: str) -> str:
    return _STRATEGY_LABELS.get(cls_name, cls_name.replace("Strategy", ""))


def _acted(message: str, outcome: str) -> str | None:
    match = _ACT_RE.match(message)
    return match.group(2) if match and match.group(1) == outcome else None


def _located(message: str, *, success_only: bool = True) -> str | None:
    """The locator strategy a tracer line names, or None for any other line."""
    match = _STRATEGY_RE.search(message)
    if not match or match.group(1) in _NON_LOCATOR_STRATEGIES:
        return None
    return match.group(1) if not success_only or match.group(2).upper() == "SUCCESS" else None


def winning_strategy(records: Iterable[logging.LogRecord]) -> str | None:
    """The short label of the strategy whose result the keyword acted on, such as ``XPath``.

    That is the last ``Action ... succeeded with`` line; a keyword that logs none (an
    assertion) gets the first locator strategy that found its element.
    """
    messages = [m for m in map(_message, records) if m is not None]
    for message in reversed(messages):
        if strategy := _acted(message, "succeeded"):
            return _label(strategy)
    for message in messages:
        if strategy := _located(message):
            return _label(strategy)
    return None


def silent_error(records: Sequence[logging.LogRecord]) -> str | None:
    """An error the keyword logged after its lookups: it did not do what was asked.

    Errors from strategies, whether they found the element or missed it, and from acts on
    results that failed before the one that worked, do not count; errors the working act
    logged do.
    """
    messages = [_message(r) or "" for r in records]
    acted = max((i for i, m in enumerate(messages) if _acted(m, "succeeded")), default=None)
    if acted is None:
        start = max((i for i, m in enumerate(messages) if _located(m, success_only=False)), default=-1)
    else:
        start = max(
            (i for i, m in enumerate(messages[:acted]) if _acted(m, "failed") or _located(m, success_only=False)),
            default=-1,
        )
    return find_error_log(records[start + 1:])


def _start() -> None:
    global _active, _saved_level
    with _lock:
        if _active == 0:
            logging.getLogger(_INTERNAL_LOGGER_NAME).addHandler(_HANDLER)
            execution = logging.getLogger(_EXECUTION_LOGGER_NAME)
            execution.addHandler(_HANDLER)
            # ExecutionTracer logs the winning strategy at INFO.
            if execution.level == logging.NOTSET or execution.level > logging.INFO:
                _saved_level = execution.level
                execution.setLevel(logging.INFO)
        _active += 1


def _stop() -> None:
    global _active, _saved_level
    with _lock:
        _active -= 1
        if _active == 0 and _saved_level is not None:
            logging.getLogger(_EXECUTION_LOGGER_NAME).setLevel(_saved_level)
            _saved_level = None


@contextmanager
def capture_keyword_logs() -> Iterator[list[logging.LogRecord]]:
    """Collect the optics log records of the keyword run inside this block.

    Records are kept per context, and ``asyncio.to_thread`` copies the context into its
    worker thread, so concurrent calls never see each other's records.
    """
    records: list[logging.LogRecord] = []
    token = _captured.set(records)
    _start()
    try:
        yield records
    finally:
        _stop()
        _captured.reset(token)
