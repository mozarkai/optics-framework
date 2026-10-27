"""Find the text field a caption names in an Appium page source, as Playwright does on the web."""
import re
from typing import Any, Optional

_IOS_FIELDS = {
    "XCUIElementTypeTextField",
    "XCUIElementTypeSecureTextField",
    "XCUIElementTypeSearchField",
    "XCUIElementTypeTextView",
}
_CAPTION_ATTRS = ("text", "content-desc", "label", "name", "value")
# A field's text (Android) and value (iOS) hold what was typed, not its name.
_FIELD_NAME_ATTRS = ("content-desc", "hint", "label", "name", "placeholderValue")


def is_field(class_name: str) -> bool:
    return class_name.endswith(("EditText", "AutoCompleteTextView")) or class_name in _IOS_FIELDS


def _tag(node: Any) -> str:
    return node.get("class") or (node.tag if isinstance(node.tag, str) else "")


def _bounds(node: Any) -> Optional[tuple[int, int, int, int]]:
    if node.get("bounds"):
        numbers = [int(n) for n in re.findall(r"-?\d+", node.get("bounds"))]
        return tuple(numbers) if len(numbers) == 4 else None
    try:
        x, y = int(float(node.get("x"))), int(float(node.get("y")))
        return x, y, x + int(float(node.get("width"))), y + int(float(node.get("height")))
    except (TypeError, ValueError):
        return None


def node_at(root: Any, rect: dict, text: str) -> Optional[Any]:
    """The node drawn at ``rect`` (a WebElement rect), preferring the one that shows ``text``."""
    want = (rect["x"], rect["y"], rect["x"] + rect["width"], rect["y"] + rect["height"])
    same = [
        node for node in root.iter()
        if (b := _bounds(node)) and all(abs(a - w) <= 1 for a, w in zip(b, want))
    ]
    shows_text = [node for node in same if text and any(node.get(a) == text for a in _CAPTION_ATTRS)]
    return (shows_text or same or [None])[-1]


def _names(field: Any, text: str, exact: bool) -> bool:
    values = [(field.get(a) or "").strip().lower() for a in _FIELD_NAME_ATTRS]
    return any(v == text if exact else text in v for v in values)


def field_for(root: Any, caption: Any) -> Optional[Any]:
    """The field ``caption`` names, or ``None`` when no single field fits."""
    fields = [node for node in root.iter() if is_field(_tag(node))]
    text = next((caption.get(a).strip() for a in _CAPTION_ATTRS if (caption.get(a) or "").strip()), "").lower()
    for exact in (True, False) if text else ():
        named = [f for f in fields if _names(f, text, exact)]
        if len(named) == 1:
            return named[0]
    for container in caption.iterancestors():
        inside = [node for node in container.iter() if is_field(_tag(node))]
        if inside:
            return inside[0] if len(inside) == 1 else None
    return None


def absolute_xpath(node: Any) -> str:
    parts = []
    while node.getparent() is not None:
        parent = node.getparent()
        same_tag = [child for child in parent if child.tag == node.tag]
        parts.append(f"{node.tag}[{same_tag.index(node) + 1}]")
        node = parent
    parts.append(node.tag)
    return "/" + "/".join(reversed(parts))
