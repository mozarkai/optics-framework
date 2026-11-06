"""Find web elements for the Selenium element sources."""

import time
from typing import Any

from selenium.common.exceptions import InvalidSelectorException, StaleElementReferenceException
from selenium.webdriver.common.by import By

from optics_framework.common import utils

_RENDERED = "//*[not(self::script or self::style or self::noscript or self::template)]"
_ON_SCREEN_FLAGS = f"return ({utils.VISIBLE_FLAGS_JS})(arguments[0], true);"
_POLL_INTERVAL_S = 0.25


def find(driver: Any, element: str, index: int = 0, *, visible: bool = False, timeout_s: float = 0.0) -> Any:
    """The ``index``-th match of ``element`` (on screen when ``visible``), or ``None`` after ``timeout_s``."""
    webdriver = driver.require_webdriver()
    queries = _queries(element, _strict_element_match(driver))
    deadline = time.monotonic() + timeout_s
    while True:
        for query in queries:
            matches = _matches(webdriver, query, visible)
            if len(matches) > index:
                return matches[index]
        if time.monotonic() >= deadline:
            return None
        time.sleep(_POLL_INTERVAL_S)


def check(driver: Any, elements: list[str], visible: bool = False) -> list[bool]:
    """One look, no waiting: whether each element is present, or on screen when ``visible``."""
    return [find(driver, element, visible=visible) is not None for element in elements]


def wait_for(driver: Any, elements: list[str], timeout_s: float, rule: str, visible: bool = False) -> None:
    """Return once ``rule`` (``any`` or ``all``) holds for ``elements``; raise ``TimeoutError`` after ``timeout_s``."""
    holds = {"any": any, "all": all}.get(rule)
    if holds is None:
        raise ValueError("Invalid rule. Use 'any' or 'all'.")
    deadline = time.monotonic() + timeout_s
    while not holds(check(driver, elements, visible)):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timeout reached: rule '{rule}' not met (visible={visible}): {elements}")
        time.sleep(_POLL_INTERVAL_S)


def _matches(webdriver: Any, query: tuple[str, str], visible: bool) -> list[Any]:
    try:
        matches = webdriver.find_elements(*query)
        if visible and matches:
            flags = webdriver.execute_script(_ON_SCREEN_FLAGS, matches)
            matches = [match for match, shown in zip(matches, flags) if shown]
    except (InvalidSelectorException, StaleElementReferenceException):
        return []
    return matches


def _strict_element_match(driver: Any) -> bool:
    try:
        return bool(driver.event_sdk.config_handler.config.strict_element_match)
    except AttributeError:
        return False


def _queries(element: str, strict: bool) -> list[tuple[str, str]]:
    """The lookups ``element`` may mean, most specific first."""
    element_type = utils.determine_element_type(element)
    if element_type == "CSS":
        return [(By.CSS_SELECTOR, utils.without_prefix(element, "css="))]
    if element_type == "XPath":
        return [(By.XPATH, utils.without_prefix(element, utils.XPATH_PREFIX))]
    if element_type != "Text":
        return []
    value = utils.without_prefix(element, "text=")
    literal = utils.xpath_literal(value)
    queries = [(By.XPATH, f"{_RENDERED}[normalize-space(text())={literal}]")]
    if not strict:
        queries.append((By.XPATH, f"{_RENDERED}[contains(text(), {literal})]"))
    queries += [(By.ID, value), (By.NAME, value), (By.CLASS_NAME, value), (By.TAG_NAME, value), (By.LINK_TEXT, value)]
    if not strict:
        queries.append((By.PARTIAL_LINK_TEXT, value))
    return queries + [(By.CSS_SELECTOR, value), (By.XPATH, value)]
