"""``optics doctor`` — a friendly environment and project health check.

Doctor answers two beginner questions: "is my machine ready?" and "is my
project set up correctly?". Machine checks (Python, engines, adb/Appium,
browser binaries) are best-effort and report warnings — a fresh machine should
get a readable to-do list, not a wall of failures. Project checks
(``validate_project``) are the strict ones: a structurally broken config.yaml
is reported as a failure and makes ``run_doctor(check=True)`` exit non-zero so
CI can gate on it.

Project configs are parsed with ``load_project_yaml`` — doctor deliberately
never constructs ``ConfigHandler``, because its constructor creates an
``execution_output/`` directory as a side effect and a diagnostic command must
not mutate the project it is inspecting.
"""
import importlib
import json
import os
import platform
import shutil
import socket
import subprocess  # nosec B404
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import urlparse

import yaml
from pydantic import TypeAdapter, ValidationError
from rich.cells import cell_len
from rich.text import Text

from optics_framework.common.config_handler import (
    load_project_yaml,
    resolve_config_file,
)
from optics_framework.common.error import OpticsError
from optics_framework.common.logging_config import escape_control, mask_secrets
from optics_framework.helper.console import can_encode, safe_console
from optics_framework.helper.environment import describe as describe_environment
from optics_framework.helper.environment import detect as detect_environment
from optics_framework.helper import updates
from optics_framework.helper.setup import ALL_ENGINES, DISTRIBUTION_NAME, EngineBackend

if TYPE_CHECKING:
    import airhand

_console = safe_console()

_SOCKET_TIMEOUT_S = 3.0
_ADB_TIMEOUT_S = 10.0

_STATUS_STYLE = {"ok": "green", "warn": "yellow", "fail": "red", "info": "blue"}
_MAX_NAME_COLUMN = 32

_ADB = "adb"
_ADB_DEVICES = "adb devices"
_APPIUM_SERVER = "appium server"
_PLAYWRIGHT_BROWSER = "playwright browser"
_SELENIUM_WEBDRIVER = "selenium webdriver"
_CONFIG_DRIVER = "config: driver"
_CONFIG_FILE = "config: file"
_BLE_DONGLE = "ble dongle"
_BLE_DEVICE = "ble paired device"
_BLE_HOSTS = "ble hosts"
_USB_SCREEN = "usb screenshot"
_ANY_DRIVER_SOURCES = frozenset({"camera_screenshot", "usb_screenshot"})
_AIRHAND_DOCS = "https://github.com/chinmayajha/airhand"
_XA11Y_DRIVER = "xa11y driver"
_XA11Y_PACKAGE = "appium-xa11y-driver"
_XA11Y_INSTALL = f"appium driver install --source=npm {_XA11Y_PACKAGE}"
_MACOS_PERMISSIONS = "macOS permissions"


class _Glyphs(NamedTuple):
    status: dict[str, str]
    width: int
    title: str
    arrow: str
_EMOJI_GLYPHS = _Glyphs({"ok": "✅", "warn": "⚠️", "fail": "❌", "info": "ℹ️"}, 3, "🩺 optics doctor", "→")
_PLAIN_GLYPHS = _Glyphs({"ok": "OK", "warn": "WARN", "fail": "FAIL", "info": "INFO"}, 5, "optics doctor", "->")
def _glyphs() -> _Glyphs:
    emoji = "".join(_EMOJI_GLYPHS.status.values()) + _EMOJI_GLYPHS.title + _EMOJI_GLYPHS.arrow
    return _EMOJI_GLYPHS if can_encode(_console, emoji) else _PLAIN_GLYPHS
class Check(NamedTuple):
    """One row of the doctor report."""
    name: str
    status: str  # "ok" | "warn" | "fail" | "info" (not counted)
    detail: str
    hint: str = ""


# --------------------------------------------------------------------------- #
# Environment checks                                                           #
# --------------------------------------------------------------------------- #

def check_core() -> list[Check]:
    """Python, optics-framework itself, and the install environment.

    The Python row is informational only — it never fails, because the package
    cannot even be imported below the supported pin, so a failing row would
    never be seen anyway."""
    py = sys.version_info
    rows = [
        Check("python", "ok", f"{py.major}.{py.minor}.{py.micro}",
              "informational — optics needs Python 3.12+"),
    ]
    try:
        rows.append(Check("optics-framework", "ok",
                          f"installed, version {version(DISTRIBUTION_NAME)}"))
    except PackageNotFoundError:
        rows.append(Check("optics-framework", "warn",
                          "not installed as a package (source checkout?)",
                          "pip install optics-framework"))
    row = describe_environment(detect_environment())
    if row:
        rows.append(Check("environment", *row))
    rows.extend(check_release())
    return rows


def check_release() -> list[Check]:
    """Whether a newer release is out, from the daily update check; no row when it cannot tell."""
    if not updates.enabled():
        return []
    newer = updates.newer(updates.refresh("cli"))
    if not newer:
        return []
    return [Check("release", "warn", f"Optics {newer[0]} is out", "optics upgrade")]


def check_engines() -> list[Check]:
    """One row per known engine backend, keyed on its primary pip package."""
    rows: list[Check] = []
    for backend in filter(EngineBackend.runs_here, ALL_ENGINES.values()):
        package = backend.packages[0]
        try:
            rows.append(Check(backend.name, "ok", f"{package} {version(package)}"))
        except PackageNotFoundError:
            rows.append(Check(backend.name, "warn", f"{package} not installed",
                              f"optics setup --install {backend.extra}"))
    return rows


def check_mobile(host: str = "127.0.0.1", port: int = 4723) -> list[Check]:
    """Android tooling (adb + attached devices) and Appium reachability.

    Everything here is advisory: adb only matters for Android users and an
    unstarted Appium server is a normal state before the first mobile run."""
    rows: list[Check] = []
    adb = shutil.which("adb")
    if adb is None:
        rows.append(Check(
            _ADB, "warn", "not found on PATH",
            "Install Android platform-tools (needed to drive Android devices)."))
    else:
        rows.append(Check(_ADB, "ok", f"found at {adb}"))
        rows.append(_adb_devices_row(adb))
    rows.append(_appium_row(host, port))
    return rows


def check_web(playwright_remote: bool = False) -> list[Check]:
    return [_playwright_row(playwright_remote), _selenium_row()]


def check_desktop(host: str = "127.0.0.1", port: int = 4723, system: str | None = None) -> list[Check]:
    """Appium reachability, the xa11y driver in APPIUM_HOME, and on macOS the privacy permissions it needs."""
    server = _appium_row(host, port)
    rows = [server, _xa11y_row(_appium_home(), server.status == "ok")]
    if (system or platform.system()) == "Darwin":
        rows.append(Check(
            _MACOS_PERMISSIONS, "info", "not checked by doctor",
            "System Settings > Privacy & Security: give Accessibility, and Screen Recording for screenshots, "
            "to the Node.js that runs Appium (setup.sh shows its path), or to your terminal if you start Appium "
            "in it. Then restart Appium: launchctl kickstart -k gui/$(id -u)/appium-xa11y"))
    return rows


def _appium_home() -> Path:
    return Path(os.environ.get("APPIUM_HOME") or os.path.expanduser(os.path.join("~", ".appium")))


def _xa11y_row(home: Path, server_up: bool) -> Check:
    found = next(filter(None, map(_xa11y_version, _xa11y_paths(home))), None)
    if found:
        return Check(_XA11Y_DRIVER, "ok", f"{_XA11Y_PACKAGE} {found} in {home}")
    hint = _XA11Y_INSTALL if shutil.which("appium") else f"npm install -g appium, then {_XA11Y_INSTALL}"
    if server_up:
        return Check(_XA11Y_DRIVER, "warn", f"not found in {home}; the running server may use another APPIUM_HOME",
                     f"{hint}, or set APPIUM_HOME to the folder the server uses and run optics doctor again")
    return Check(_XA11Y_DRIVER, "warn", f"not installed in {home}", hint)


def _xa11y_paths(home: Path) -> list[Path]:
    """Where Appium loads the driver from: its manifest entry, else the package Appium adds to the manifest at start."""
    modules = home / "node_modules"
    try:
        manifest = yaml.safe_load((modules / ".cache" / "appium" / "extensions.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        manifest = None
    drivers = manifest.get("drivers") if isinstance(manifest, dict) else None
    listed = [Path(entry["installPath"]) for entry in (drivers.values() if isinstance(drivers, dict) else ())
              if isinstance(entry, dict) and entry.get("pkgName") == _XA11Y_PACKAGE and entry.get("installPath")]
    return listed or [modules / _XA11Y_PACKAGE]


def _xa11y_version(package: Path) -> str | None:
    try:
        data = json.loads((package / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("name") != _XA11Y_PACKAGE:
        return None
    return str(data.get("version") or "(no version)")


def _adb_devices_row(adb: str) -> Check:
    try:
        result = subprocess.run(  # nosec B603
            [adb, "devices"], capture_output=True, text=True,
            timeout=_ADB_TIMEOUT_S, check=False, shell=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return Check(_ADB_DEVICES, "warn", f"could not run adb: {exc}",
                     "Check your platform-tools installation.")
    serials = _parse_adb_devices(result.stdout)
    if serials is None:
        first_line = next((line.strip() for line in result.stdout.splitlines()
                           if line.strip()), "(no output)")
        return Check(_ADB_DEVICES, "warn",
                     f"unexpected adb output: {first_line}",
                     "Check your platform-tools installation.")
    if serials:
        return Check(_ADB_DEVICES, "ok",
                     f"{len(serials)} connected: {', '.join(serials)}")
    return Check(_ADB_DEVICES, "warn", "no devices attached",
                 "Connect a device or start an emulator (USB debugging on).")


def _parse_adb_devices(stdout: str) -> list[str] | None:
    """Serials in the ``device`` state from `adb devices` output.

    Returns None when the device-list header is absent. Real adb prints
    "List of devices attached"; anything else is a daemon banner or an error
    message, which must not be mistaken for "zero devices attached"."""
    serials: list[str] = []
    in_list = False
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("List of devices attached"):
            in_list = True
            continue
        if in_list and stripped:
            parts = stripped.split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
    return serials if in_list else None


def _appium_row(host: str, port: int) -> Check:
    try:
        socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT_S).close()
    except OSError:
        return Check(_APPIUM_SERVER, "warn", f"not reachable at {host}:{port}",
                     "Start it in another terminal with the `appium` command.")
    return Check(_APPIUM_SERVER, "ok", f"reachable at {host}:{port}")


def _playwright_row(remote: bool = False) -> Check:
    try:
        version("playwright")
    except PackageNotFoundError:
        return Check(_PLAYWRIGHT_BROWSER, "warn",
                     "Playwright package not installed",
                     "optics setup --install playwright")
    if remote:
        return Check(_PLAYWRIGHT_BROWSER, "ok", "remote browser configured; no local download needed")
    if _playwright_chromium_downloaded():
        return Check(_PLAYWRIGHT_BROWSER, "ok", "Chromium download found")
    return Check(_PLAYWRIGHT_BROWSER, "warn",
                 "package installed, but its Chromium build is not downloaded",
                 "playwright install chromium  (or: optics setup --install playwright)")


def _playwright_browser_dirs() -> list[str]:
    """Directories Playwright may keep its browser downloads in."""
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return [override]
    home = os.path.expanduser("~")
    return [
        os.path.join(home, "Library", "Caches", "ms-playwright"),  # macOS
        os.path.join(home, ".cache", "ms-playwright"),  # Linux
        os.path.join(home, "AppData", "Local", "ms-playwright"),  # Windows
    ]


def _playwright_chromium_revision() -> str | None:
    """Chromium build the installed Playwright expects, from its own manifest.

    Playwright refuses to launch any build but the one it pins, so a leftover
    download from an earlier version is not a usable browser."""
    try:
        import playwright
        manifest = (Path(playwright.__file__).parent / "driver" / "package"
                    / "browsers.json")
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (ImportError, OSError, ValueError):
        return None
    for browser in data.get("browsers", []):
        if browser.get("name") == "chromium":
            revision = browser.get("revision")
            return str(revision) if revision else None
    return None


def _playwright_chromium_downloaded() -> bool:
    revision = _playwright_chromium_revision()
    for base in _playwright_browser_dirs():
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        if revision is None:
            if any(name.startswith("chromium") for name in entries):
                return True
        elif f"chromium-{revision}" in entries:
            return True
    return False


def _selenium_row() -> Check:
    try:
        version("selenium")
    except PackageNotFoundError:
        return Check(_SELENIUM_WEBDRIVER, "warn",
                     "Selenium package not installed",
                     "optics setup --install selenium")
    driver = shutil.which("chromedriver") or shutil.which("geckodriver")
    if driver:
        return Check(_SELENIUM_WEBDRIVER, "ok", f"driver found at {driver}")
    return Check(_SELENIUM_WEBDRIVER, "warn",
                 "package installed, but no WebDriver binary on PATH",
                 "Install a browser with a matching driver (e.g. chromedriver).")


# --------------------------------------------------------------------------- #
# Project checks                                                               #
# --------------------------------------------------------------------------- #

def _load_project_yaml(folder: str) -> tuple[dict | None, Check | None]:
    """Read ``<folder>/config.yaml`` with load_project_yaml and resolve its
    environment references and relative paths. Returns
    ``(data, None)`` or ``(None, failing row)`` — never constructs ConfigHandler,
    whose constructor would create execution_output/ as a side effect."""
    path = os.path.join(folder, "config.yaml")
    recreate = "Re-create the project with `optics quickstart`."
    if not os.path.isfile(path):
        return None, Check(_CONFIG_FILE, "fail", f"no config.yaml in {folder}", recreate)
    try:
        data = load_project_yaml(path) or {}
    except (yaml.YAMLError, OSError) as exc:
        return None, Check(_CONFIG_FILE, "fail", f"could not parse config.yaml: {exc}", recreate)
    if not isinstance(data, dict):
        return None, Check(_CONFIG_FILE, "fail", "config.yaml is not a mapping", recreate)
    try:
        return resolve_config_file(data, folder), None
    except OpticsError as exc:
        return None, Check("config: references", "fail", exc.message,
                           "Set the variable or add the file, then re-run.")


def _enabled_entries(section: object) -> dict[str, dict]:
    """Map of name → entry for every ``enabled: true`` item in a dependency
    section (a list of single-key mappings)."""
    enabled: dict[str, dict] = {}
    for entry in section or []:
        if isinstance(entry, dict):
            for name, cfg in entry.items():
                if isinstance(cfg, dict) and _is_enabled(cfg.get("enabled")):
                    enabled[str(name)] = cfg
    return enabled


def _is_enabled(value: object) -> bool:
    """``value`` read with the bool rules ``Config`` applies, so ``enabled: ${USE_APPIUM}`` counts."""
    try:
        return TypeAdapter(bool).validate_python(value)
    except ValidationError:
        return False


def validate_project(folder: str) -> list[Check]:
    """Lint ``<folder>/config.yaml`` structurally.

    Checks that exactly one driver is enabled, that it has at least one
    matching ``<driver>_*`` element source, and that the settings needed to
    open a session are present. Failing rows are what ``run_doctor(check=True)``
    turns into a non-zero exit."""
    data, problem = _load_project_yaml(folder)
    if problem:
        return [problem]

    rows: list[Check] = []
    drivers = _enabled_entries(data.get("driver_sources"))
    sources = set(_enabled_entries(data.get("elements_sources")))
    if not drivers:
        rows.append(Check(
            _CONFIG_DRIVER, "fail", "no driver enabled",
            "Set enabled: true under exactly one driver_sources entry."))
    elif len(drivers) > 1:
        rows.append(Check(
            _CONFIG_DRIVER, "warn", f"{len(drivers)} drivers enabled "
            f"({', '.join(sorted(drivers))})",
            "Enable exactly one unless you deliberately want driver fallback."))
    else:
        rows.append(Check(_CONFIG_DRIVER, "ok",
                          f"{next(iter(drivers))} enabled"))

    for driver, cfg in sorted(drivers.items()):
        rows.append(_source_row(driver, sources))
        rows.append(_settings_row(driver, cfg))

    if all(row.status == "ok" for row in rows):
        rows.append(Check("config: project", "ok",
                          "driver, element sources and settings look runnable"))
    return rows


def _appium_platforms() -> Any:
    try:
        from optics_framework.engines.drivers import appium_platforms
    except ImportError:  # no Appium client: the Engines section names the install
        return None
    return appium_platforms


def _promoted(caps: dict) -> dict:
    """The capabilities as the Appium server reads them, so that every check reads appium:options the same way."""
    platforms = _appium_platforms()
    return platforms.promote_appium_options(caps) if platforms else caps


def _cap(caps: dict, key: str) -> Any:
    return caps.get(key) or caps.get(f"appium:{key}")


def _desktop_problem(caps: dict) -> tuple[list[str], bool] | None:
    """None off a desktop; else the missing app as the driver words it, since a desktop needs no device or package."""
    platforms = _appium_platforms()
    if platforms is None:
        return None
    profile = platforms.get_profile(_cap(caps, "platformName"))
    if profile is None or not profile.app_at_start:
        return None
    try:
        platforms.require_app_at_start(caps)
    except OpticsError as e:
        session = platforms.session_to_attach(caps)
        if session:
            return [f"no app: the start attaches to session {session}, and fails once that session has ended"], False
        return [e.message], True
    return [], False


def _source_row(driver: str, enabled_sources: set[str]) -> Check:
    matching = sorted(s for s in enabled_sources
                      if s.startswith(f"{driver}_") or s in _ANY_DRIVER_SOURCES)
    if matching:
        return Check(f"config: {driver} element source", "ok",
                     f"enabled: {', '.join(matching)}")
    if driver == "ble":
        return Check("config: ble element source", "warn",
                     "no screen source: only keywords that do not read the screen run",
                     "Enable camera_screenshot or usb_screenshot under elements_sources to read the screen.")
    return Check(f"config: {driver} element source", "fail",
                 f"no {driver}_* element source enabled",
                 "Enable at least one matching entry under elements_sources.")


def _settings_row(driver: str, cfg: dict) -> Check:
    name = f"config: {driver} settings"
    caps = cfg.get("capabilities") or {}
    problems, fatal = _appium_problems(caps) if driver == "appium" else ([], False)
    if driver in ("appium", "selenium") and not cfg.get("url"):
        problems, fatal = ["url is missing", *problems], True
    if driver == "playwright" and not caps.get("browser"):
        problems.append("capabilities.browser missing (chromium is the default)")
    if driver == "ble":
        problems.extend(_ble_problems(caps))
        fatal = bool(problems)
    if not problems:
        return Check(name, "ok", "required settings present")
    return Check(name, "fail" if fatal else "warn", "; ".join(problems),
                 "Edit config.yaml in this project folder.")


def _ble_problems(caps: Any) -> list[str]:
    from optics_framework.engines.drivers.ble import Capabilities

    try:
        Capabilities.model_validate(caps)
    except ValidationError as exc:
        return [f"{'.'.join(map(str, error['loc'])) or 'capabilities'}: {error['msg']}"
                for error in exc.errors()]
    return []


def _airhand() -> Any:
    """airhand when it is installed, imported at each check, so a package that quickstart has just installed is seen."""
    importlib.invalidate_caches()
    try:
        return importlib.import_module("airhand")
    except ImportError:
        return None


def check_ble(caps: Any) -> list[Check]:
    """The dongle the ble driver opens, and whether a paired device holds an encrypted link to it."""
    airhand = _airhand()
    # The settings row reports capabilities that are not a mapping.
    if airhand is None or not isinstance(caps, dict):
        return []
    serial, port = caps.get("serial"), caps.get("port")
    if serial is not None and port is not None:
        return []
    try:
        dongle = airhand.Dongle(serial, port=port)
    except ValueError as exc:
        return [Check(_BLE_DONGLE, "warn", str(exc), "Set capabilities.serial in config.yaml.")]
    except airhand.DongleLost as exc:
        return [Check(_BLE_DONGLE, "warn", str(exc), "Plug in the airhand dongle named in config.yaml.")]
    except airhand.WrongFirmware as exc:
        return [Check(_BLE_DONGLE, "warn", str(exc), f"Install the firmware for this airhand version: {_AIRHAND_DOCS}#quick-start")]
    with dongle:
        try:
            status = dongle.status()
        except airhand.AirhandError as exc:
            return [Check(_BLE_DONGLE, "warn", str(exc), "Replug the dongle, then run doctor again.")]
        rows = [Check(_BLE_DONGLE, "ok", dongle.identity)]
        try:
            hosts: list[airhand.Host] | None = dongle.hosts()
        except airhand.Unsupported:
            hosts = None
        except airhand.AirhandError as exc:
            return rows + [Check(_BLE_DEVICE, "warn", str(exc), "Replug the dongle, then run doctor again.")]
    if hosts:
        rows.append(Check(_BLE_HOSTS, "ok", ", ".join(_host_label(h) for h in hosts)))
    host_row = _host_row(caps.get("host"), hosts)
    if host_row:
        rows.append(host_row)
    return rows + [_link_row(status, hosts or [])]


def _link_row(status: dict[str, str], hosts: "list[airhand.Host]") -> Check:
    if status.get("conn") != "yes":
        return Check(_BLE_DEVICE, "warn", "no device connected", "Select airhand in the device's Bluetooth settings.")
    if int(status.get("sec", "0")) < 2:
        return Check(_BLE_DEVICE, "warn", "connected, but the link is not encrypted",
                     "Forget airhand in the device's Bluetooth settings and pair again.")
    linked = next((h.name or h.address for h in hosts if h.connected), None)
    return Check(_BLE_DEVICE, "ok", f"{linked} connected and encrypted" if linked else "connected and encrypted")


def _host_label(host: "airhand.Host") -> str:
    marks = [mark for mark, on in (("connected", host.connected), ("chosen", host.chosen)) if on]
    return f"{host.name or '?'} ({host.address})" + (f" [{', '.join(marks)}]" if marks else "")


def _host_row(host: Any, hosts: "list[airhand.Host] | None") -> Check | None:
    """A failure when capabilities.host is one the dongle cannot choose, so the session would stop with E0501."""
    if host is None:
        return None
    if not isinstance(host, str) or not host.strip():
        return Check(_BLE_HOSTS, "fail", f"capabilities.host must be a device name or address, not {host!r}",
                     "Set capabilities.host to a paired device's name or address.")
    if hosts is None:
        return Check(_BLE_HOSTS, "fail", "the dongle's firmware cannot choose a host",
                     "Run airhand update, or remove capabilities.host.")
    airhand = _airhand()
    try:
        airhand.find_host(hosts, host)
    except airhand.Refused as exc:
        return Check(_BLE_HOSTS, "fail", str(exc), "Pair the device with airhand once, or fix capabilities.host.")
    return None


def check_usb_screen(usb_caps: dict | None, ble_caps: dict | None) -> list[Check]:
    """Whether usb_screenshot finds its tool and one phone, and takes a screenshot the size of the ble frame."""
    if usb_caps is None:
        return []
    if not isinstance(usb_caps, dict):
        return [Check(_USB_SCREEN, "fail", f"usb_screenshot capabilities must be a mapping, not {usb_caps!r}",
                      "Set capabilities to keys such as serial: or remove them.")]
    ble_caps = ble_caps if isinstance(ble_caps, dict) else {}
    from optics_framework.common.image_decode import image_size
    from optics_framework.engines.elementsources import usb_screenshot

    platform = usb_caps.get("platform") or ble_caps.get("platform")
    usb = usb_screenshot.PLATFORMS.get(platform)
    if usb is None:
        return [Check(_USB_SCREEN, "fail", f"platform {platform} has no USB screenshots",
                      "Set capabilities.platform to android or ios, or use camera_screenshot.")]
    if shutil.which(usb.tool) is None:
        return [Check(_USB_SCREEN, "fail", usb.missing, "")]
    try:
        source = usb_screenshot.UsbScreenshot({"capabilities": {**usb_caps, "platform": platform}})
        width, height = image_size(source.capture_screenshot_bytes())
    except OpticsError as exc:
        return [Check(_USB_SCREEN, "fail", exc.message or str(exc), usb.hint)]
    frame = ble_caps.get("frame") if isinstance(ble_caps.get("frame"), dict) else None
    if frame is not None and (frame.get("width"), frame.get("height")) != (width, height):
        return [Check(_USB_SCREEN, "fail", f"the screenshot is {width}x{height}, but the ble frame is {frame.get('width')}x{frame.get('height')}",
                      f"Set the ble frame to {{width: {width}, height: {height}}}.")]
    return [Check(_USB_SCREEN, "ok", f"{width}x{height} from {source.serial}")]


def _usb_caps(folder: str | None) -> dict | None:
    data, _ = _load_project_yaml(folder) if folder else (None, None)
    cfg = _enabled_entries((data or {}).get("elements_sources")).get("usb_screenshot")
    return None if cfg is None else cfg.get("capabilities") or {}


def _ble_caps(folder: str | None) -> dict | None:
    data, _ = _load_project_yaml(folder) if folder else (None, None)
    if data is None:
        return None
    cfg = _enabled_entries(data.get("driver_sources")).get("ble")
    return None if cfg is None else cfg.get("capabilities") or {}


def _appium_problems(caps: dict) -> tuple[list[str], bool]:
    """The missing capabilities, and whether one stops the session from starting."""
    caps = _promoted(caps)
    desktop = _desktop_problem(caps)
    if desktop is not None:
        return desktop
    problems = [f"{key} missing in capabilities" for key in ("deviceName", "platformName") if not _cap(caps, key)]
    # iOS launches apps by bundleId — without one XCUITest has nothing to
    # launch, so it is a hard failure; Android keeps the historical
    # appPackage/appActivity requirements at their existing severity.
    if str(_cap(caps, "platformName") or "").lower() == "ios":
        stops = not _cap(caps, "bundleId")
        return problems + (["bundleId missing in capabilities"] if stops else []), stops
    return problems + [f"{key} missing in capabilities" for key in ("appPackage", "appActivity") if not _cap(caps, key)], False


# --------------------------------------------------------------------------- #
# Report                                                                       #
# --------------------------------------------------------------------------- #

_NO_CONFIG_HINT = ("Run `optics quickstart` to create a project, "
                   "or pass a project folder")

_DRIVER_ROW_NAMES = {
    "selenium": (_SELENIUM_WEBDRIVER,),
    "playwright": (_PLAYWRIGHT_BROWSER,),
    "ble": (_BLE_DONGLE, _BLE_DEVICE),
}


def _engine_row_names_by_driver() -> dict[str, str]:
    """Driver config key → the Engines row that carries its Python client.

    An engine's ``extra`` is also its ``config.yaml`` source key."""
    return {engine.extra: engine.name for engine in ALL_ENGINES.values()}


def _enabled_driver_names(folder: str) -> set[str]:
    """Names of the drivers the project's config enables (empty when there is
    no readable config — e.g. a bare `optics doctor` with no folder)."""
    data, _ = _load_project_yaml(folder)
    if data is None:
        return set()
    return set(_enabled_entries(data.get("driver_sources")))


def _mandatory_hints(rows: list[Check], drivers: set[str], target: str = "android") -> list[str]:
    """Hints of warnings on an ENABLED driver's must-have pieces.

    A warning for something the user never enabled (say, Playwright's browser
    while they drive Appium) is an optional extra; a warning for their own
    driver's server/device/browser blocks the first real run and gets called
    out in the closing message. Order follows the rows, deduplicated. An
    enabled driver's missing Python client blocks just as hard — the server can
    be up and the device attached, and the run still fails on instantiation.
    An attached adb device is a must-have only when the project drives Android,
    and the xa11y driver only when it drives a desktop."""
    required = _required_rows(drivers, target)
    hints: list[str] = []
    for row in rows:
        if row.name in required and row.status == "warn" and row.hint and row.hint not in hints:
            hints.append(row.hint)
    return hints


def _required_rows(drivers: set[str], target: str) -> set[str]:
    engine_rows = _engine_row_names_by_driver()
    required = {engine_rows[driver] for driver in drivers if driver in engine_rows}
    required.update(name for driver, names in _DRIVER_ROW_NAMES.items() if driver in drivers for name in names)
    if "appium" in drivers:
        required.update((_APPIUM_SERVER, *_target_rows().get(target, ())))
    return required


def _target_rows() -> dict[str, tuple[str, ...]]:
    platforms = _appium_platforms()
    desktops = platforms.DESKTOP if platforms else ()
    return {"android": (_ADB, _ADB_DEVICES), **{name: (_XA11Y_DRIVER,) for name in desktops}}


def _appium_entry(folder: str) -> dict | None:
    data, _ = _load_project_yaml(folder)
    if data is None:
        return None
    cfg = _enabled_entries(data.get("driver_sources")).get("appium")
    return cfg if isinstance(cfg, dict) else None


def _appium_platform(folder: str) -> str:
    """The project's appium platformName, lower case; a project with no platformName gets the Android checks."""
    caps = _promoted((_appium_entry(folder) or {}).get("capabilities") or {})
    return str(_cap(caps, "platformName") or "android").strip().lower()


def _appium_target(folder: str) -> tuple[str, int] | None:
    """(host, port) of the project's enabled appium url, or None when the
    project doesn't drive appium (or its url is unusable)."""
    cfg = _appium_entry(folder)
    return _split_host_port(cfg.get("url")) if cfg is not None else None


def _playwright_is_remote(folder: str | None) -> bool:
    """Whether an enabled playwright driver entry carries a url, so the browser is remote."""
    data, _ = _load_project_yaml(folder) if folder else (None, None)
    if data is None:
        return False
    cfg = _enabled_entries(data.get("driver_sources")).get("playwright")
    return isinstance(cfg, dict) and bool(cfg.get("url"))


def _split_host_port(url: object) -> tuple[str, int] | None:
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        # "//" (not http://) lets urlparse read a bare host:port as a netloc.
        parsed = urlparse(url if "://" in url else f"//{url}")
        return parsed.hostname or "127.0.0.1", parsed.port or 4723
    except ValueError:
        return None


def _row_text(row: Check, name_width: int, glyphs: _Glyphs) -> Text:
    """One check as a single line: glyph, padded name, detail — no table
    borders to break when a narrow terminal wraps a long detail string."""
    glyph = glyphs.status.get(row.status, "•")
    style = _STATUS_STYLE.get(row.status, "")
    text = Text("  ")
    text.append(glyph, style=style)
    text.append(" " * max(1, glyphs.width - cell_len(glyph)))
    text.append(row.name.ljust(name_width))
    text.append("  ")
    text.append(row.detail)
    return text


def _hint_text(hint: str, arrow: str) -> Text:
    text = Text(f"      {arrow} ", style="dim")
    text.append(hint, style="dim")
    return text


def _print_section(title: str, rows: list[Check], name_width: int, glyphs: _Glyphs) -> None:
    if not rows:
        return
    _console.print(f"[bold]{title}[/bold]")
    for row in rows:
        _console.print(_row_text(row, name_width, glyphs))
        if row.hint:
            _console.print(_hint_text(row.hint, glyphs.arrow))
    _console.print()


def print_report(sections: list[tuple[str, list[Check]]],
                   mandatory_hints: list[str]) -> None:
    """Print the report as grouped, left-aligned lines (not a bordered
    table), the counts line and the closing message.

    A four-column table sized wider than the visible terminal/pane wraps its
    box-drawing characters mid-line and comes out jagged; plain lines instead
    degrade gracefully — a long detail or hint just wraps as text.

    ``mandatory_hints`` carries the warnings on the ENABLED driver's own
    requirements; when non-empty the reassurance line is replaced by a
    targeted call-to-action listing them."""
    rows = [row for _, section_rows in sections for row in section_rows]
    name_width = min(max((len(row.name) for row in rows), default=0),
                      _MAX_NAME_COLUMN)

    glyphs = _glyphs()
    _console.print(f"[bold]{glyphs.title}[/bold]\n")
    for title, section_rows in sections:
        _print_section(title, section_rows, name_width, glyphs)

    counts = {status: sum(1 for r in rows if r.status == status)
              for status in ("ok", "warn", "fail")}
    _console.print(
        f"{counts['ok']} ok, {counts['warn']} warning(s), {counts['fail']} failure(s)")
    if counts["fail"]:
        _console.print(f"[red]Fix the {glyphs.status['fail']} rows above, then re-run optics doctor.[/red]")
    elif counts["warn"]:
        if mandatory_hints:
            _console.print(f"[yellow]{glyphs.status['warn']} Before your first real run:[/yellow]")
            for hint in mandatory_hints:
                _console.print(Text("  • ").append(hint))
        else:
            _console.print(
                f"Good enough to start — {glyphs.status['warn']} rows are optional extras you can add later.")
    else:
        _console.print("[green]Everything looks good — happy testing![/green]")


class Diagnosis(NamedTuple):
    """The outcome of a doctor run, so a caller can print it *and* act on it
    without paying for the checks twice — several shell out to adb or open a
    socket."""

    sections: list[tuple[str, list[Check]]]
    blocking: list[str]
    failed: bool

    @property
    def ready(self) -> bool:
        return not self.failed and not self.blocking


def diagnose(folder: str | None = None) -> Diagnosis:
    """Run every check and collect the result without printing anything.

    With ``folder``, the Appium probe targets that project's enabled appium
    url (falling back to 127.0.0.1:4723) and ``validate_project`` rows are
    appended.

    Without ``folder``, the diagnosed folder is the current directory: when it
    holds no config.yaml a ⚠️ row says so (a warning, never a --check
    failure)."""
    host, port = "127.0.0.1", 4723
    if folder:
        target = _appium_target(folder)
        if target:
            host, port = target
    sections: list[tuple[str, list[Check]]] = [
        ("Core", check_core()),
        ("Engines", check_engines()),
        ("Mobile", check_mobile(host, port)),
        ("Desktop", check_desktop(host, port)),
        ("Web", check_web(_playwright_is_remote(folder))),
    ]
    ble_caps = _ble_caps(folder)
    usb_rows = check_usb_screen(_usb_caps(folder), ble_caps)
    if ble_caps is not None:
        sections.append(("Bluetooth", check_ble(ble_caps) + usb_rows))
    elif usb_rows:
        sections.append(("Screen over USB", usb_rows))
    if folder:
        sections.append(("Project", validate_project(folder)))
    elif not os.path.isfile(os.path.join(os.getcwd(), "config.yaml")):
        sections.append(("Project", [Check("project", "warn",
                                           "No config.yaml found here",
                                           _NO_CONFIG_HINT)]))
    rows = [row for _, section_rows in sections for row in section_rows]
    drivers = _enabled_driver_names(folder) if folder else set()
    # Hints read the raw row names, so a secret that matches a name cannot hide a blocking warning.
    target = _appium_platform(folder) if folder else "android"
    blocking = [_literal(hint) for hint in _mandatory_hints(rows, drivers, target)]
    sections = [(title, [_literal_check(row) for row in section_rows]) for title, section_rows in sections]
    return Diagnosis(sections, blocking, any(row.status == "fail" for row in rows))


def _literal(text: str) -> str:
    return escape_control(mask_secrets(text))


def _literal_check(row: Check) -> Check:
    """``row`` with secrets masked and control characters escaped, since project files supply its text."""
    return row._replace(name=_literal(row.name), detail=_literal(row.detail), hint=_literal(row.hint))


def print_diagnosis(diagnosis: Diagnosis) -> None:
    print_report(diagnosis.sections, diagnosis.blocking)


def run_doctor(folder: str | None = None, check: bool = False) -> int:
    """Diagnose and print. Returns 0, or 1 under ``check=True`` when a
    project-config row failed (environment gaps stay warnings — they are
    installable later and must not fail a fresh machine)."""
    diagnosis = diagnose(folder)
    print_diagnosis(diagnosis)
    return 1 if check and diagnosis.failed else 0
