"""Tell people when a newer Optics release is out, and what the release they just installed added."""
import json
import os
import subprocess  # nosec B404
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path

import requests
from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

from optics_framework.common import safe_fs
from optics_framework.helper.environment import detect, plan_upgrade
from optics_framework.common.optics_home import optics_home
from optics_framework.helper.version import VERSION

PYPI_URL = "https://pypi.org/pypi/optics-framework/json"
RELEASES_URL = "https://optics-framework.org/release.json"
ENV_NO_UPDATE_CHECK = "OPTICS_NO_UPDATE_CHECK"
_DAY_S = 86400
_TIMEOUT_S = 3.0
_WAIT_S = 0.5
_SKIPPED_SHOWN = 3
_OFF_VALUES = frozenset({"", "0", "false", "no", "off"})
_refresh: threading.Thread | None = None


def turned_off(variable: str) -> bool:
    """True when ``variable`` or DO_NOT_TRACK is set to anything but an off value."""
    return any(os.environ.get(name, "").strip().lower() not in _OFF_VALUES for name in (variable, "DO_NOT_TRACK"))


def user_agent(surface: str) -> str:
    return f"optics/{VERSION} ({surface}; {sys.platform})"


def enabled() -> bool:
    return not turned_off(ENV_NO_UPDATE_CHECK) and not os.environ.get("CI")


@dataclass
class State:
    checked: float = 0.0
    latest: str = ""
    releases: dict[str, dict[str, str]] = field(default_factory=dict)
    seen: str = ""


def _path() -> Path:
    return Path(optics_home()) / "update.json"


def _load() -> State:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return State(float(data["checked"]), str(data["latest"]), dict(data["releases"]), str(data.get("seen", "")))
    except (OSError, ValueError, KeyError, TypeError):
        return State()


def _store(state: State) -> None:
    try:
        safe_fs.write_text(_path(), json.dumps(asdict(state)))
    except OSError:
        pass


def _version(text: str) -> Version | None:
    try:
        return Version(text)
    except InvalidVersion:
        return None


def _headlines(surface: str, cached: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Each release's headline from the site; ``cached`` when the site is down or answers something else."""
    try:
        releases = requests.get(RELEASES_URL, headers={"User-Agent": user_agent(surface)}, timeout=_TIMEOUT_S).json()["releases"]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return cached
    if not isinstance(releases, dict):
        return cached
    return {v: r for v, r in releases.items() if isinstance(r, dict) and all(isinstance(t, str) for t in r.values())}


def refresh(surface: str) -> State:
    """The cached state, read again from PyPI when it is a day old."""
    state = _load()
    if time.time() - state.checked < _DAY_S:
        return state
    return lookup(surface) or state


def lookup(surface: str) -> State | None:
    """Read the newest release from PyPI now and cache it; None when PyPI does not answer."""
    state = _load()
    try:
        latest = requests.get(PYPI_URL, headers={"User-Agent": user_agent(surface)}, timeout=_TIMEOUT_S).json()["info"]["version"]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None
    _store(State(state.checked, str(latest), state.releases, state.seen))
    releases = state.releases if turned_off("DO_NOT_TRACK") else _headlines(surface, state.releases)
    fresh = State(time.time(), str(latest), releases, state.seen)
    _store(fresh)
    return fresh


def refresh_in_background(surface: str, then: Callable[[str], object] | None = None) -> None:
    """Start a daily check that never delays the command, passing each notice line to ``then`` when it ends."""
    global _refresh

    def check() -> None:
        refresh(surface)
        for line in notice_lines(wait=False) if then else ():
            then(line)

    if enabled() and _refresh is None:
        _refresh = threading.Thread(target=check, daemon=True, name="optics-update-check")
        _refresh.start()


def _headline(state: State, version: str) -> str:
    release = state.releases.get(version, {})
    text = " ".join(part for part in (release.get("headline", ""), release.get("url", "")) if part)
    return text[:300]


def newer(state: State) -> list[str]:
    """Releases after this one, newest first; empty when this is the newest."""
    current = _version(VERSION)
    latest = _version(state.latest)
    if current is None or latest is None or latest <= current:
        return []
    between = sorted((v for v in map(_version, state.releases) if v and current < v < latest), reverse=True)
    return [state.latest, *(str(v) for v in between)]


def update_notice(state: State) -> list[str]:
    """One line saying a newer release is out, and the headline of each skipped release."""
    versions = newer(state)
    if not versions:
        return []
    lines = [f"Optics {versions[0]} is out (you have {VERSION}). Run `optics upgrade`."]
    lines += [f"  {v}: {text}" for v in versions[:_SKIPPED_SHOWN] if (text := _headline(state, v))]
    return lines


def notice_lines(wait: bool = True) -> list[str]:
    """What to tell the user now: a newer release, or once after an upgrade, what this release added."""
    if not enabled():
        return []
    if wait and _refresh is not None:
        _refresh.join(_WAIT_S)
    state = _load()
    lines = update_notice(state)
    if lines or state.seen == VERSION or not state.checked:
        return lines
    state.seen = VERSION
    _store(state)
    text = _headline(state, VERSION)
    return [f"New in Optics {VERSION}: {text}"] if text else []


def installed_spec() -> str:
    """``optics-framework[...]`` with every extra whose packages are all installed, so their pins move too."""
    try:
        dist = metadata.distribution("optics-framework")
    except metadata.PackageNotFoundError:
        return "optics-framework"
    requirements = [Requirement(line) for line in dist.requires or ()]
    extras = [extra for extra in dist.metadata.get_all("Provides-Extra") or () if _extra_installed(extra, requirements)]
    return f"optics-framework[{','.join(extras)}]" if extras else "optics-framework"


def _extra_installed(extra: str, requirements: list[Requirement]) -> bool:
    needed = [r.name for r in requirements if r.marker and r.marker.evaluate({"extra": extra})]
    return bool(needed) and all(_installed(name) for name in needed)


def _installed(name: str) -> bool:
    try:
        metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return False
    return True


def upgrade() -> int:
    """``optics upgrade``: look up the newest release, even where the passive notice is off, and move to it."""
    state = lookup("cli")
    if state is None:
        print("Could not look up the newest release on PyPI.")
    elif not newer(state):
        print(f"Optics {VERSION} is the newest release.")
        return 0
    latest = state.latest if state else ""
    plan = plan_upgrade(detect(), installed_spec(), latest)
    if plan.command is None:
        print(plan.describe())
        return 1
    print(f"Upgrading Optics {VERSION} to {latest}." if latest else "Upgrading to the newest release the installer finds.")
    if plan.note:
        print(plan.note)
    return subprocess.run(plan.command, check=False).returncode  # nosec B603
