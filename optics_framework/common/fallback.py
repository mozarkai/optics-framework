"""Fallback level 1: run a keyword over the combinations of its parameters' alternative values."""
import inspect
import math
from collections import deque
from collections.abc import Awaitable, Callable, Iterator, Sequence
from itertools import islice, product
from typing import Any

from optics_framework.common.error import Code, OpticsError, is_element_not_found
from optics_framework.common.logging_config import internal_logger

MAX_ATTEMPTS = 20
KEPT_FAILURES = 5

Combo = tuple[Any, ...]


def advances(exc: BaseException) -> bool:
    """True when another value may succeed: the element was not found (E02xx / X0201), and no popup stopped the step."""
    return isinstance(exc, OpticsError) and is_element_not_found(exc.code)


def check_arity(method: Callable[..., Any], count: int, keyword: str) -> None:
    """Refuse ``count`` positional values ``method`` cannot take, before any of them is tried."""
    signature = inspect.signature(method)
    params = [p for p in signature.parameters.values() if p.name != "self"]
    try:
        signature.replace(parameters=params).bind(*range(count))
    except TypeError as exc:
        raise OpticsError(Code.E0403, message=f"'{keyword}' cannot take {count} positional params: {exc}") from exc


class FallbackExhausted(OpticsError):
    """Every tried combination failed to find its element; carries the last failure's code."""

    def __init__(self, subject: str, failures: Sequence[tuple[Combo, OpticsError]], tried: int, total: int) -> None:
        self.last = failures[-1][1]
        self.tried, self.total = tried, total
        super().__init__(self.last.code, message=self._describe(subject, failures), cause=self.last)

    @property
    def limit_note(self) -> str:
        return f" (stopped at the limit of {MAX_ATTEMPTS} of {self.total} combinations)" if self.total > self.tried else ""

    def _describe(self, subject: str, failures: Sequence[tuple[Combo, OpticsError]]) -> str:
        if self.tried == 1:
            return self.last.message
        omitted = self.tried - len(failures)
        lines = [f"{combo} -> {error}" for combo, error in failures]
        if omitted:
            lines.insert(0, f"... {omitted} earlier attempts not shown")
        return f"All {self.tried} fallback combinations failed for {subject}{self.limit_note}:\n" + "\n".join(lines)


class _Ladder:
    """The combinations to try, in order and at most ``MAX_ATTEMPTS``, and the failure once they run out."""

    def __init__(self, candidates: Sequence[Sequence[Any]], subject: str) -> None:
        if any(not values for values in candidates):
            raise OpticsError(Code.E0403, message=f"A parameter of {subject} has no value to try")
        self.subject = subject
        self.total = math.prod(len(values) for values in candidates)
        self.tried = 0
        self._candidates = candidates
        self._failures: deque[tuple[Combo, OpticsError]] = deque(maxlen=KEPT_FAILURES)

    def __iter__(self) -> Iterator[Combo]:
        for combo in islice(product(*self._candidates), MAX_ATTEMPTS):
            self.tried += 1
            yield combo

    def failed(self, combo: Combo, exc: Exception) -> None:
        """Record an element-not-found failure; re-raise any other, which no other value can fix."""
        if not advances(exc):
            raise exc
        internal_logger.debug("Keyword fallback: tried %s, error: %s", combo, exc)
        self._failures.append((combo, exc))

    def exhausted(self) -> FallbackExhausted:
        return FallbackExhausted(self.subject, list(self._failures), self.tried, self.total)


def run[R](candidates: Sequence[Sequence[Any]], call: Callable[[Combo], R], subject: str) -> R:
    """``call`` on each combination until one succeeds; a non-element error is raised as is."""
    ladder = _Ladder(candidates, subject)
    for combo in ladder:
        try:
            return call(combo)
        except Exception as exc:
            ladder.failed(combo, exc)
    raise ladder.exhausted()


async def run_async[R](candidates: Sequence[Sequence[Any]], call: Callable[[Combo], Awaitable[R]], subject: str) -> R:
    """``run`` for a coroutine ``call``."""
    ladder = _Ladder(candidates, subject)
    for combo in ladder:
        try:
            return await call(combo)
        except Exception as exc:
            ladder.failed(combo, exc)
    raise ladder.exhausted()
