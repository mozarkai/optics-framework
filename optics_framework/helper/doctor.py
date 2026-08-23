"""``optics doctor`` — a friendly environment and project health check.

Doctor answers two beginner questions: "is my machine ready?" and "is my
project set up correctly?". Machine checks (Python, engines, adb/Appium,
browser binaries) are best-effort and report warnings — a fresh machine should
get a readable to-do list, not a wall of failures. Project checks
(``validate_project``) are the strict ones: a structurally broken config.yaml
is reported as a failure and makes ``run_doctor(check=True)`` exit non-zero so
CI can gate on it.

Project configs are parsed with plain ``yaml.safe_load`` — doctor deliberately
never constructs ``ConfigHandler``, because its constructor creates an
``execution_output/`` directory as a side effect and a diagnostic command must
not mutate the project it is inspecting.
"""
import os
import shutil
import socket
import subprocess  # nosec B404
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import NamedTuple
from urllib.parse import urlparse

import yaml
from rich.console import Console
from rich.table import Table

from optics_framework.helper.setup import ALL_ENGINES, DISTRIBUTION_NAME

_console = Console()

_SOCKET_TIMEOUT_S = 3.0
_ADB_TIMEOUT_S = 10.0

_STATUS_GLYPH = {
    "ok": "[green]✅[/green]",
    "warn": "[yellow]⚠️[/yellow] ",
    "fail": "[red]❌[/red]",
}

_ADB_DEVICES = "adb devices"
_APPIUM_SERVER = "appium server"
_PLAYWRIGHT_BROWSER = "playwright browser"
_SELENIUM_WEBDRIVER = "selenium webdriver"
_CONFIG_DRIVER = "config: driver"


class Check(NamedTuple):
    """One row of the doctor report."""
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    hint: str = ""


# --------------------------------------------------------------------------- #
# Environment checks                                                           #
# --------------------------------------------------------------------------- #

def check_core() -> list[Check]:
    """Python and optics-framework itself.

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
    return rows


def check_engines() -> list[Check]:
    """One row per known engine backend, keyed on its primary pip package."""
    rows: list[Check] = []
    for backend in ALL_ENGINES.values():
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
            "adb", "warn", "not found on PATH",
            "Install Android platform-tools (needed to drive Android devices)."))
    else:
        rows.append(Check("adb", "ok", f"found at {adb}"))
        rows.append(_adb_devices_row(adb))
    rows.append(_appium_row(host, port))
    return rows


def check_web() -> list[Check]:
    """Browser-binary presence for the web drivers, best-effort.

    A pip package being installed says nothing about whether its browser was
    downloaded, so anything short of a confirmed binary is a warning."""
    return [_playwright_row(), _selenium_row()]


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


def _playwright_row() -> Check:
    try:
        version("playwright")
    except PackageNotFoundError:
        return Check(_PLAYWRIGHT_BROWSER, "warn",
                     "Playwright package not installed",
                     "optics setup --install playwright")
    if _playwright_chromium_downloaded():
        return Check(_PLAYWRIGHT_BROWSER, "ok", "Chromium download found")
    return Check(_PLAYWRIGHT_BROWSER, "warn",
                 "package installed, but no Chromium download detected",
                 "playwright install chromium  (or: optics setup --install playwright)")


def _playwright_chromium_downloaded() -> bool:
    home = os.path.expanduser("~")
    cache_dirs = (
        os.path.join(home, "Library", "Caches", "ms-playwright"),  # macOS
        os.path.join(home, ".cache", "ms-playwright"),  # Linux
    )
    for base in cache_dirs:
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        if any(name.startswith("chromium") for name in entries):
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

def _load_project_yaml(folder: str) -> tuple[dict | None, str | None]:
    """Read ``<folder>/config.yaml`` with plain yaml.safe_load. Returns
    ``(data, None)`` or ``(None, reason)`` — never constructs ConfigHandler,
    whose constructor would create execution_output/ as a side effect."""
    path = os.path.join(folder, "config.yaml")
    if not os.path.isfile(path):
        return None, f"no config.yaml in {folder}"
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError) as exc:
        return None, f"could not parse config.yaml: {exc}"
    if not isinstance(data, dict):
        return None, "config.yaml is not a mapping"
    return data, None


def _enabled_entries(section: object) -> dict[str, dict]:
    """Map of name → entry for every ``enabled: true`` item in a dependency
    section (a list of single-key mappings)."""
    enabled: dict[str, dict] = {}
    for entry in section or []:
        if isinstance(entry, dict):
            for name, cfg in entry.items():
                if isinstance(cfg, dict) and cfg.get("enabled") is True:
                    enabled[str(name)] = cfg
    return enabled


def validate_project(folder: str) -> list[Check]:
    """Lint ``<folder>/config.yaml`` structurally.

    Checks that exactly one driver is enabled, that it has at least one
    matching ``<driver>_*`` element source, and that the settings needed to
    open a session are present. Failing rows are what ``run_doctor(check=True)``
    turns into a non-zero exit."""
    data, error = _load_project_yaml(folder)
    if error:
        return [Check("config: file", "fail", error,
                      "Re-create the project with `optics quickstart`.")]

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


def _source_row(driver: str, enabled_sources: set[str]) -> Check:
    matching = sorted(s for s in enabled_sources if s.startswith(f"{driver}_"))
    if matching:
        return Check(f"config: {driver} element source", "ok",
                     f"enabled: {', '.join(matching)}")
    return Check(f"config: {driver} element source", "fail",
                 f"no {driver}_* element source enabled",
                 "Enable at least one matching entry under elements_sources.")


def _settings_row(driver: str, cfg: dict) -> Check:
    name = f"config: {driver} settings"
    problems: list[str] = []
    fatal = False
    if driver in ("appium", "selenium") and not cfg.get("url"):
        problems.append("url is missing")
        fatal = True
    caps = cfg.get("capabilities") or {}
    if driver == "appium":
        problems.extend(
            f"{key} missing in capabilities"
            for key in ("deviceName", "platformName")
            if not caps.get(key))
        # iOS launches apps by bundleId — without one XCUITest has nothing to
        # launch, so it is a hard failure; Android keeps the historical
        # appPackage/appActivity requirements at their existing severity.
        if str(caps.get("platformName", "")).lower() == "ios":
            if not caps.get("bundleId"):
                problems.append("bundleId missing in capabilities")
                fatal = True
        else:
            problems.extend(
                f"{key} missing in capabilities"
                for key in ("appPackage", "appActivity")
                if not caps.get(key))
    if driver == "playwright" and not caps.get("browser"):
        problems.append("capabilities.browser missing (chromium is the default)")
    if not problems:
        return Check(name, "ok", "required settings present")
    return Check(name, "fail" if fatal else "warn", "; ".join(problems),
                 "Edit config.yaml in this project folder.")


# --------------------------------------------------------------------------- #
# Report                                                                       #
# --------------------------------------------------------------------------- #

_NO_CONFIG_HINT = ("Run `optics quickstart` to create a project, "
                   "or pass a project folder")

_DRIVER_ROW_NAMES = {
    "selenium": (_SELENIUM_WEBDRIVER,),
    "playwright": (_PLAYWRIGHT_BROWSER,),
}


def _enabled_driver_names(folder: str) -> set[str]:
    """Names of the drivers the project's config enables (empty when there is
    no readable config — e.g. a bare `optics doctor` with no folder)."""
    data, _ = _load_project_yaml(folder)
    if data is None:
        return set()
    return set(_enabled_entries(data.get("driver_sources")))


def _mandatory_hints(rows: list[Check], drivers: set[str]) -> list[str]:
    """Hints of warnings on an ENABLED driver's must-have pieces.

    A warning for something the user never enabled (say, Playwright's browser
    while they drive Appium) is an optional extra; a warning for their own
    driver's server/device/browser blocks the first real run and gets called
    out in the closing message. Order follows the rows, deduplicated."""
    hints: list[str] = []
    for row in rows:
        if row.status != "warn" or not row.hint:
            continue
        mandatory = (
            ("appium" in drivers
             and (row.name == _APPIUM_SERVER
                  or (row.name == _ADB_DEVICES
                      and "no devices attached" in row.detail)))
            or any(row.name in names
                   for driver, names in _DRIVER_ROW_NAMES.items()
                   if driver in drivers)
        )
        if mandatory and row.hint not in hints:
            hints.append(row.hint)
    return hints


def _appium_target(folder: str) -> tuple[str, int] | None:
    """(host, port) of the project's enabled appium url, or None when the
    project doesn't drive appium (or its url is unusable)."""
    data, _ = _load_project_yaml(folder)
    if data is None:
        return None
    for driver, cfg in _enabled_entries(data.get("driver_sources")).items():
        if driver == "appium":
            return _split_host_port(cfg.get("url"))
    return None


def _split_host_port(url: object) -> tuple[str, int] | None:
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        # "//" (not http://) lets urlparse read a bare host:port as a netloc.
        parsed = urlparse(url if "://" in url else f"//{url}")
        return parsed.hostname or "127.0.0.1", parsed.port or 4723
    except ValueError:
        return None


def _print_report(rows: list[Check], mandatory_hints: list[str]) -> None:
    """Print the report table, the counts line and the closing message.

    ``mandatory_hints`` carries the warnings on the ENABLED driver's own
    requirements; when non-empty the reassurance line is replaced by a
    targeted call-to-action listing them."""
    table = Table(title="🩺 optics doctor", header_style="bold")
    table.add_column("")
    table.add_column("Check")
    table.add_column("Details")
    table.add_column("Hint", style="dim")
    for row in rows:
        table.add_row(_STATUS_GLYPH.get(row.status, "•"),
                      row.name, row.detail, row.hint)
    _console.print(table)

    counts = {status: sum(1 for r in rows if r.status == status)
              for status in ("ok", "warn", "fail")}
    _console.print(
        f"{counts['ok']} ok, {counts['warn']} warning(s), {counts['fail']} failure(s)")
    if counts["fail"]:
        _console.print("[red]Fix the ❌ rows above, then re-run optics doctor.[/red]")
    elif counts["warn"]:
        if mandatory_hints:
            _console.print("[yellow]⚠️ Before your first real run:[/yellow]")
            for hint in mandatory_hints:
                _console.print(f"  • {hint}")
        else:
            _console.print(
                "Good enough to start — ⚠️ rows are optional extras you can add later.")
    else:
        _console.print("[green]Everything looks good — happy testing![/green]")


def run_doctor(folder: str | None = None, check: bool = False) -> int:
    """Run every check and print the report. Returns 0, or 1 under
    ``check=True`` when a project-config row failed (environment gaps stay
    warnings — they are installable later and must not fail a fresh machine).

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
    rows = [
        *check_core(),
        *check_engines(),
        *check_mobile(host, port),
        *check_web(),
    ]
    if folder:
        rows.extend(validate_project(folder))
    elif not os.path.isfile(os.path.join(os.getcwd(), "config.yaml")):
        rows.append(Check("project", "warn", "No config.yaml found here",
                          _NO_CONFIG_HINT))
    drivers = _enabled_driver_names(folder) if folder else set()
    _print_report(rows, _mandatory_hints(rows, drivers))
    if check and any(row.status == "fail" for row in rows):
        return 1
    return 0
