from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from optics_framework.common import utils
from optics_framework.common.popup_guard import PopupRun

current_test_case: ContextVar[str] = ContextVar("current_test_case", default=None)
element_names: ContextVar[dict[str, str]] = ContextVar("element_names")
# The runner's own record of the run's popups; without a runner (a library or live session) a keyword layer keeps its own.
popup_run: ContextVar[PopupRun | None] = ContextVar("popup_run", default=None)


@contextmanager
def case_scope(test_case: str, popup: PopupRun) -> Iterator[None]:
    """Run one test case with its name and the runner's popup record bound, then restore what was bound before."""
    case, record = current_test_case.set(test_case), popup_run.set(popup)
    try:
        yield
    finally:
        popup_run.reset(record)
        current_test_case.reset(case)


def name_locators(params: Iterable[str], locators: Callable[[str], list[str] | None]) -> None:
    """Record, for the keyword about to run, the element name each locator of its ``${name}`` params comes from; the first name wins."""
    names: dict[str, str] = {}
    for name in filter(None, map(utils.whole_var_name, params)):
        for value in locators(name) or []:
            names.setdefault(value, name)
    element_names.set(names)
