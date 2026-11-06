"""Find web elements for the Selenium element sources."""

from typing import Any

from selenium.common.exceptions import InvalidSelectorException
from selenium.webdriver.common.by import By

from optics_framework.common import utils

_RENDERED = "//*[not(self::script or self::style or self::noscript or self::template)]"


def find(driver: Any, element: str, index: int = 0) -> Any:
    """The ``index``-th match of ``element`` through ``driver``'s session, or ``None`` when nothing matches."""
    webdriver = driver.require_webdriver()
    for query in _queries(element, _strict_element_match(driver)):
        try:
            matches = webdriver.find_elements(*query)
        except InvalidSelectorException:
            continue
        if len(matches) > index:
            return matches[index]
    return None


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
