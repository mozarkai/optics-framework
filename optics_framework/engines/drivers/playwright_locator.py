"""Turns an Optics element value into a Playwright locator, for every Playwright source."""

import json
import time
from typing import Any, Callable

from playwright.async_api import Error as PlaywrightError

from optics_framework.common import utils
from optics_framework.engines.drivers.playwright import Playwright

_ENGINE_PREFIXES = ("role=", "id=", "data-testid=", "data-test=", "data-test-id=")
# Other agent browsers' spellings of the same locators.
_ALIASES = {"loc=css:": "css=", "loc=role:": "role=", "testid=": "data-testid="}
_SEMANTIC_PREFIXES = {"text=": "text", "label=": "label", "placeholder=": "placeholder", "alt=": "alt", "title=": "title"}
_POLL_INTERVAL_S = 0.25


def _strip_prefix(element: str, prefix: str) -> str | None:
    if element[: len(prefix)].lower() == prefix:
        return element[len(prefix):]
    return None


def _semantic(page: Any, value: str, kinds: tuple, exact: bool) -> list[Any]:
    # Exact matches of every kind come before partial ones.
    builders = {
        "text": lambda e: page.get_by_text(value, exact=e),
        "label": lambda e: page.get_by_label(value, exact=e),
        "placeholder": lambda e: page.get_by_placeholder(value, exact=e),
        "alt": lambda e: page.get_by_alt_text(value, exact=e),
        "title": lambda e: page.get_by_title(value, exact=e),
        "button": lambda e: page.get_by_role("button", name=value, exact=e),
        "link": lambda e: page.get_by_role("link", name=value, exact=e),
    }
    passes = (True,) if exact else (True, False)
    return [builders[kind](e) for e in passes for kind in kinds]


def _prefixed(page: Any, element: str, exact: bool) -> list[Any] | None:
    """The locators a prefixed ``element`` names, or ``None`` when it has no known prefix."""
    if (value := _strip_prefix(element, "xpath=")) is not None:
        return [page.locator(f"xpath={value}")]
    if (value := _strip_prefix(element, "css=")) is not None:
        return [page.locator(value)]
    if (value := _strip_prefix(element, "loc=href:")) is not None:
        return [page.locator(f"[href={json.dumps(value, ensure_ascii=False)}]")]
    for prefix, kind in _SEMANTIC_PREFIXES.items():
        if (value := _strip_prefix(element, prefix)) is not None:
            inner = _unquote(value)
            return _semantic(page, value if inner is None else inner, (kind,), exact or inner is not None)
    return None


def _unquote(value: str) -> str | None:
    """The text inside ``"..."``, which asks for an exact match, or ``None`` when ``value`` is not quoted."""
    return value[1:-1] if len(value) > 1 and value[0] == value[-1] == '"' else None


def candidates(page: Any, element: str, *, exact: bool = False) -> list[Any]:
    """The locators ``element`` may mean, most specific first."""
    element = element.strip()
    for alias, prefix in _ALIASES.items():
        if (value := _strip_prefix(element, alias)) is not None:
            element = prefix + value
    if (found := _prefixed(page, element, exact)) is not None:
        return found
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


def _nth_match(locator: Any, index: int, visible_only: bool, on_screen: bool, run: Callable) -> tuple[Any | None, int]:
    try:
        if not visible_only:
            count = run(locator.count())
            return (locator.nth(index) if count > index else None), count
        flags = run(locator.evaluate_all(utils.VISIBLE_FLAGS_JS, on_screen))
    except PlaywrightError as e:
        if Playwright.is_dead_session_error(e):
            raise
        return None, 0  # an unparsable selector matches nothing
    visible = [i for i, shown in enumerate(flags) if shown]
    return (locator.nth(visible[index]) if len(visible) > index else None), len(visible)


def find(
    driver: Playwright,
    element: str,
    *,
    index: int = 0,
    exact: bool = False,
    timeout_s: float = 0.0,
    visible_only: bool = True,
    on_screen: bool = False,
    warn_if_ambiguous: bool = False,
) -> Any | None:
    """The ``index``-th (visible) match of ``element``, or ``None`` after ``timeout_s``."""
    options = candidates(driver.page, element, exact=exact)
    deadline = time.monotonic() + timeout_s
    while True:
        for locator in options:
            match, count = _nth_match(locator, index, visible_only, on_screen, driver.run)
            if match is not None:
                if warn_if_ambiguous:
                    utils.warn_if_ambiguous(element, count, index)
                return match
        if time.monotonic() >= deadline:
            return None
        time.sleep(_POLL_INTERVAL_S)


def is_present(driver: Playwright, element: str, *, exact: bool = False) -> bool:
    return find(driver, element, exact=exact, visible_only=False) is not None


def is_visible(driver: Playwright, element: str, *, exact: bool = False) -> bool:
    return find(driver, element, exact=exact, on_screen=True) is not None
