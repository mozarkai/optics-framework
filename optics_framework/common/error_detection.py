"""Shared on-screen error-detection primitives.

Used by both the CLI/TestRunner path (`_capture_end_of_run_artifacts`) and the
library `Optics` class (`capture_and_detect`) so the matching logic lives in
one place.
"""
import re
from typing import Dict, List, Optional

# Attributes in Appium XML that carry user-visible text on screen.
_APPIUM_TEXT_ATTRS = re.compile(
    r'\b(?:text|content-desc|label|value|hint|name)="([^"]*)"'
)


def extract_visible_text(page_source: str) -> str:
    """Extract only user-visible text from a page source string.

    For HTML (Selenium/Playwright): strips all tags via BeautifulSoup,
    returning only rendered text content.

    For Appium XML: pulls values of the attributes that carry on-screen text
    (text, content-desc, label, value, hint, name), ignoring resource-ids,
    class names, bounds, and other metadata. This prevents false-positive
    matches where a pattern appears inside an element attribute name or CSS
    class rather than as visible text.
    """
    stripped = page_source.lstrip()
    if stripped.lower().startswith(("<html", "<!doctype")):
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(page_source, "lxml").get_text(separator=" ", strip=True)
        except Exception:
            return page_source
    return " ".join(_APPIUM_TEXT_ATTRS.findall(page_source))


def detect_errors_in_text(
    searchable: str,
    error_definitions: Dict[str, Dict[str, str]],
    context_label: Optional[str] = None,
    test_case: Optional[str] = None,
) -> List[Dict]:
    """OR-match each error definition's pattern OR error_code against `searchable`.

    Returns a list of matched dicts of shape
    ``{error_code, matched_on, pattern, description, severity, ...}``.
    When ``context_label`` is provided, each entry also carries
    ``detected_at=context_label``; when ``test_case`` is provided, the entry
    carries ``test_case=...``.

    `matched_on` is ``"pattern"`` when the pattern column triggered the match,
    or ``"code"`` when only the error_code substring was present.
    """
    matched: List[Dict] = []
    for code, meta in error_definitions.items():
        pattern = meta.get("pattern", "")
        matched_on: Optional[str] = None
        if pattern and pattern in searchable:
            matched_on = "pattern"
        elif code and code in searchable:
            matched_on = "code"
        if matched_on:
            entry: Dict = {"error_code": code, "matched_on": matched_on, **meta}
            if context_label:
                entry["detected_at"] = context_label
            if test_case:
                entry["test_case"] = test_case
            matched.append(entry)
    return matched
