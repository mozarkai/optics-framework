"""Environment diagnostics and engine discovery for the Optics MCP server.

Dependency-light helpers backing the MCP onboarding/reliability tools:

- ``run_doctor`` — one-call environment check (Python, ``[mcp]`` extra, adb + the
  Android SDK, connected devices, an optional Appium probe) with an actionable
  ``fix`` per failing check instead of a stack trace.
- ``list_connected_devices`` / ``list_available_sources`` / ``default_sources_for_driver``
  — device and engine discovery so an agent never has to shell out or read the
  framework's source to learn the right ``elements_sources`` module names.
- ``get_foreground_app`` / ``list_installed_packages`` — Android-only, best-effort
  ``adb`` wrappers used by the ``get_current_app`` / ``list_installed_apps`` tools.

The subprocess wrappers mirror ``helper/live.py``'s device discovery: fixed argv,
no shell, short timeouts, and a missing binary degrades to an empty/error result
rather than raising. Reflection over the engine packages is name-level only (no
engine imports), so a missing optional extra never turns discovery into an
ImportError.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import subprocess  # nosec B404 - fixed-argv adb / idevice_id device + app probes only
from typing import Any, Optional

from optics_framework.common.factories import (
    ElementSourceFactory,
    ImageFactory,
    TextFactory,
)
from optics_framework.helper.version import VERSION

_ADB = "adb"
_IDEVICE_ID = "idevice_id"

# Priority order used when ordering a driver's element sources (mirrors the
# StrategyFactory priority: find_element < page_source < screenshot).
_SOURCE_RANK = {"find_element": 0, "page_source": 1, "screenshot": 2}

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})


# --------------------------------------------------------------------------- #
# subprocess helpers (best-effort, never raise)
# --------------------------------------------------------------------------- #
def _run_tool(argv: list[str], timeout: float = 10.0) -> Optional[str]:
    """Run a fixed-argv command, returning stdout or ``None`` on any failure.

    ``None`` distinguishes "tool missing / errored" from an empty-but-successful
    result (a caller that needs the distinction checks for ``None``).
    """
    try:
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell, tool from PATH
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return completed.stdout


def _parse_adb_devices(output: str) -> list[str]:
    """Serials of ready Android devices from ``adb devices`` output.

    Keeps only rows whose second column is exactly ``device`` (drops the header
    line and ``offline`` / ``unauthorized`` rows).
    """
    devices: list[str] = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def _parse_idevice_ids(output: str) -> list[str]:
    """UDIDs from ``idevice_id -l`` output (one per line, trailing suffix tolerated)."""
    devices: list[str] = []
    for line in output.splitlines():
        tokens = line.split()
        if tokens:
            devices.append(tokens[0])
    return devices


def list_android_devices() -> list[str]:
    """Connected Android device serials via ``adb devices`` (``[]`` if adb absent)."""
    output = _run_tool([_ADB, "devices"])
    return _parse_adb_devices(output) if output is not None else []


def list_ios_devices() -> list[str]:
    """Connected iOS UDIDs via ``idevice_id -l`` (``[]`` if libimobiledevice absent)."""
    output = _run_tool([_IDEVICE_ID, "-l"])
    return _parse_idevice_ids(output) if output is not None else []


def list_connected_devices() -> list[dict[str, str]]:
    """All connected mobile devices as ``{"udid", "platform"}`` dicts.

    Best-effort and silent: an empty list can mean "nothing connected" or "adb /
    idevice_id not installed" — ``run_doctor`` reports the binary's presence so a
    caller can tell the two apart.
    """
    devices: list[dict[str, str]] = [
        {"udid": serial, "platform": "android"} for serial in list_android_devices()
    ]
    devices.extend({"udid": udid, "platform": "ios"} for udid in list_ios_devices())
    return devices


# --------------------------------------------------------------------------- #
# engine / source discovery (name-level reflection, no engine imports)
# --------------------------------------------------------------------------- #
def _iter_module_names(package: str) -> list[str]:
    """Public submodule names of an engine package (sorted; ``[]`` if unimportable)."""
    try:
        pkg = importlib.import_module(package)
    except Exception:  # pragma: no cover - defensive; package is always importable
        return []
    return sorted(
        m.name for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("_")
    )


def _source_driver(module_name: str) -> str:
    """Driver an element-source module pairs with, inferred from its filename prefix.

    The naming convention (``appium_*`` / ``selenium_*`` / ``playwright_*``) is
    authoritative and matches each source's ``REQUIRED_DRIVER_TYPE``; using the
    prefix avoids importing modules that pull optional third-party deps.
    """
    return module_name.split("_", 1)[0]


def _order_element_sources(names: list[str]) -> list[str]:
    """Order element sources by strategy priority (find_element, page_source, screenshot)."""

    def rank(name: str) -> tuple[int, str]:
        suffix = name.split("_", 1)[1] if "_" in name else name
        return (_SOURCE_RANK.get(suffix, len(_SOURCE_RANK)), name)

    return sorted(names, key=rank)


def default_sources_for_driver(driver: str) -> list[str]:
    """Sane default ``elements_sources`` for a driver, by reflecting installed sources.

    Returns the driver's ``{driver}_find_element/_page_source/_screenshot`` trio in
    priority order, or ``[]`` when no element source matches the driver (e.g.
    ``ble``) — the caller then falls back to whatever it was given.
    """
    key = (driver or "").strip().lower()
    matches = [
        name
        for name in _iter_module_names(ElementSourceFactory.DEFAULT_PACKAGE)
        if _source_driver(name) == key
    ]
    return _order_element_sources(matches)


def list_available_sources(driver: Optional[str] = None) -> dict[str, Any]:
    """Available engine sources by category, for the discovery MCP tool.

    Element sources are bucketed by their driver; when ``driver`` is given, only
    that driver's sources are returned (empty list if the driver has none). Text
    and image detectors are driver-agnostic and returned as flat lists.
    """
    elements = _iter_module_names(ElementSourceFactory.DEFAULT_PACKAGE)
    by_driver: dict[str, list[str]] = {}
    for name in elements:
        by_driver.setdefault(_source_driver(name), []).append(name)
    by_driver = {drv: _order_element_sources(names) for drv, names in by_driver.items()}

    if driver is None:
        elements_view: Any = by_driver
    else:
        key = driver.strip().lower()
        elements_view = {"driver": key, "sources": by_driver.get(key, [])}

    return {
        "elements_sources": elements_view,
        "text_detection": _iter_module_names(TextFactory.DEFAULT_PACKAGE),
        "image_detection": _iter_module_names(ImageFactory.DEFAULT_PACKAGE),
    }


# --------------------------------------------------------------------------- #
# doctor (delegates to helper/doctor.py — the same checks as `optics doctor`)
# --------------------------------------------------------------------------- #
def _check_to_dict(check: Any) -> dict[str, Any]:
    """Render a ``doctor.Check`` namedtuple as a JSON row; ``fix`` on warn/fail."""
    ok = check.status == "ok"
    row: dict[str, Any] = {"name": check.name, "status": check.status, "ok": ok, "detail": check.detail}
    if check.hint and not ok:
        row["fix"] = check.hint
    return row


def run_doctor(
    project_path: Optional[str] = None,
    appium_host: str = "127.0.0.1",
    appium_port: int = 4723,
) -> dict[str, Any]:
    """One-call environment diagnosis, reusing ``optics doctor``'s checks.

    Runs the core / engine / mobile (adb + Appium reachability) / web checks and,
    when ``project_path`` is given, the strict project-config validation. ``ok`` is
    the AND of every check that is not a ``fail`` (``warn`` rows — e.g. adb absent on
    a web-only box — do not fail the report). Also reports whether the ``[mcp]``
    extra (fastmcp) is importable, since that is what runs this server.
    """
    from optics_framework.helper import doctor  # lazy: pulls rich/setup only when asked

    rows = (
        doctor.check_core()
        + doctor.check_engines()
        + doctor.check_mobile(appium_host, appium_port)
        + doctor.check_web()
    )
    if project_path:
        rows += doctor.validate_project(project_path)

    checks = [_check_to_dict(row) for row in rows]
    try:
        importlib.import_module("fastmcp")
        checks.append({"name": "mcp_extra", "status": "ok", "ok": True, "detail": "fastmcp installed"})
    except ImportError:
        checks.append(
            {
                "name": "mcp_extra",
                "status": "fail",
                "ok": False,
                "detail": "fastmcp not importable",
                "fix": "Install the MCP extra: pip install 'optics-framework[mcp]'",
            }
        )

    ok = all(row["status"] != "fail" for row in checks)
    return {"ok": ok, "version": VERSION, "checks": checks}


# --------------------------------------------------------------------------- #
# Android app introspection (best-effort adb wrappers)
# --------------------------------------------------------------------------- #
def _adb_argv(serial: Optional[str], *args: str) -> list[str]:
    """``adb [-s serial] <args...>`` — the serial targets a specific device."""
    prefix = [_ADB]
    if serial:
        prefix += ["-s", serial]
    return prefix + list(args)


def _parse_pm_list_packages(output: str) -> list[str]:
    """Package ids from ``pm list packages`` output (each line ``package:<id>``)."""
    packages: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            pkg = line[len("package:"):].strip()
            if pkg:
                packages.append(pkg)
    return sorted(set(packages))


# ``mResumedActivity``/``ResumedActivity``/``mCurrentFocus`` lines carry a
# ``package/activity`` token; the package/activity chars are the Android-legal set.
_FOCUS_RE = re.compile(r"([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)")


def _parse_foreground_app(output: str) -> dict[str, Optional[str]]:
    """Foreground ``{"package","activity"}`` from ``dumpsys activity activities``.

    Scans for the first resumed/focused-activity line and pulls the
    ``package/activity`` token; returns ``None`` values when nothing matches.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if any(
            marker in stripped
            for marker in ("mResumedActivity", "ResumedActivity", "mCurrentFocus", "topResumedActivity")
        ):
            match = _FOCUS_RE.search(stripped)
            if match:
                return {"package": match.group(1), "activity": match.group(2)}
    return {"package": None, "activity": None}


def get_foreground_app(serial: Optional[str] = None) -> dict[str, Optional[str]]:
    """Foreground app package/activity via ``adb ... dumpsys activity activities``.

    Android-only and best-effort. Raises ``RuntimeError`` when adb is unavailable so
    the tool layer can surface a clear message rather than a silent empty result.
    """
    output = _run_tool(_adb_argv(serial, "shell", "dumpsys", "activity", "activities"), timeout=15)
    if output is None:
        raise RuntimeError(
            "adb not available or the device is unreachable; install Android "
            "platform-tools and connect a device (this is Android-only)."
        )
    return _parse_foreground_app(output)


def list_installed_packages(serial: Optional[str] = None, query: Optional[str] = None) -> list[str]:
    """Installed package ids via ``adb ... pm list packages`` (optionally filtered).

    ``query`` is a case-insensitive substring match. Android-only, best-effort;
    raises ``RuntimeError`` when adb is unavailable.
    """
    output = _run_tool(_adb_argv(serial, "shell", "pm", "list", "packages"), timeout=30)
    if output is None:
        raise RuntimeError(
            "adb not available or the device is unreachable; install Android "
            "platform-tools and connect a device (this is Android-only)."
        )
    packages = _parse_pm_list_packages(output)
    if query:
        needle = query.strip().lower()
        packages = [pkg for pkg in packages if needle in pkg.lower()]
    return packages


def is_truthy(value: Any) -> bool:
    """Interpret an MCP string flag as a boolean (``"true"``/``"1"``/``"yes"``/``"on"``)."""
    return str(value).strip().lower() in _TRUE_STRINGS
