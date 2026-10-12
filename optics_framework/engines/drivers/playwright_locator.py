"""Turns an Optics element value into a Playwright locator, for every Playwright source."""

import time
from typing import Any, List, Optional

from playwright.async_api import Error as PlaywrightError

from optics_framework.common import utils
from optics_framework.common.async_utils import run_async
from optics_framework.engines.drivers.playwright import Playwright

_ENGINE_PREFIXES = ("role=", "id=", "data-testid=", "data-test=", "data-test-id=")
_POLL_INTERVAL_S = 0.25

_VISIBLE_FLAGS_SCRIPT = """
(els, onScreen) => els.map((el) => {
    const rect = el.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    if (window.getComputedStyle(el).visibility === 'hidden') return false;
    return !onScreen || (rect.bottom > 0 && rect.right > 0
        && rect.top < window.innerHeight && rect.left < window.innerWidth);
})
"""


def _strip_prefix(element: str, prefix: str) -> Optional[str]:
    if element[: len(prefix)].lower() == prefix:
        return element[len(prefix):]
    return None


def _semantic(page: Any, value: str, kinds: tuple, exact: bool) -> List[Any]:
    # Exact matches of every kind come before partial ones.
    builders = {
        "text": lambda e: page.get_by_text(value, exact=e),
        "label": lambda e: page.get_by_label(value, exact=e),
        "placeholder": lambda e: page.get_by_placeholder(value, exact=e),
        "button": lambda e: page.get_by_role("button", name=value, exact=e),
        "link": lambda e: page.get_by_role("link", name=value, exact=e),
    }
    passes = (True,) if exact else (True, False)
    return [builders[kind](e) for e in passes for kind in kinds]


def candidates(page: Any, element: str, *, exact: bool = False) -> List[Any]:
    """The locators ``element`` may mean, most specific first."""
    element = element.strip()
    if (value := _strip_prefix(element, "xpath=")) is not None:
        return [page.locator(f"xpath={value}")]
    if (value := _strip_prefix(element, "css=")) is not None:
        return [page.locator(value)]
    if (value := _strip_prefix(element, "text=")) is not None:
        return _semantic(page, value, ("text",), exact)
    if (value := _strip_prefix(element, "label=")) is not None:
        return _semantic(page, value, ("label",), exact)
    if (value := _strip_prefix(element, "placeholder=")) is not None:
        return _semantic(page, value, ("placeholder",), exact)
    if element.lower().startswith(_ENGINE_PREFIXES):
        return [page.locator(element)]
    element_type = utils.determine_element_type(element)
    if element_type == "XPath":
        return [page.locator(f"xpath={element}")]
    text_kinds = ("text", "label", "placeholder", "button", "link")
    if element_type == "CSS":
        # Values like "#Trending" or "p.m." look like CSS but are often visible text.
        return [page.locator(element), *_semantic(page, element, text_kinds, exact)]
    return _semantic(page, element, text_kinds, exact)


def _nth_match(locator: Any, index: int, visible_only: bool, on_screen: bool) -> Optional[Any]:
    try:
        if not visible_only:
            return locator.nth(index) if run_async(locator.count()) > index else None
        flags = run_async(locator.evaluate_all(_VISIBLE_FLAGS_SCRIPT, on_screen))
    except PlaywrightError as e:
        if Playwright.is_dead_session_error(e):
            raise
        return None  # an unparsable selector matches nothing
    visible = [i for i, shown in enumerate(flags) if shown]
    return locator.nth(visible[index]) if len(visible) > index else None


def find(
    page: Any,
    element: str,
    *,
    index: int = 0,
    exact: bool = False,
    timeout_s: float = 0.0,
    visible_only: bool = True,
    on_screen: bool = False,
) -> Optional[Any]:
    """The ``index``-th (visible) match of ``element``, or ``None`` after ``timeout_s``."""
    options = candidates(page, element, exact=exact)
    deadline = time.monotonic() + timeout_s
    while True:
        for locator in options:
            match = _nth_match(locator, index, visible_only, on_screen)
            if match is not None:
                return match
        if time.monotonic() >= deadline:
            return None
        time.sleep(_POLL_INTERVAL_S)


def is_present(page: Any, element: str, *, exact: bool = False) -> bool:
    return find(page, element, exact=exact, visible_only=False) is not None


def is_visible(page: Any, element: str, *, exact: bool = False) -> bool:
    return find(page, element, exact=exact, on_screen=True) is not None

