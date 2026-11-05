"""The local Chrome and Edge profiles a Playwright session can start signed in with."""
import json
import os
import pathlib
import shutil
import sys
from dataclasses import asdict, dataclass

from optics_framework.common.error import Code, OpticsError

# Chromium refuses automation of its default user data directory, so a session runs on a copy of these.
_SIGN_IN_STATE = ("Cookies", "Cookies-journal", "Network", "Local Storage", "Preferences")
_LOCAL_STATE = "Local State"
_USER_DATA = {
    "darwin": ("Library/Application Support", {
        "chrome": "Google/Chrome", "chrome-beta": "Google/Chrome Beta", "msedge": "Microsoft Edge",
    }),
    "linux": (".config", {"chrome": "google-chrome", "chrome-beta": "google-chrome-beta", "msedge": "microsoft-edge"}),
    "win32": ("AppData/Local", {
        "chrome": "Google/Chrome/User Data", "chrome-beta": "Google/Chrome Beta/User Data",
        "msedge": "Microsoft/Edge/User Data",
    }),
}


@dataclass(frozen=True)
class BrowserProfile:
    channel: str
    directory: str
    name: str
    email: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def user_data_dir(channel: str) -> pathlib.Path | None:
    """Where ``channel`` keeps its profiles on this machine, or None when this OS has no known place."""
    platform = "linux" if sys.platform.startswith("linux") else sys.platform
    base, folders = _USER_DATA.get(platform, ("", {}))
    if channel not in folders:
        return None
    home = os.environ.get("LOCALAPPDATA") if platform == "win32" else None
    root = pathlib.Path(home) if home else pathlib.Path.home() / base
    return root / folders[channel]


def list_profiles(channels: tuple[str, ...] = ("chrome", "msedge")) -> list[BrowserProfile]:
    """The profiles each installed ``channel`` lists in its Local State, with the account signed in to each."""
    found = []
    for channel in channels:
        root = user_data_dir(channel)
        try:
            cache = json.loads((root / _LOCAL_STATE).read_text(encoding="utf-8"))["profile"]["info_cache"]
        except (TypeError, OSError, ValueError, KeyError):
            continue
        for directory, info in sorted(cache.items()):
            found.append(BrowserProfile(channel, directory, info.get("name", ""), info.get("user_name", "")))
    return found


def find_profile(choice: str, channel: str = "") -> BrowserProfile:
    """The one profile whose directory, else name or account, is ``choice``; E0501 lists the candidates otherwise."""
    wanted = choice.strip().casefold()
    profiles = list_profiles((channel,)) if channel else list_profiles()
    matches = _matching(profiles, wanted)
    if len(matches) == 1:
        return matches[0]
    listed = "; ".join(f"{p.channel} {p.directory!r} ({p.name}, {p.email or 'not signed in'})" for p in matches or profiles)
    if matches:
        raise OpticsError(Code.E0501, f"Browser profile {choice!r} matches several: {listed}. Pass channel or the directory")
    raise OpticsError(Code.E0501, f"No browser profile {choice!r}. Profiles: {listed or 'none found'}")


def _matching(profiles: list[BrowserProfile], wanted: str) -> list[BrowserProfile]:
    """The profiles whose directory is ``wanted``, else those whose name or account is."""
    by_directory = [p for p in profiles if p.directory.casefold() == wanted]
    return by_directory or [p for p in profiles if wanted in (p.name.casefold(), p.email.casefold())]


def copy_sign_in_state(profile: BrowserProfile, target: pathlib.Path) -> None:
    """Copy the profile's cookies, local storage and preferences into the new user data directory ``target``."""
    source = user_data_dir(profile.channel)
    shutil.copy2(source / _LOCAL_STATE, target / _LOCAL_STATE)
    (target / profile.directory).mkdir(parents=True, exist_ok=True)
    for name in _SIGN_IN_STATE:
        item = source / profile.directory / name
        if item.is_dir():
            shutil.copytree(item, target / profile.directory / name, ignore=shutil.ignore_patterns("LOCK"))
        elif item.is_file():
            shutil.copy2(item, target / profile.directory / name)
