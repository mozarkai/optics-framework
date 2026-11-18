import math
import re
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt, ValidationError, model_validator

from optics_framework.common import utils
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.eventSDK import EventSDK
from optics_framework.common.logging_config import internal_logger

try:
    import airhand
except ImportError:
    airhand = None

MIGRATION_NOTE = "'Migrating from the v1 ble driver' in the configuration docs"
LEGACY_KEYS = frozenset({"device_id", "x_invert", "y_invert", "pixel_width", "pixel_height", "mickeys_width", "mickeys_height"})
SWIPE_MS = 1000
# In frame heights per second; iOS reads a slower drag as a tap at its start, or ignores it.
MIN_DRAG_SPEED = 0.75
WHEEL_LINES = 5
# iOS reads a burst of detents as one flick and reverses its sign: 1-2 move nothing, 8 move 0.46 of an iPhone screen.
IOS_WHEEL_LINES = 8
# iOS sends the wheel to the view under the pointer.
WHEEL_AT = (0.5, 0.5)
DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
DESKTOP_WHEEL = {"up": (WHEEL_LINES, 0), "down": (-WHEEL_LINES, 0), "left": (0, -WHEEL_LINES), "right": (0, WHEEL_LINES)}
IOS_WHEEL = {"up": (-IOS_WHEEL_LINES, 0), "down": (IOS_WHEEL_LINES, 0)}
# Android ignores the wheel from the dongle; a 0.6-screen drag this slow moves the content 1.08 times, an 800 ms one 1.5 times and up to a screen.
ANDROID_SCROLL_MS = 4000
MEDIA = {"volumeup": "volup", "volumedown": "voldown"}
NO_HID_KEY = frozenset({"power", "camera"})
SEARCH_OPEN_S = 0.6
# iOS ignores an Enter that arrives before Spotlight has listed its results.
SEARCH_RESULTS_S = 1.5
# Keys sent while the app opens are lost.
APP_OPEN_S = 1.5
HOME_TWICE_S = 0.15
# A switcher opened while the app still animates home lays its cards out from that app, not from the home screen.
HOME_SETTLE_S = 1.0
# iOS ignores Cmd+Space for a moment after Home closes Spotlight.
SEARCH_CLOSED_S = 2.5
# Android draws the focus move before an Enter can act on it.
FOCUS_MOVE_S = 0.4
SWITCHER_OPEN_S = 1.2
FLICK_MS = 250
# The cursor stays where it pressed and hides that spot from the camera; the status bar holds nothing a test reads.
PARK = (1.0, 0.0)
LaunchMethod = Literal["search_key", "launcher_key", "drawer_search", "drawer_tap", "home_icon"]
DRAWER_METHODS = frozenset({"drawer_search", "drawer_tap"})
# A search that found the app shows its name twice: the typed query and the result.
SEARCH_SHOWS_NAME = 2
# The icon's middle is about two label heights above its label.
ICON_ABOVE_LABEL = 2.0


@dataclass(frozen=True)
class Platform:
    back: Optional[tuple[str, str]]
    launcher: Optional[str]
    touch: bool
    media_home: bool
    select_all: str = "ctrl+a"
    # Where the App Switcher shows the last app used, when it is opened from the home screen.
    last_app_card: Optional[float] = None
    # Where a flick that closes that card starts and ends; Android lets a short flick snap back about one time in ten.
    card_flick: tuple[float, float] = (0.5, 0.05)
    # Android reads the dongle's pointer as a relative mouse, so it gets touches.
    finger: bool = False
    # Opens the list of apps with its search box, and the app switcher, when the system search cannot open an app.
    app_list: Optional[str] = None
    switcher: Optional[str] = None
    # Home from an app that the system search opened goes back to the search, so leaving it takes a second Home.
    search_returns: bool = False
    # The wheel detents and pan that scroll each direction; a direction without one drags.
    wheel: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    # How long a Scroll drag lasts, when the screen flings content on by the release speed.
    scroll_ms: Optional[int] = None


PLATFORMS: dict[str, Platform] = {
    "android": Platform(
        ("media", "back"),
        None,
        touch=True,
        media_home=True,
        last_app_card=0.5,
        card_flick=(0.75, 0.02),
        finger=True,
        app_list="meta",
        switcher="meta+tab",
        scroll_ms=ANDROID_SCROLL_MS,
    ),
    "ios": Platform(
        None, "cmd+space", touch=True, media_home=True, select_all="cmd+a", last_app_card=0.5, search_returns=True, wheel=IOS_WHEEL
    ),
    "macos": Platform(("key", "cmd+["), "cmd+space", touch=False, media_home=False, select_all="cmd+a", wheel=DESKTOP_WHEEL),
    "windows": Platform(("key", "alt+left"), "ctrl+escape", touch=False, media_home=False, wheel=DESKTOP_WHEEL),
    "linux": Platform(("key", "alt+left"), None, touch=False, media_home=False, wheel=DESKTOP_WHEEL),
}


class Frame(BaseModel):
    width: PositiveInt
    height: PositiveInt


class Point(BaseModel):
    x: NonNegativeInt
    y: NonNegativeInt


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serial: Optional[str] = None
    port: Optional[str] = None
    platform: Literal["android", "ios", "macos", "windows", "linux"]
    frame: Frame
    # Some Android launchers start an app search only from a tap on their search box.
    app_search: Optional[Point] = None
    launch: Optional[list[LaunchMethod]] = Field(None, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _refuse_legacy_keys(cls, data: Any) -> Any:
        legacy = sorted(LEGACY_KEYS & set(data)) if isinstance(data, dict) else []
        if legacy:
            raise ValueError(f"{', '.join(legacy)} belong to the v1 ble driver; see {MIGRATION_NOTE}")
        return data

    @model_validator(mode="after")
    def _one_link(self) -> "Capabilities":
        if self.serial is not None and self.port is not None:
            raise ValueError("set serial (the dongle's USB serial number) or port, not both")
        return self

    @model_validator(mode="after")
    def _app_search_in_frame(self) -> "Capabilities":
        box = self.app_search
        if box is not None and (box.x >= self.frame.width or box.y >= self.frame.height):
            raise ValueError(f"app_search ({box.x}, {box.y}) is outside the frame {self.frame.width}x{self.frame.height}")
        return self

    @model_validator(mode="after")
    def _launch_fits_platform(self) -> "Capabilities":
        platform = PLATFORMS[self.platform]
        missing = {
            "launcher_key": platform.launcher is None,
            "drawer_search": platform.app_list is None,
            "drawer_tap": platform.app_list is None or self.app_search is None,
            "home_icon": not platform.media_home,
        }
        refused = [method for method in self.launch or [] if missing.get(method)]
        if refused:
            raise ValueError(f"launch methods {', '.join(refused)} need a key or an app_search that platform {self.platform} does not have")
        return self

    @property
    def launch_methods(self) -> list[str]:
        if self.launch:
            return list(self.launch)
        platform = PLATFORMS[self.platform]
        if platform.app_list:
            methods = ["drawer_tap" if self.app_search else "drawer_search"]
        else:
            methods = ["launcher_key"] if platform.launcher else []
        # Some launchers have no app drawer, or give its key to the assistant; their apps sit on the home screen.
        return methods + ["home_icon"] if methods and platform.media_home else methods


class BLEDriver(DriverInterface):
    """Drives a device as a Bluetooth keyboard and pointer through an airhand dongle; positions are in ``frame`` pixels."""

    DEPENDENCY_TYPE = "driver_sources"
    NAME = "ble"

    def __init__(self, config: Optional[Dict[str, Any]] = None, event_sdk: Optional[EventSDK] = None):
        if event_sdk is None:
            raise OpticsError(Code.E0101, message="The ble driver needs an EventSDK")
        if airhand is None:
            raise OpticsError(Code.E0601, message="The ble driver needs the airhand package: pip install 'optics-framework[ble]', or run optics setup")
        self.event_sdk = event_sdk
        try:
            self.capabilities = Capabilities(**(config or {}).get("capabilities", {}))
        except ValidationError as exc:
            raise OpticsError(Code.E0503, message=f"Invalid ble capabilities: {exc}") from exc
        self.platform = PLATFORMS[self.capabilities.platform]
        self._read_screen: Optional[Callable[[], list[dict]]] = None
        try:
            with _mapped():
                self._dongle = airhand.Dongle(self.capabilities.serial, port=self.capabilities.port, touch=self.platform.finger)
        except ValueError as exc:
            raise OpticsError(Code.E0503, message=f"{exc} in capabilities.serial") from exc
        internal_logger.info(f"ble dongle ready: {self._dongle.identity}")

    @staticmethod
    def is_dead_session_error(exc: BaseException) -> bool:
        lost = airhand is not None and isinstance(exc, (airhand.DongleLost, airhand.HostUnavailable))
        return lost or (isinstance(exc, OpticsError) and exc.code == Code.E0106)

    def supports(self, capability: str) -> bool:
        if capability == "go_back":
            return self.platform.back is not None
        opens_apps = bool(self.capabilities.launch_methods)
        if capability in ("launch_app", "launch_other_app"):
            return opens_apps
        if capability == "force_terminate_app":
            return opens_apps and self.platform.last_app_card is not None
        return super().supports(capability)

    def get_driver_session_id(self) -> Optional[str]:
        return self._dongle.identity

    def press_coordinates(self, coor_x: int, coor_y: int, event_name: Optional[str] = None) -> None:
        self._capture(event_name)
        with _mapped():
            self._dongle.tap(*self._to_fractions(coor_x, coor_y))
            self._park()

    def press_percentage_coordinates(self, percentage_x: float, percentage_y: float, repeat: int, event_name: Optional[str] = None) -> None:
        self._capture(event_name)
        with _mapped():
            for _ in range(repeat):
                self._dongle.tap(percentage_x / 100, percentage_y / 100)
            self._park()

    def swipe(self, x_coor: int, y_coor: int, direction: str, swipe_length: int, event_name: Optional[str] = None) -> None:
        dx, dy = _direction(direction)
        frame = self.capabilities.frame
        end_x = min(max(x_coor + dx * swipe_length, 0), frame.width)
        end_y = min(max(y_coor + dy * swipe_length, 0), frame.height)
        self._capture(event_name)
        self._drag(self._to_fractions(x_coor, y_coor), self._to_fractions(end_x, end_y), SWIPE_MS)

    def swipe_percentage(self, x_percentage: int, y_percentage: int, direction: str, swipe_length_percentage: int, event_name: Optional[str] = None) -> None:
        dx, _ = _direction(direction)
        frame = self.capabilities.frame
        length = (frame.width if dx else frame.height) * swipe_length_percentage / 100
        self.swipe(frame.width * x_percentage / 100, frame.height * y_percentage / 100, direction, length, event_name)

    def scroll(self, direction: str, duration: int = 1000, event_name: Optional[str] = None) -> None:
        fractions = utils.SCROLL_FRACTIONS.get(direction)
        if fractions is None:
            raise OpticsError(Code.E0403, message=f"Unknown scroll direction '{direction}'")
        self._capture(event_name)
        if self.platform.scroll_ms is not None:
            with _mapped():
                self._dongle.swipe(fractions[:2], fractions[2:], self.platform.scroll_ms)
            return
        wheel = self.platform.wheel.get(direction)
        if wheel is None:
            self._drag(fractions[:2], fractions[2:], int(duration))
            return
        with _mapped():
            if self.platform.touch:
                self._dongle.move(*WHEEL_AT)
            self._dongle.wheel(*wheel)
            self._park()

    def enter_text(self, text: str | utils.SpecialKey, event_name: Optional[str] = None) -> None:
        if isinstance(text, utils.SpecialKey):
            self.press_keycode(text.value, event_name)
            return
        self._capture(event_name)
        with _mapped():
            self._dongle.type_text(utils.strip_sensitive_prefix(text))

    def enter_text_using_keyboard(self, text: str | utils.SpecialKey, event_name: Optional[str] = None) -> None:
        self.enter_text(text, event_name)

    def press_keycode(self, keycode: str, event_name: Optional[str] = None) -> None:
        name = utils.strip_sensitive_prefix(str(keycode)).strip().lower()
        if not name or any(char.isspace() for char in name):
            raise OpticsError(Code.E0403, message=f"Key name {keycode!r} is empty or holds a space or line break")
        if name in NO_HID_KEY:
            self._unsupported(f"press_keycode {name}")
        if name == "back":
            self.go_back(event_name)
            return
        media = MEDIA.get(name, name)
        self._capture(event_name)
        with _mapped():
            if name == "search":
                self._search()
            elif media in airhand.MEDIA and (name != "home" or self.platform.media_home):
                self._dongle.media(media)
            else:
                self._dongle.key(name)

    def clear_text(self, event_name: Optional[str] = None) -> None:
        self._capture(event_name)
        with _mapped():
            self._dongle.key(self.platform.select_all)
            self._dongle.key("backspace")

    def go_back(self, event_name: Optional[str] = None) -> None:
        self._require("go_back")
        self._capture(event_name)
        kind, name = self.platform.back
        with _mapped():
            getattr(self._dongle, kind)(name)

    def terminate(self) -> None:
        self._dongle.close()
        self.event_sdk.send_all_events()

    def launch_app(self, app_identifier: str | None = None, app_activity: str | None = None, event_name: str | None = None) -> None:
        # A session opens the configured app with no name; a ble project configures none, so the screen stays as it is.
        if not app_identifier or not app_identifier.strip():
            return
        self._require("launch_app")
        self._open_app(app_identifier, event_name)

    def launch_other_app(self, app_name: str, event_name: str | None) -> None:
        self._require("launch_other_app")
        self._open_app(app_name, event_name)

    def get_app_version(self) -> str:
        self._unsupported("get_app_version")

    def force_terminate_app(self, app_name: str, event_name: Optional[str] = None) -> None:
        self._require("force_terminate_app")
        self._open_app(app_name, event_name)
        card = self.platform.last_app_card
        with _mapped():
            self._go_home()
            if self.platform.switcher:
                self._dongle.key(self.platform.switcher)
            else:
                self._dongle.media("home")
                time.sleep(HOME_TWICE_S)
                self._dongle.media("home")
            time.sleep(SWITCHER_OPEN_S)
        start, end = self.platform.card_flick
        self._drag((card, start), (card, end), FLICK_MS)
        with _mapped():
            self._dongle.media("home")
        time.sleep(SWITCHER_OPEN_S)

    def press_element(self, element: str, repeat: int, event_name: Optional[str] = None) -> None:
        self._unsupported("press_element")

    def enter_text_element(self, element: str, text: str, event_name: Optional[str] = None) -> None:
        self._unsupported("enter_text_element")

    def clear_text_element(self, element: str, event_name: Optional[str] = None) -> None:
        self._unsupported("clear_text_element")

    def swipe_element(self, element: str, direction: str, swipe_length: int, event_name: Optional[str] = None) -> None:
        self._unsupported("swipe_element")

    def get_text_element(self, element: str) -> str:
        self._unsupported("get_text_element")

    def execute_script(self, script: str, *args, event_name: Optional[str] = None) -> Any:
        self._unsupported("execute_script")

    def _search(self) -> None:
        if self.platform.launcher:
            self._dongle.key(self.platform.launcher)
        else:
            self._dongle.media("search")

    def attach_screen(self, read: Optional[Callable[[], list[dict]]]) -> None:
        self._read_screen = read

    def _open_app(self, app_name: str | None, event_name: str | None) -> None:
        name = (app_name or "").strip()
        if not name:
            raise OpticsError(Code.E0403, message="The ble driver opens an app by the name the system search shows; give that name")
        self._capture(event_name)
        methods = self.capabilities.launch_methods
        if self._read_screen is None:
            self._launch_unchecked(name, methods)
            return
        for method in methods:
            if self._launched(method, name):
                if method != methods[0]:
                    internal_logger.warning(f"'{name}' opened with {method} after {', '.join(methods[:methods.index(method)])} did not open it; "
                                            f"to try it first on this device, set capabilities.launch: [{', '.join([method] + [m for m in methods if m != method])}]")
                return
            internal_logger.info(f"{method} did not open '{name}'")
            with _mapped():
                self._go_home()
        raise OpticsError(Code.E0405, message=f"No launch method opened '{name}': tried {', '.join(methods)}. "
                                              "Set capabilities.launch to the methods that open apps on this device")

    def _launch_unchecked(self, name: str, methods: list[str]) -> None:
        blind = [method for method in methods if method != "home_icon"]
        if not blind:
            raise OpticsError(Code.E0501, message="The home_icon launch method finds the app with the camera; add camera_screenshot and an OCR engine, or another launch method")
        with _mapped():
            self._type_query(blind[0], name)
            self._commit(blind[0])
        time.sleep(APP_OPEN_S)
        internal_logger.info(f"Opened '{name}' with {blind[0]}; no camera reads the screen, so the launch is not checked")

    def _launched(self, method: str, name: str) -> bool:
        if method == "home_icon":
            return self._tap_home_icon(name)
        with _mapped():
            self._type_query(method, name)
        search = self._read()
        if search is not None and len(_labels(search, name)) < SEARCH_SHOWS_NAME:
            return False
        with _mapped():
            self._commit(method)
        return self._opened(search, method, name)

    def _opened(self, before: Optional[list[dict]], method: str, name: str) -> bool:
        time.sleep(APP_OPEN_S)
        after = self._read() if before is not None else None
        if not after:
            internal_logger.info(f"Opened '{name}' with {method}; the camera did not read the screen, so the launch is not checked")
            return True
        return not any(_near(shown, still) for shown in _labels(before, name) for still in _labels(after, name))

    def _read(self) -> Optional[list[dict]]:
        try:
            return self._read_screen()
        except OpticsError as exc:
            if exc.code in (Code.E0106, Code.E0305):
                raise
            internal_logger.warning(f"The camera did not read the screen: {exc}")
            return None

    def _type_query(self, method: str, name: str) -> None:
        if method in DRAWER_METHODS:
            self._dongle.media("home")
            time.sleep(SEARCH_OPEN_S)
            self._dongle.key(self.platform.app_list)
        else:
            # Cmd+Space closes a search that is already open.
            if self.platform.search_returns:
                self._go_home()
            if method == "search_key":
                self._dongle.media("search")
            else:
                self._dongle.key(self.platform.launcher)
        time.sleep(SEARCH_OPEN_S)
        box = self.capabilities.app_search
        if method == "drawer_tap" and box:
            self._dongle.tap(*self._to_fractions(box.x, box.y))
            time.sleep(SEARCH_OPEN_S)
        self._dongle.type_text(name)
        time.sleep(SEARCH_RESULTS_S)

    def _commit(self, method: str) -> None:
        # A tapped box keeps the focus, so Up moves it to the top result; a launcher that searches as keys come opens it on Enter.
        if method == "drawer_tap":
            self._dongle.key("up")
            time.sleep(FOCUS_MOVE_S)
        self._dongle.key("enter")

    def _tap_home_icon(self, name: str) -> bool:
        with _mapped():
            self._go_home()
        home = self._read()
        labels = _labels(home, name) if home is not None else []
        if not labels:
            return False
        left, top, right, bottom = labels[0]
        x = (left + right) / 2
        y = max(top - ICON_ABOVE_LABEL * (bottom - top), 0)
        with _mapped():
            self._dongle.tap(*self._to_fractions(x, y))
            self._park()
        return self._opened(home, "home_icon", name)

    def _go_home(self) -> None:
        self._dongle.media("home")
        time.sleep(HOME_SETTLE_S)
        if self.platform.search_returns:
            self._dongle.media("home")
            time.sleep(SEARCH_CLOSED_S)

    def _drag(self, start: tuple[float, float], end: tuple[float, float], ms: int) -> None:
        frame = self.capabilities.frame
        heights = math.hypot((end[0] - start[0]) * frame.width, (end[1] - start[1]) * frame.height) / frame.height
        with _mapped():
            self._dongle.swipe(start, end, max(1, min(ms, round(1000 * heights / MIN_DRAG_SPEED))))
            self._park()

    def _park(self) -> None:
        """Move the pointer off the screen; a refused move must not make the touch before it count as failed."""
        if not self.platform.touch or self.platform.finger:
            return
        try:
            self._dongle.move(*PARK)
        except (airhand.DongleLost, airhand.HostUnavailable):
            raise
        except airhand.AirhandError as exc:
            internal_logger.warning(f"The pointer was not moved off the screen: {exc}")

    def _require(self, capability: str) -> None:
        if not self.supports(capability):
            self._unsupported(capability)

    def _to_fractions(self, x: float, y: float) -> tuple[float, float]:
        frame = self.capabilities.frame
        return float(x) / frame.width, float(y) / frame.height

    def _capture(self, event_name: Optional[str]) -> None:
        if event_name:
            self.event_sdk.capture_event(event_name)


@contextmanager
def _mapped() -> Iterator[None]:
    try:
        yield
    except (airhand.DongleLost, airhand.HostUnavailable) as exc:
        raise OpticsError(Code.E0106, message=str(exc)) from exc
    except airhand.WrongFirmware as exc:
        raise OpticsError(Code.E0501, message=str(exc)) from exc
    except airhand.Unsupported as exc:
        raise OpticsError(Code.E0403, message=str(exc)) from exc
    except airhand.AirhandError as exc:
        raise OpticsError(Code.E0401, message=str(exc)) from exc


def _direction(direction: str) -> tuple[int, int]:
    if direction not in DIRECTIONS:
        raise OpticsError(Code.E0403, message=f"Unknown swipe direction '{direction}'")
    return DIRECTIONS[direction]


def _key(text: str) -> str:
    return "".join(text.split()).casefold()


def _box(row: dict) -> tuple[int, int, int, int]:
    bounds = row["bounds"]
    return bounds["x1"], bounds["y1"], bounds["x2"], bounds["y2"]


def _same_line(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    short, tall = sorted((a, b), key=lambda box: box[3] - box[1])
    return tall[1] <= (short[1] + short[3]) / 2 <= tall[3]


def _beside(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """b starts within half a text height of a's end, as the next word of one label does."""
    return b[0] - a[2] <= min(a[3] - a[1], b[3] - b[1]) / 2


def _cut_off(text: str, wanted: str) -> bool:
    """A label the launcher shortened, read as its start and the ellipsis."""
    shown = re.fullmatch(r"(.*[^\W_])[\W_]+", text.strip())
    start = _key(shown.group(1)) if shown else ""
    return len(start) >= 4 and wanted.startswith(start)


def _labels(rows: list[dict], name: str) -> list[tuple[int, int, int, int]]:
    """Boxes of the rows, or runs of words side by side on one line, that read the name, then those that read its cut-off start."""
    wanted = _key(name)
    rows = sorted((row for row in rows if _key(row.get("text", ""))), key=lambda row: _box(row)[0])
    found = []
    for i, row in enumerate(rows):
        run, read = _run_from(rows, i, wanted)
        if read == wanted:
            cut = _key(row["text"]) != wanted and _cut_off(row["text"], wanted)
            found.append((cut, (min(b[0] for b in run), min(b[1] for b in run), max(b[2] for b in run), max(b[3] for b in run))))
    return [box for _, box in sorted(found, key=lambda f: f[0])]


def _run_from(rows: list[dict], start: int, wanted: str) -> tuple[list[tuple[int, int, int, int]], str]:
    """The boxes and joined text of the words from ``rows[start]`` on that read toward ``wanted``."""
    first = rows[start]
    run, read = [_box(first)], _key(first["text"])
    if read != wanted and _cut_off(first["text"], wanted):
        read = wanted
    for row in rows[start + 1:]:
        if read == wanted or not wanted.startswith(read):
            break
        if _same_line(run[-1], _box(row)) and _beside(run[-1], _box(row)):
            run.append(_box(row))
            read += _key(row["text"])
    return run, read


def _near(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return all(abs(p - q) <= (a[3] - a[1]) / 2 for p, q in zip(a, b))
