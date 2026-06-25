"""Shared on-screen error-detection primitives.

Used by both the CLI/TestRunner path (`_capture_end_of_run_artifacts`) and the
library `Optics` class (`capture_and_detect`) so the matching logic lives in
one place.
"""
import re
import xml.etree.ElementTree as ET  # nosec B405
from typing import Dict, List, Optional

from optics_framework.common.logging_config import internal_logger

try:
    from bs4 import BeautifulSoup as _BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BeautifulSoup = None  # type: ignore[assignment,misc]
    _BS4_AVAILABLE = False

# Attributes in mobile XML (Appium/XCUITest) that carry user-visible text.
# NOTE: kept platform-neutral on purpose — these names are common across
# Android (uiautomator) and iOS (XCUITest) page sources. Add new on-screen-text
# attributes here as other element sources are supported.
_MOBILE_TEXT_ATTRS = ("text", "content-desc", "label", "value", "hint", "name")

# Regex fallback for when the XML is malformed and ElementTree can't parse it.
# The leading ``(?<![\w-])`` ensures we only match a *whole* attribute name and
# never a suffix of a longer one — e.g. ``data-text``, ``aria-label`` and
# ``tab-name`` must NOT match ``text``/``label``/``name``.
_MOBILE_TEXT_ATTRS_RE = re.compile(
    r'(?<![\w-])(?:text|content-desc|label|value|hint|name)="([^"]*)"'
)


def extract_visible_text(page_source: str) -> str:
    """Extract only user-visible text from a page source string.

    For HTML (Selenium/Playwright): strips all tags via BeautifulSoup,
    returning only rendered text content.  Returns ``""`` when bs4 is
    unavailable or parsing fails — raw HTML is never returned as it would
    cause severe false positives (CSS class names like ``class="error"``).

    For Appium / mobile XML: parses via ElementTree and collects values of
    the attributes that carry on-screen text (text, content-desc, label,
    value, hint, name), ignoring resource-ids, class names, bounds, and
    other metadata.  Falls back to regex extraction when the XML is
    malformed.
    """
    stripped = page_source.lstrip()
    if stripped.lower().startswith(("<html", "<!doctype")):
        if not _BS4_AVAILABLE or _BeautifulSoup is None:
            internal_logger.warning(
                "beautifulsoup4 is not installed; cannot extract visible text "
                "from HTML page source. Install the 'beautifulsoup4' package to "
                "enable HTML error detection."
            )
            return ""
        # Only guard against malformed-markup parse failures here — the import
        # is handled at module load, so this never masks an ImportError.
        try:
            return _BeautifulSoup(page_source, "lxml").get_text(separator=" ", strip=True)
        except (ValueError, TypeError) as exc:
            internal_logger.warning("Failed to parse HTML page source: %s", exc)
            return ""

    # Mobile / Appium XML — prefer ElementTree over regex.
    try:
        root = ET.fromstring(page_source)  # nosec B314
        texts: List[str] = []
        for elem in root.iter():
            for attr in _MOBILE_TEXT_ATTRS:
                val = (elem.get(attr) or "").strip()
                if val:
                    texts.append(val)
        return " ".join(texts)
    except ET.ParseError:
        return " ".join(_MOBILE_TEXT_ATTRS_RE.findall(page_source))


def detect_errors_in_text(
    searchable: str,
    error_definitions: Dict[str, Dict[str, str]],
    context_label: Optional[str] = None,
    test_case: Optional[str] = None,
) -> List[Dict]:
    """OR-match each error definition's ``match_string`` OR ``error_code`` against ``searchable``.

    Matching is **case-insensitive substring** matching — the ``match_string``
    value (and the ``error_code``) are treated as plain text and tested with
    ``in``; they are *not* interpreted as regular expressions or glob patterns.

    Returns a list of matched dicts of shape
    ``{error_code, matched_on, match_string, description, severity, ...}``.
    When ``context_label`` is provided, each entry also carries
    ``detected_at=context_label``; when ``test_case`` is provided, the entry
    carries ``test_case=...``. These two keys are the only ones that vary
    between callers; the core fields are always present.

    ``matched_on`` is ``"match_string"`` when the ``match_string`` column
    triggered the match, or ``"code"`` when only the ``error_code`` substring
    was present.
    """
    searchable_lower = searchable.lower()
    matched: List[Dict] = []
    for code, meta in error_definitions.items():
        match_string = meta.get("match_string", "")
        matched_on: Optional[str] = None
        if match_string and match_string.lower() in searchable_lower:
            matched_on = "match_string"
        elif code and code.lower() in searchable_lower:
            matched_on = "code"
        if matched_on:
            entry: Dict = {"error_code": code, "matched_on": matched_on, **meta}
            if context_label:
                entry["detected_at"] = context_label
            if test_case:
                entry["test_case"] = test_case
            matched.append(entry)
    return matched
