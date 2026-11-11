"""Platform profiles for the Appium driver.

Each device family (Android, iOS, TV) differs in options class, app-id capability
names, and keycode delivery. This module holds those differences as data
(PlatformProfile) so the driver stays free of platform branching logic.

Adding a new TV: register a PlatformProfile, update config defaults, ship a sample.
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

MAC = "mac"
WINDOWS = "windows"
LINUX = "linux"

#: Phone/tablet platforms — the historical Appium surface. Used by ``@supported_on``
#: to fence off touch actions that a TV D-pad cannot perform.
MOBILE = (ANDROID, IOS)
#: Desktop platforms, served by the Xa11y Appium driver.
DESKTOP = (MAC, WINDOWS, LINUX)
#: Platforms with a pointer and a native element tree.
POINTER = MOBILE + DESKTOP

# --- keycode delivery strategies --------------------------------------------
KEYCODE_ANDROID_INT = "android_int"   # driver.press_keycode(int(code))
KEYCODE_RC_NAMED = "rc_named"         # driver.execute_script("<vendor>: pressKey", {...})
KEYCODE_CHORD = "chord"               # driver.execute_script("xa11y: chord", {"keys": "cmd+s"})

# Defaults shared by the mobile (Android/iOS) session; TV profiles keep their own
# minimal set because these mobile-oriented caps are rejected by the TV drivers.
# Both drivers wait up to 10 s for an idle app before each command; a screen that never settles pays it on every call.
_MOBILE_DEFAULT_OPTIONS: dict[str, Any] = {
    "newCommandTimeout": 3600,
    "ensureWebviewsHavePages": True,
    "nativeWebScreenshot": True,
    "noReset": True,
    "shouldTerminateApp": True,
    "forceAppLaunch": True,
    "connectHardwareKeyboard": True,
}

# A context switch asks every app's DevTools socket for its pages, and a frozen app's never answers. Version
# details stay on (one parallel 2 s wait at most): without them chromedriver is matched to the WebView, not Chrome.
_ANDROID_CONTEXT_OPTIONS: dict[str, Any] = {
    "ensureWebviewsHavePages": False,
    "extractChromeAndroidPackageFromContextName": True,
}

# Web Inspector lists every app's pages ("*" covers WebViews and any browser) and fails fast: without these,
# an empty listing retries for 10 s and a frozen page blocks each script for 2 min. A single try is too few:
# system daemons connecting right after the inspector attaches void it, so an open WebView is missed.
_IOS_CONTEXT_OPTIONS: dict[str, Any] = {
    "includeSafariInWebviews": True,
    "additionalWebviewBundleIds": ["*"],
    "webviewConnectRetries": 3,
    "webviewAtomWaitTimeout": 5000,
}


@dataclass(frozen=True)
class PlatformProfile:
    """Device family configuration: options class, app-id caps, keycode delivery."""

    name: str
    label: str
    options_factory: Callable[[], Any]
    app_id_caps: tuple[str, ...]
    keycode_strategy: str
    default_options: dict[str, Any] = field(default_factory=dict)
    native_session_options: dict[str, Any] = field(default_factory=dict)  # left out when a session starts in a browser
    rc_command: str | None = None  # only meaningful when keycode_strategy == KEYCODE_RC_NAMED
    rc_key_map: dict[str, str] = field(default_factory=dict)
    rc_extra_payload: dict[str, Any] = field(default_factory=dict)
    web_ui: bool = False  # the session's own context is the app's DOM, with no native tree
    native_screenshot: bool = True
    select_all_chord: str | None = None  # desktop: clear_text selects all with this chord, then deletes
    app_at_start: bool = False  # the driver attaches when the session starts, so the app (or appName) must be in the capabilities then

    def resolve_rc_key(self, keycode: str) -> str:
        key = str(keycode).strip().upper()
        mapped = self.rc_key_map.get(key)
        if mapped is not None:
            return mapped
        if key.startswith("KEY_"):
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


def promote_appium_options(caps: dict[str, Any]) -> dict[str, Any]:
    """The capabilities as the Appium server reads them: each key in appium:options moves up with the appium: prefix."""
    options = caps.get("appium:options")
    if not isinstance(options, dict):
        return caps
    rest = {k: v for k, v in caps.items() if k != "appium:options"}
    return rest | {k if k.startswith("appium:") else f"appium:{k}": v for k, v in options.items()}


def supported_platforms() -> str:
    return ", ".join(p.label for p in PROFILES.values())


SESSION_ID_CAPS = ("existingSessionId", "sessionId", "appium:existingSessionId", "appium:sessionId")


def session_to_attach(caps: dict[str, Any]) -> str | None:
    """The session id the capabilities name: the driver attaches to it before it checks for an app."""
    return next((str(caps[k]) for k in SESSION_ID_CAPS if caps.get(k) and isinstance(caps[k], (str, int, float))), None)


def session_app(caps: dict[str, Any]) -> str | None:
    """The app the capabilities give the session: its id, or on a desktop the name of the running app it attaches to."""
    profile = get_profile(caps.get("platformName") or caps.get("appium:platformName"))
    if profile is None:
        return None
    keys = (*profile.app_id_caps, *(("appName", "appium:appName") if profile.app_at_start else ()))
    return next((str(caps[k]) for k in keys if caps.get(k)), None)


def require_app_at_start(caps: dict[str, Any]) -> None:
    """Raise E0104 when the platform attaches at session start and the capabilities name no app to launch or attach to."""
    profile = get_profile(caps.get("platformName") or caps.get("appium:platformName"))
    if profile is None or not profile.app_at_start:
        return
    if not session_app(caps):
        raise OpticsError(
            Code.E0104,
            message=f"A {profile.label} session needs its app when it starts: set 'app' to launch it, or 'appName' to "
            "attach to a running one. With neither, the driver attaches to the frontmost app, which can be the window "
            "that runs the agent.",
        )


# --- the capability decorator -----------------------------------------------
def supported_on(*platforms: str) -> Callable:
    """Guard: raises E0105 if called on an unsupported platform."""
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
    default_options={
        **_MOBILE_DEFAULT_OPTIONS,
        **_ANDROID_CONTEXT_OPTIONS,
        "ignoreHiddenApiPolicyError": True,
        "settings": {"waitForIdleTimeout": 1000},  # milliseconds
    },
))

register_profile(PlatformProfile(
    name=IOS,
    label="iOS",
    options_factory=XCUITestOptions,
    app_id_caps=("bundleId", "appium:bundleId"),
    keycode_strategy=KEYCODE_ANDROID_INT,
    default_options={**_MOBILE_DEFAULT_OPTIONS, "settings": {"waitForIdleTimeout": 1}},  # seconds
    native_session_options=_IOS_CONTEXT_OPTIONS,
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
    web_ui=True,
    native_screenshot=False,  # hangs on the TV's hardware compositor and stalls every later command
))

# The webOS driver's remote buttons and media keys; it sends any other name to the TV, which ignores it.
_WEBOS_KEYS = {
    "UP": "UP", "DOWN": "DOWN", "LEFT": "LEFT", "RIGHT": "RIGHT",
    "ENTER": "ENTER", "SELECT": "ENTER", "OK": "ENTER",
    "BACK": "BACK", "HOME": "HOME",
    "PLAY": "PLAY", "PAUSE": "PAUSE", "STOP": "STOP",
    "REWIND": "REWIND", "FF": "FF", "FAST_FORWARD": "FF",
    "VOL_UP": "VOL_UP", "VOLUME_UP": "VOL_UP", "VOL_DOWN": "VOL_DOWN", "VOLUME_DOWN": "VOL_DOWN",
    "MUTE": "MUTE", "UNMUTE": "UNMUTE", "CHAN_UP": "CHAN_UP", "CHAN_DOWN": "CHAN_DOWN",
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
    web_ui=True,
))

# Desktop apps through appium-xa11y-driver: keys are chords, text goes to the focused control.
for _name, _label, _select_all in ((MAC, "Mac", "cmd+a"), (WINDOWS, "Windows", "ctrl+a"), (LINUX, "Linux", "ctrl+a")):
    register_profile(PlatformProfile(
        name=_name,
        label=_label,
        options_factory=AppiumOptions,
        app_id_caps=("app", "appium:app"),
        keycode_strategy=KEYCODE_CHORD,
        default_options={"automationName": "Xa11y", "newCommandTimeout": 3600},
        select_all_chord=_select_all,
        app_at_start=True,
    ))
