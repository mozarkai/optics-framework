from typing import List, Optional, Protocol, Sequence, Tuple

from optics_framework.common.models import PopupDetect


class PopupScreen(Protocol):
    """One read of the screen, as the popup guard needs it. A driver parses its own page source into this."""

    @property
    def raw(self) -> str: ...

    @property
    def missed(self) -> str: ...

    @property
    def readable(self) -> bool: ...

    @property
    def platform(self) -> Optional[str]: ...

    @property
    def app_id(self) -> Optional[str]: ...

    @property
    def alerts_outside_source(self) -> bool:
        """True when system alerts are left out of the page source, so the driver has to be asked for them."""
        ...

    def add_alert(self, text: str, buttons: Sequence[str]) -> None: ...

    def matches(self, detect: PopupDetect) -> Optional[List[str]]: ...

    def has_dialog(self) -> bool: ...

    def kind(self) -> str: ...

    def popup_text(self) -> str: ...

    def popup_title(self) -> str: ...

    def point_of(self, target: str) -> Optional[Tuple[int, int]]: ...


class BlankScreen:
    """The screen of a driver that cannot read popups: nothing matches and no dialog is seen."""

    raw = ""
    missed = ""
    readable = False
    platform = None
    app_id = None
    alerts_outside_source = False

    def add_alert(self, text: str, buttons: Sequence[str]) -> None:
        return None

    def matches(self, detect: PopupDetect) -> Optional[List[str]]:
        return None

    def has_dialog(self) -> bool:
        return False

    def kind(self) -> str:
        return "in_app"

    def popup_text(self) -> str:
        return ""

    def popup_title(self) -> str:
        return ""

    def point_of(self, target: str) -> Optional[Tuple[int, int]]:
        return None
