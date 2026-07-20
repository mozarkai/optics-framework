"""
Platform profiles for the Appium driver.

The Appium driver (``engines/drivers/appium.py``) speaks to several device families
through one Appium server: Android phones, iOS phones, and TV app runtimes
(Samsung Tizen, LG webOS). They differ in only a few, well-contained ways:

* which Appium *options* object builds the session
  (``UiAutomator2Options`` / ``XCUITestOptions`` / generic ``AppiumOptions``),
* which capability carries the app identifier passed to ``launch_app``
  (``appPackage`` vs ``bundleId`` vs ``appId``), and
* how a "keycode" is delivered — an Android integer key event
  (``driver.press_keycode(66)``) versus a TV's *named* remote button sent over the
  vendor command (``driver.execute_script("tizen: pressKey", {"key": "KEY_ENTER"})``).

Those differences live here as **data** (:class:`PlatformProfile`), so the driver
stays free of ``if android / elif ios / elif tizen`` ladders and adding a new device
family is "register a profile", not "edit the driver".

*Which keywords* a family supports is declared on the driver methods themselves with
the :func:`supported_on` decorator. Calling a method on a platform that does not
support it raises a clear :class:`OpticsError` (``E0105``) up front, instead of a
confusing backend failure deep inside Appium.

To add a new remote-control TV (e.g. Roku, Fire TV):
  1. Add a :class:`PlatformProfile` via :func:`register_profile` below, keyed by the
     normalized ``platformName`` the Appium server expects.
  2. Fill in its options factory, app-id caps, keycode strategy and (for TVs) the
     remote command + key map.
  3. Nothing else — session creation, element sources, self-healing, events, live
     mode and the MCP server all work unchanged.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any, Callable

from appium.options.android.uiautomator2.base import UiAutomator2Options
from appium.options.ios import XCUITestOptions  # type: ignore
from appium.options.common.base import AppiumOptions

from optics_framework.common.error import OpticsError, Code

# --- canonical platform keys (normalized `platformName`) --------------------
ANDROID = "android"
IOS = "ios"
TIZEN = "tizentv"   # Samsung Tizen TV  (platformName: TizenTV)
WEBOS = "lgtv"      # LG webOS TV       (platformName: LGTV)

#: Phone/tablet platforms — the historical Appium surface. Used by ``@supported_on``
#: to fence off touch/keyboard actions that a TV D-pad cannot perform.
MOBILE = (ANDROID, IOS)

# --- keycode delivery strategies --------------------------------------------
KEYCODE_ANDROID_INT = "android_int"   # driver.press_keycode(int(code))
KEYCODE_RC_NAMED = "rc_named"         # driver.execute_script("<vendor>: pressKey", {...})

# Defaults shared by the mobile (Android/iOS) session; TV profiles keep their own
# minimal set because these mobile-oriented caps are rejected by the TV drivers.
_MOBILE_DEFAULT_OPTIONS: dict[str, Any] = {
    "newCommandTimeout": 3600,
    "ensureWebviewsHavePages": True,
    "nativeWebScreenshot": True,
    "noReset": True,
    "shouldTerminateApp": True,
    "forceAppLaunch": True,
    "connectHardwareKeyboard": True,
}


@dataclass(frozen=True)
class PlatformProfile:
    """Everything that differs between device families, as data."""

    name: str                                   # canonical key == normalized platformName
    label: str                                  # human-readable name for messages
    options_factory: Callable[[], Any]          # builds the Appium options object
    app_id_caps: tuple[str, ...]                # cap keys that receive launch_app's app id
    keycode_strategy: str                       # KEYCODE_ANDROID_INT | KEYCODE_RC_NAMED
    default_options: dict[str, Any] = field(default_factory=dict)
    # Remote-control specifics — only meaningful when keycode_strategy == KEYCODE_RC_NAMED.
    rc_command: str | None = None               # e.g. "tizen: pressKey" / "webos: pressKey"
    rc_key_map: dict[str, str] = field(default_factory=dict)   # canonical name -> vendor key
    rc_extra_payload: dict[str, Any] = field(default_factory=dict)  # merged into {"key": ...}
    # When True, an unmapped key name is passed through as-is (webOS accepts bare
    # names like "ENTER"); when False, only mapped keys or explicit "KEY_*" pass.
    rc_passthrough_unknown: bool = False

    def resolve_rc_key(self, keycode: str) -> str:
        """Map a canonical key name (UP/ENTER/BACK/...) to this platform's vendor key."""
        key = str(keycode).strip().upper()
        mapped = self.rc_key_map.get(key)
        if mapped is not None:
            return mapped
        if key.startswith("KEY_") or self.rc_passthrough_unknown:
            return key
        raise OpticsError(
            Code.E0104,
            message=f"Unknown remote key '{keycode}' for {self.label}. "
                    f"Known keys: {', '.join(sorted(self.rc_key_map))}.",
        )


PROFILES: dict[str, PlatformProfile] = {}


def register_profile(profile: PlatformProfile) -> PlatformProfile:
    PROFILES[profile.name] = profile
    return profile


def normalize_platform(platform_name: Any) -> str:
    return str(platform_name or "").strip().lower()


def get_profile(platform_name: Any) -> PlatformProfile | None:
    return PROFILES.get(normalize_platform(platform_name))


def supported_platforms() -> str:
    """Comma-separated list of supported platforms, for error messages."""
    return ", ".join(p.label for p in PROFILES.values())


# --- the capability decorator -----------------------------------------------
def supported_on(*platforms: str) -> Callable:
    """Declare which platforms a driver method supports.

    On an unsupported platform the call raises ``OpticsError(E0105)`` with a clear
    message rather than failing obscurely in the backend. Methods left undecorated
    are assumed universal. The allowed set is also stored on the wrapper as
    ``_supported_platforms`` so tooling/dry-run can introspect capabilities.
    """
    allowed = frozenset(normalize_platform(p) for p in platforms)

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            platform = self._active_platform()  # provided by the Appium driver
            if platform and platform not in allowed:
                label = PROFILES[platform].label if platform in PROFILES else platform
                raise OpticsError(
                    Code.E0105,
                    message=f"'{fn.__name__}' is not supported on {label}. "
                            f"Supported here: {', '.join(sorted(allowed))}.",
                )
            return fn(self, *args, **kwargs)

        wrapper._supported_platforms = allowed  # type: ignore[attr-defined]
        return wrapper

    return decorator


# --- the profiles ------------------------------------------------------------
register_profile(PlatformProfile(
    name=ANDROID,
    label="Android",
    options_factory=UiAutomator2Options,
    app_id_caps=("appPackage", "appium:appPackage"),
    keycode_strategy=KEYCODE_ANDROID_INT,
    default_options={**_MOBILE_DEFAULT_OPTIONS, "ignoreHiddenApiPolicyError": True},
))

register_profile(PlatformProfile(
    name=IOS,
    label="iOS",
    options_factory=XCUITestOptions,
    app_id_caps=("bundleId", "appium:bundleId"),
    keycode_strategy=KEYCODE_ANDROID_INT,
    default_options=dict(_MOBILE_DEFAULT_OPTIONS),
))

# D-pad / OK -> Samsung remote key names (values from @headspinio/tizen-remote).
_TIZEN_KEYS = {
    "UP": "KEY_UP", "DOWN": "KEY_DOWN", "LEFT": "KEY_LEFT", "RIGHT": "KEY_RIGHT",
    "ENTER": "KEY_ENTER", "SELECT": "KEY_ENTER", "OK": "KEY_ENTER",
    "BACK": "KEY_RETURN", "HOME": "KEY_HOME",
    "PLAY": "KEY_PLAY", "PAUSE": "KEY_PAUSE", "STOP": "KEY_STOP",
    "REWIND": "KEY_REWIND", "FF": "KEY_FF", "FAST_FORWARD": "KEY_FF",
}
register_profile(PlatformProfile(
    name=TIZEN,
    label="Samsung Tizen TV",
    options_factory=AppiumOptions,
    app_id_caps=("appPackage", "appium:appPackage"),
    keycode_strategy=KEYCODE_RC_NAMED,
    default_options={"newCommandTimeout": 3600, "rcMode": "remote", "noReset": True},
    rc_command="tizen: pressKey",
    rc_key_map=_TIZEN_KEYS,
    rc_passthrough_unknown=False,
))

# webOS accepts bare names ("ENTER", "UP") in rc mode; map is identity for nav/media.
_WEBOS_KEYS = {
    "UP": "UP", "DOWN": "DOWN", "LEFT": "LEFT", "RIGHT": "RIGHT",
    "ENTER": "ENTER", "SELECT": "ENTER", "OK": "ENTER",
    "BACK": "BACK", "HOME": "HOME",
    "PLAY": "PLAY", "PAUSE": "PAUSE", "STOP": "STOP",
    "REWIND": "REWIND", "FF": "FF", "FAST_FORWARD": "FF",
}
register_profile(PlatformProfile(
    name=WEBOS,
    label="LG webOS TV",
    options_factory=AppiumOptions,
    app_id_caps=("appId", "appium:appId"),
    keycode_strategy=KEYCODE_RC_NAMED,
    default_options={
        "newCommandTimeout": 600, "rcMode": "rc",
        "useSecureWebsocket": True, "autoExtendDevMode": False, "noReset": True,
    },
    rc_command="webos: pressKey",
    rc_key_map=_WEBOS_KEYS,
    rc_extra_payload={"duration": 200},
    rc_passthrough_unknown=True,
))
