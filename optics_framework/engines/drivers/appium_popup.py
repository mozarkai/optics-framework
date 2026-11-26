import re
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from lxml import etree  # type: ignore[import-untyped]

from optics_framework.common import utils
from optics_framework.common.error import OpticsError
from optics_framework.common.error_detection import MOBILE_TEXT_ATTRS
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.models import PopupDetect

TEXT_LIMIT = 200

# Nodes that only a dialog or sheet has; a class name is no help because the dialog's own views are ordinary ones.
_ANDROID_DIALOG_PANEL = "parentPanel"
_ANDROID_DIALOG_IDS = ("alertTitle", _ANDROID_DIALOG_PANEL, "button1", "design_bottom_sheet")
ANDROID_SYSTEM_PACKAGES = (
    "com.android.permissioncontroller", "com.google.android.permissioncontroller",
    "com.android.packageinstaller", "com.google.android.packageinstaller",
)
_IOS_ALERT_TAGS = ("XCUIElementTypeAlert", "XCUIElementTypeSheet", "XCUIElementTypePopover")
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_SWITCHED_OFF = ("displayed", "visible", "enabled")


def _centre(node: Any) -> Optional[Tuple[int, int]]:
    """The middle of a node that is shown, enabled and has an area; None for one a tap could not reach."""
    if any(node.get(attr) == "false" for attr in _SWITCHED_OFF):
        return None
    bounds = _BOUNDS.fullmatch(node.get("bounds") or "")
    if bounds:
        x1, y1, x2, y2 = map(int, bounds.groups())
        return ((x1 + x2) // 2, (y1 + y2) // 2) if x2 > x1 and y2 > y1 else None
    try:
        x, y, width, height = (int(float(node.get(a))) for a in ("x", "y", "width", "height"))
    except (TypeError, ValueError):
        return None
    return (x + width // 2, y + height // 2) if width > 0 and height > 0 else None


class AppiumScreen:
    """An Appium page source (Android or iOS XML), read once, as the popup guard sees a screen."""

    def __init__(self, raw: Optional[str]):
        self.raw = raw or ""
        self.root = None
        if raw:
            try:
                self.root = etree.fromstring(raw.encode("utf-8"))
            except Exception as e:  # noqa: BLE001 - an unreadable source is a screen with nothing on it
                internal_logger.debug("Popup guard: page source is not XML: %s", e)
        self.nodes = [n for n in self.root.iter() if isinstance(n.tag, str)] if self.root is not None else []
        self.missed = ""

    def add_alert(self, text: str, buttons: Sequence[str]) -> None:
        """Show a system alert on this screen. iOS leaves it out of the page source, so it is read through the driver."""
        if self.root is None:
            self.root = etree.Element("AppiumAUT")
        alert = etree.SubElement(self.root, "XCUIElementTypeAlert", name=text, label=text)
        for button in buttons:
            etree.SubElement(alert, "XCUIElementTypeButton", name=button, label=button)
        self.nodes = [n for n in self.root.iter() if isinstance(n.tag, str)]
        self.raw = etree.tostring(self.root, encoding="unicode")

    @staticmethod
    def _texts(nodes) -> List[str]:
        seen: Dict[str, None] = {}
        for node in nodes:
            for attr in MOBILE_TEXT_ATTRS:
                value = " ".join((node.get(attr) or "").split())
                if value:
                    seen.setdefault(value)
        return list(seen)

    @property
    def alerts_outside_source(self) -> bool:
        return self.platform != "android"

    @property
    def readable(self) -> bool:
        return self.root is not None

    @property
    def text(self) -> str:
        return " ".join(self._texts(self.nodes))

    @property
    def platform(self) -> Optional[str]:
        if self.root is None:
            return None
        return "ios" if self.root.tag == "AppiumAUT" or self.nodes[0].tag.startswith("XCUIElementType") else "android"

    @property
    def app_id(self) -> Optional[str]:
        """The app under test: the first Android package that is not the system's, or the iOS application's name."""
        for node in self.nodes:
            if node.tag == "XCUIElementTypeApplication":
                return node.get("name")
            package = node.get("package")
            if package and package not in ANDROID_SYSTEM_PACKAGES:
                return package
        return None

    def dialog(self):
        """The node a dialog, sheet or alert hangs from, or None. A title or button marker stands for its dialog's panel."""
        for node in self.nodes:
            if (
                node.tag in _IOS_ALERT_TAGS
                or node.get("package") in ANDROID_SYSTEM_PACKAGES
                or self._resource_name(node) in _ANDROID_DIALOG_IDS
            ):
                return next((a for a in node.iterancestors() if self._resource_name(a) == _ANDROID_DIALOG_PANEL), node)
        return None

    @staticmethod
    def _resource_name(node) -> str:
        return (node.get("resource-id") or "").rsplit("/", 1)[-1].rsplit(":", 1)[-1]

    def has_dialog(self) -> bool:
        return self.dialog() is not None

    def kind(self) -> str:
        dialog = self.dialog()
        system = dialog is not None and (dialog.tag == "XCUIElementTypeAlert" or dialog.get("package") in ANDROID_SYSTEM_PACKAGES)
        return "system_alert" if system else "in_app"

    def popup_text(self) -> str:
        dialog = self.dialog()
        return " ".join(self._texts(dialog.iter() if dialog is not None else self.nodes))[:TEXT_LIMIT]

    def popup_title(self) -> str:
        """The first text of the dialog, which is what names it; a rule written from it keeps to that."""
        dialog = self.dialog()
        return next(iter(self._texts(dialog.iter() if dialog is not None else self.nodes)), "")[:TEXT_LIMIT]

    def point_of(self, target: str) -> Optional[Tuple[int, int]]:
        """The centre of the first shown, enabled node ``target`` names (an xpath, a resource-id or its visible text), from its own bounds."""
        return next((point for node in self._nodes_of(target) if (point := _centre(node))), None)

    def _nodes_of(self, target: str) -> Iterator[Any]:
        if self.root is None:
            return
        kind = utils.determine_element_type(target)
        if kind == "XPath":
            try:
                found = self.root.xpath(utils.resolve_locator(target).removeprefix(utils.XPATH_PREFIX))
            except (etree.XPathError, OpticsError):
                return
            yield from (n for n in found if hasattr(n, "get"))
        elif kind == "Text":
            text = utils.parse_text_only_prefix(target)[0]
            wanted = " ".join(text.removeprefix("text=").split()).casefold()
            yield from (
                node for node in self.nodes
                if node.get("resource-id") == target
                or any(" ".join((node.get(a) or "").split()).casefold() == wanted for a in MOBILE_TEXT_ATTRS)
            )

    def matches(self, detect: PopupDetect) -> Optional[List[str]]:
        """The detect keys that matched when every one given did, else None; ``missed`` names the key that did not."""
        found: List[str] = []
        self.missed = ""
        if detect.text_contains:
            lowered = self.text.lower()
            if not any(t.lower() in lowered for t in detect.text_contains):
                self.missed = "text_contains"
                return None
            found.append("text_contains")
        if detect.resource_id:
            if not any(n.get("resource-id") == detect.resource_id for n in self.nodes):
                self.missed = "resource_id"
                return None
            found.append("resource_id")
        if detect.xpath:
            try:
                hit = bool(self.root.xpath(detect.xpath)) if self.root is not None else False
            except etree.XPathError as e:
                internal_logger.debug("Popup guard: xpath %r is not valid: %s", detect.xpath, e)
                hit = False
            if not hit:
                self.missed = "xpath"
                return None
            found.append("xpath")
        return found
