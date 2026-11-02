"""What a keyword run did: the winning locator strategy and an error it logged without raising."""
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

_INTERNAL_LOGGER_NAME = "optics.internal"
_LABELS = {"TextElementStrategy": "Text", "TextDetectionStrategy": "OCR", "ImageDetectionStrategy": "Image"}
_NOT_LOOKUPS = frozenset({"ScreenshotStrategy", "PagesourceStrategy"})


@dataclass
class Outcome:
    strategy: str | None = None
    acted: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def silent(self) -> str | None:
        return self.errors[0] if self.errors else None


_outcome: ContextVar[Outcome | None] = ContextVar("_outcome", default=None)


class _ErrorSink(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        o = _outcome.get()
        if o is None or not record.name.startswith(_INTERNAL_LOGGER_NAME):
            return
        try:
            o.errors.append(record.getMessage())
        except Exception:  # noqa: BLE001 - a handler must never raise into the keyword
            self.handleError(record)


logging.getLogger(_INTERNAL_LOGGER_NAME).addHandler(_ErrorSink(logging.ERROR))


def _label(strategy: object) -> str:
    name = type(strategy).__name__
    return _LABELS.get(name, name.replace("Strategy", ""))


def looked(strategy: object, ok: bool) -> None:
    """Until the act has run, errors before a lookup do not count and the first strategy that found the element wins."""
    o = _outcome.get()
    if o is None or o.acted or type(strategy).__name__ in _NOT_LOOKUPS:
        return
    o.errors.clear()
    if ok and o.strategy is None:
        o.strategy = _label(strategy)


def act_failed() -> None:
    o = _outcome.get()
    if o is not None:
        o.errors.clear()


def acted(strategy: object) -> None:
    o = _outcome.get()
    if o is not None:
        o.acted = True
        o.strategy = _label(strategy)


@contextmanager
def capture() -> Iterator[Outcome]:
    """Collect the outcome of the keyword run inside this block; ``asyncio.to_thread`` carries it along."""
    outcome = Outcome()
    token = _outcome.set(outcome)
    try:
        yield outcome
    finally:
        _outcome.reset(token)
