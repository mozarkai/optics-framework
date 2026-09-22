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
import json
import os
import shutil
import socket
import subprocess  # nosec B404
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

import yaml
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from optics_framework.helper.engine_requirements import (
    enabled_keys, missing_engines)
from optics_framework.helper.environment import describe as describe_environment
from optics_framework.helper.environment import detect as detect_environment
from optics_framework.helper.setup import (
    ALL_ENGINES, DISTRIBUTION_NAME, engine_for)

_console = Console()

_SOCKET_TIMEOUT_S = 3.0
_ADB_TIMEOUT_S = 10.0

_STATUS_GLYPH = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
_STATUS_STYLE = {"ok": "green", "warn": "yellow", "fail": "red"}
_MAX_NAME_COLUMN = 32

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
                              backend.install_hint))
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
    rows.extend(_engine_rows(data))

    if all(row.status == "ok" for row in rows):
        rows.append(Check("config: project", "ok",
                          "driver, element sources and settings look runnable"))
    return rows


def _engine_rows(data: dict) -> list[Check]:
    """A ⚠️ row per engine this project enables whose package is missing.

    The Engines section is a machine-wide inventory, where a missing package is
    an extra nobody asked for; here the project asked for it, so the row belongs
    with the rest of what the config promises. Same condition, and the same
    words, that ``dry_run``/``execute`` refuse to start on."""
    return [Check(f"config: {item.config_key} engine", "warn",
                  item.detail, item.hint)
            for item in missing_engines(data)]


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


def _project_engine_hints(folder: str | None) -> set[str]:
    """Install commands for every engine the project enables.

    An enabled engine is one the project asked for, wherever in config.yaml it
    sits, so a warning pointing at its install command is not an optional extra
    — the run dies the moment that engine is instantiated. Matching on the hint
    rather than a row name catches it whether the machine-wide Engines row or
    the project's own ``config: <key> engine`` row raised it."""
    if not folder:
        return set()
    data, _ = _load_project_yaml(folder)
    if data is None:
        return set()
    engines = (engine_for(key) for key in enabled_keys(data))
    return {engine.install_hint for engine in engines if engine is not None}


def _enabled_driver_names(folder: str) -> set[str]:
    """Names of the drivers the project's config enables (empty when there is
    no readable config — e.g. a bare `optics doctor` with no folder)."""
    data, _ = _load_project_yaml(folder)
    if data is None:
        return set()
    return set(_enabled_entries(data.get("driver_sources")))


def _mandatory_hints(rows: list[Check], drivers: set[str],
                     engine_hints: set[str]) -> list[str]:
    """Hints of warnings on an ENABLED driver's must-have pieces.

    A warning for something the user never enabled (say, Playwright's browser
    while they drive Appium) is an optional extra; a warning for their own
    driver's server/device/browser blocks the first real run and gets called
    out in the closing message. Order follows the rows, deduplicated. An
    enabled engine's missing Python package blocks just as hard — the server can
    be up and the device attached, and the run still fails on instantiation —
    so ``engine_hints`` carries the install commands the project laid claim to
    (`_project_engine_hints`), drivers and OCR/LLM engines alike."""
    hints: list[str] = []
    for row in rows:
        if row.status != "warn" or not row.hint:
            continue
        mandatory = (
            ("appium" in drivers
             and (row.name == _APPIUM_SERVER
                  or (row.name == _ADB_DEVICES
                      and "no devices attached" in row.detail)))
            or row.hint in engine_hints
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


def _row_text(row: Check, name_width: int) -> Text:
    """One check as a single line: glyph, padded name, detail — no table
    borders to break when a narrow terminal wraps a long detail string."""
    glyph = _STATUS_GLYPH.get(row.status, "•")
    style = _STATUS_STYLE.get(row.status, "")
    text = Text("  ")
    text.append(glyph, style=style)
    text.append(" " * max(1, 3 - cell_len(glyph)))
    text.append(row.name.ljust(name_width))
    text.append("  ")
    text.append(row.detail)
    return text


def _hint_text(hint: str) -> Text:
    text = Text("      → ", style="dim")
    text.append(hint, style="dim")
    return text


def _print_section(title: str, rows: list[Check], name_width: int) -> None:
    if not rows:
        return
    _console.print(f"[bold]{title}[/bold]")
    for row in rows:
        _console.print(_row_text(row, name_width))
        if row.hint:
            _console.print(_hint_text(row.hint))
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

    _console.print("[bold]🩺 optics doctor[/bold]\n")
    for title, section_rows in sections:
        _print_section(title, section_rows, name_width)

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
        ("Web", check_web()),
    ]
    if folder:
        sections.append(("Project", validate_project(folder)))
    elif not os.path.isfile(os.path.join(os.getcwd(), "config.yaml")):
        sections.append(("Project", [Check("project", "warn",
                                           "No config.yaml found here",
                                           _NO_CONFIG_HINT)]))
    rows = [row for _, section_rows in sections for row in section_rows]
    drivers = _enabled_driver_names(folder) if folder else set()
    return Diagnosis(sections,
                     _mandatory_hints(rows, drivers,
                                      _project_engine_hints(folder)),
                     any(row.status == "fail" for row in rows))


def print_diagnosis(diagnosis: Diagnosis) -> None:
    print_report(diagnosis.sections, diagnosis.blocking)


def run_doctor(folder: str | None = None, check: bool = False) -> int:
    """Diagnose and print. Returns 0, or 1 under ``check=True`` when a
    project-config row failed (environment gaps stay warnings — they are
    installable later and must not fail a fresh machine)."""
    diagnosis = diagnose(folder)
    print_diagnosis(diagnosis)
    return 1 if check and diagnosis.failed else 0
