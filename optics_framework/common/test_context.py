from collections.abc import Callable, Iterable
from contextvars import ContextVar

from optics_framework.common import utils

current_test_case: ContextVar[str] = ContextVar("current_test_case", default=None)
element_names: ContextVar[dict[str, str]] = ContextVar("element_names")


def name_locators(params: Iterable[str], locators: Callable[[str], list[str] | None]) -> None:
    """Record, for the keyword about to run, the element name each locator of its ``${name}`` params comes from; the first name wins."""
    names: dict[str, str] = {}
    for name in filter(None, map(utils.whole_var_name, params)):
        for value in locators(name) or []:
            names.setdefault(value, name)
    element_names.set(names)
