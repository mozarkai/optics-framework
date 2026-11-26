from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

current_test_case: ContextVar[str] = ContextVar("current_test_case", default=None)
element_names: ContextVar[dict[str, str]] = ContextVar("element_names")
# The runner's own record of the run's popups; without a runner (a library or live session) a keyword layer keeps its own.
popup_run: ContextVar[Any] = ContextVar("popup_run", default=None)


@contextmanager
def case_scope(test_case: str, popup: Any) -> Iterator[None]:
    """Run one test case with its name and the runner's popup record bound, then restore what was bound before."""
    case, record = current_test_case.set(test_case), popup_run.set(popup)
    try:
        yield
    finally:
        popup_run.reset(record)
        current_test_case.reset(case)
