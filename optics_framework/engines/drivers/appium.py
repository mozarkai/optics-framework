import json
import subprocess  # nosec
import time
import uuid
from contextlib import contextmanager
from collections.abc import Iterator, Sequence
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlsplit, urlunsplit
from appium import webdriver
from appium.common.exceptions import NoSuchContextException
from appium.webdriver.appium_connection import AppiumConnection
from appium.webdriver.webdriver import WebDriver
from appium.webdriver.client_config import AppiumClientConfig
from selenium.common import WebDriverException
from selenium.common.exceptions import (
    InvalidSelectorException,
    InvalidSessionIdException,
    NoSuchWindowException,
    TimeoutException,
)
from selenium.webdriver.remote.command import Command  # type: ignore
from appium.options.android.uiautomator2.base import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.action_chains import ActionChains  # type: ignore
from selenium.webdriver.common.actions.action_builder import ActionBuilder  # type: ignore
from selenium.webdriver.common.actions.pointer_input import PointerInput  # type: ignore
from selenium.webdriver.common.actions import interaction  # type: ignore
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from optics_framework.common.utils import SpecialKey
from optics_framework.common.eventSDK import EventSDK
from optics_framework.engines.drivers.appium_UI_helper import UIHelper
from optics_framework.engines.drivers.appium_platforms import (
    PlatformProfile,
    get_profile,
    normalize_platform,
    supported_platforms,
    supported_on,
    ANDROID,
    MOBILE,
    KEYCODE_RC_NAMED,
)
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.session_liveness import raise_if_session_dead

_MAX_ERROR_OUTPUT_LINES = 20

_CDP_EXECUTE = "opticsExecuteCdp"
_WEB_RECT_JS = "const r = arguments[0].getBoundingClientRect(); return [r.left, r.top, r.width, r.height];"
# An invisible, untouchable box over the visual viewport: its native rect maps the page's CSS pixels to the screen.
# It goes inside an open modal, because WebKit hides everything outside one from accessibility.
_PROBE_JS = (
    "const v = window.visualViewport, p = document.createElement('div');"
    "p.id = arguments[0]; p.setAttribute('role', 'img'); p.setAttribute('aria-label', arguments[0]);"
    "p.style.cssText = `position:fixed;left:${v.offsetLeft}px;top:${v.offsetTop}px;width:${v.width}px;"
    "height:${v.height}px;opacity:0.01;pointer-events:none;z-index:2147483647`;"
    "const modal = [...document.querySelectorAll('dialog:modal,[aria-modal=\"true\"]')]"
    ".filter(e => e.getClientRects().length).pop();"
    "(modal || document.documentElement).appendChild(p); const r = p.getBoundingClientRect();"
    "return [r.left, r.top, r.width, r.height];"
)
_PROBE_REMOVE_JS = "document.getElementById(arguments[0])?.remove();"
_GET_CONTEXTS = "mobile: getContexts"
_SAFARI_VIEW_SERVICE = "com.apple.SafariViewService"
_ONE_PAGE_ON_SCREEN = frozenset({_SAFARI_VIEW_SERVICE, "com.apple.mobilesafari"})
# A zero-size page reports visible too: Firefox runs its translations engine in one.
_PAGE_SHOWN_JS = "return document.visibilityState === 'visible' && innerWidth > 0 && innerHeight > 0;"
_KEYBOARD_TIMEOUT_S = 2.0
_KEYBOARD_POLL_S = 0.1
_PROBE_TIMEOUT_S = 2.0


def _starts_in_web(caps: dict[str, Any]) -> bool:
    return bool(caps.get("browserName") or caps.get("autoWebview") or caps.get("appium:autoWebview"))


def _page_holds(driver: WebDriver, queries: Sequence[tuple[str, str]]) -> bool:
    try:
        return not queries or any(driver.find_elements(*query) for query in queries)
    except InvalidSelectorException:
        return True  # the lookup that follows reports the bad selector


def _ios_page(context: dict[str, Any]) -> tuple[str, str, str]:
    return context["id"], str(context.get("url", "")), str(context.get("title", ""))


def _ios_context_pid(context: str) -> int | None:
    """Process id in an iOS web context name, ``WEBVIEW_<pid>.<page>``."""
    try:
        return int(context.removeprefix("WEBVIEW_").split(".")[0])
    except ValueError:
        return None


def _is_app_webview_page(page: dict[str, Any]) -> bool:
    """An Android WebView describes its page in JSON (attached, visible, bounds); a browser tab does not."""
    try:
        description = json.loads(page.get("description") or "")
    except ValueError:
        return False
    return isinstance(description, dict) and "attached" in description


def _context_name(webview: dict[str, Any]) -> str | None:
    """Switchable context name of a ``mobile: getContexts`` entry; ``webview`` can be a pid (``WEBVIEW_28005``)."""
    return webview.get("webviewName") or webview.get("webview")


def _summarize_command_output(output: str, max_lines: int = _MAX_ERROR_OUTPUT_LINES) -> str:
    """Cap command output embedded in error messages so one failure can't flood the log."""
    lines = [line for line in (output or "").splitlines() if line.strip()]
    if not lines:
        return "<no output>"
    if len(lines) <= max_lines:
        return "\n".join(lines)
    half = max_lines // 2
    omitted = len(lines) - max_lines
    return "\n".join([*lines[:half], f"… ({omitted} lines omitted)", *lines[-half:]])


class _QueryForwardingConnection(AppiumConnection):
    """Re-appends the server URL's query string after each command path."""

    def __init__(self, query: str, client_config: AppiumClientConfig) -> None:
        self._forwarded_query = query
        super().__init__(client_config=client_config)

    def _request(self, method, url, body=None):  # type: ignore[override]
        parts = urlsplit(url)
        query = "&".join(q for q in (parts.query, self._forwarded_query) if q)
        return super()._request(method, urlunsplit(parts._replace(query=query)), body=body)


def _command_executor_and_config(
    server_url: str, timeout: int
) -> tuple[str | AppiumConnection, AppiumClientConfig | None]:
    """The ``command_executor`` and ``client_config`` to hand ``webdriver.Remote``.

    A connection built here holds its own client config, so none is returned with it.
    """
    if "?" not in server_url and "#" not in server_url:
        return server_url, AppiumClientConfig(remote_server_addr=server_url, timeout=timeout)
    parts = urlsplit(server_url)
    base_url = urlunsplit(parts._replace(query="", fragment=""))
    connection = _QueryForwardingConnection(
        parts.query, AppiumClientConfig(remote_server_addr=base_url, timeout=timeout)
    )
    return connection, None


class Appium(DriverInterface):
    DEPENDENCY_TYPE = "driver_sources"
    NAME = "appium"
    NOT_INITIALIZED = "Appium driver is not initialized. Please start the session first."
    MOBILE_TYPE_COMMAND = "mobile: type"
    IOS_SPECIAL_KEYS = {
        SpecialKey.ENTER: "\n",
        SpecialKey.TAB: "\t",
        SpecialKey.BACKSPACE: "\b",
        SpecialKey.SPACE: " ",
    }
    MOBILE_PREFIX = "mobile:"
    NATIVE_CONTEXT = "NATIVE_APP"
    BROWSER_SESSION_CONTEXT = "CHROMIUM"
    ANDROID_BROWSER_PACKAGES = {"chrome": "com.android.chrome"}
    MOBILE_CLEAR = "mobile: clear"
    CAP_APP_PACKAGE = "appium:appPackage"
    CAP_APP_PACKAGE_LEGACY = "appPackage"
    CAP_PLATFORM_NAME = "platformName"
    CAP_APPIUM_PLATFORM_NAME = "appium:platformName"
    CAP_BUNDLE_ID = "bundleId"
    CAP_APPIUM_BUNDLE_ID = "appium:bundleId"
    CAP_APP_ACTIVITY = "appActivity"
    CAP_APPIUM_APP_ACTIVITY = "appium:appActivity"
    CAP_SESSION_ID = "sessionId"
    CAP_APPIUM_SESSION_ID = "appium:sessionId"
    CAP_EXISTING_SESSION_ID = "existingSessionId"
    CAP_APPIUM_EXISTING_SESSION_ID = "appium:existingSessionId"
    PLATFORM_ANDROID = "android"
    PLATFORM_IOS = "ios"
    COMMAND_NEW_SESSION = "newSession"
    RESPONSE_VALUE = "value"
    RESPONSE_CAPABILITIES = "capabilities"
    CONFIG_URL = "url"
    CONFIG_CAPABILITIES = "capabilities"
    DEFAULT_APPIUM_URL = "http://127.0.0.1:4723"
    CONNECTION_TIMEOUT = 300
    VERSION_NAME_PREFIX = "versionName="
    KEY_PLATFORMNAME = "platformname"
    CAP_UDID = "udid"
    CAP_APPIUM_UDID = "appium:udid"
    CAP_SESSION_DEVICE_UDID = "deviceUDID"
    SESSION_ID_CAP_KEYS = (
        "existingSessionId",
        "sessionId",
        "appium:existingSessionId",
        "appium:sessionId",
    )
    KEYCODE_MAP = {
        # Basic keys
        SpecialKey.ENTER: 66,
        SpecialKey.TAB: 61,
        SpecialKey.BACKSPACE: 67,
        SpecialKey.SPACE: 62,
        SpecialKey.ESCAPE: 111,

        # Mobile/System specific keys
        SpecialKey.BACK: 4,
        SpecialKey.HOME: 3,
        SpecialKey.MENU: 82,
        SpecialKey.VOLUME_UP: 24,
        SpecialKey.VOLUME_DOWN: 25,
        SpecialKey.POWER: 26,
        SpecialKey.CAMERA: 27,
        SpecialKey.SEARCH: 84,
    }


    def __init__(self, config: Optional[Dict[str, Any]] = None, event_sdk: Optional[EventSDK] = None) -> None:
        self.driver: Optional[WebDriver] = None
        if event_sdk is None:
            internal_logger.debug("No EventSDK instance provided to Appium driver.")
            raise OpticsError(Code.E0101, message="Appium driver requires an EventSDK instance.")
        self.event_sdk: EventSDK = event_sdk
        if config is None:
            internal_logger.debug(
                f"No configuration provided for {self.DEPENDENCY_TYPE}: {self.NAME}"
            )
            raise OpticsError(Code.E0104, message="Appium driver not enabled in config")

        self.appium_server_url: str = str(config.get(self.CONFIG_URL, self.DEFAULT_APPIUM_URL))

        self.capabilities: Dict[str, Any] = config.get(self.CONFIG_CAPABILITIES, {})
        if not self.capabilities:
            internal_logger.debug("No capabilities found in config")
            raise OpticsError(Code.E0104, message="Appium capabilities not found in config")

        # UI Tree handling
        self.ui_helper: Optional[UIHelper] = None
        self.initialized: bool = True
        self._known_web_contexts: dict[tuple[str, str], tuple[str, int]] = {}
        self._frozen_web_contexts: set[tuple[str, str, str]] = set()
        self._ios_web_context: str | None = None

    def _require_driver(self) -> WebDriver:
        """Helper to ensure self.driver is initialized, else raise error."""
        if self.driver is None:
            internal_logger.debug(self.NOT_INITIALIZED)
            raise OpticsError(Code.E0101, message=self.NOT_INITIALIZED)
        return self.driver

    def _session_capabilities(self) -> dict[str, Any]:
        """Live session capabilities, falling back to the configured ones."""
        caps: dict[str, Any] = {}
        if self.driver is not None:
            try:
                caps = self.driver.capabilities or {}
            except Exception:
                caps = {}
        return caps or self.capabilities or {}

    def _active_platform(self) -> str:
        """Normalized platformName from live or configured capabilities."""
        caps = self._session_capabilities()
        platform = caps.get(self.CAP_PLATFORM_NAME) or caps.get(self.CAP_APPIUM_PLATFORM_NAME)
        if not platform:
            for key, value in caps.items():
                if key.lower() == self.KEY_PLATFORMNAME:
                    platform = value
                    break
        return normalize_platform(platform)

    def native_ui_platform(self) -> str | None:
        """``android`` or ``ios`` while the session reads that phone's native UI tree; None on TVs and in iOS browser or web view sessions."""
        platform = self._active_platform()
        if platform == ANDROID:
            return platform  # browser and autoWebview sessions are parked in NATIVE_APP on creation
        return platform if platform in MOBILE and not _starts_in_web(self._session_capabilities()) else None

    def _require_profile(self) -> PlatformProfile:
        profile = get_profile(self._active_platform())
        if profile is None:
            raise OpticsError(
                Code.E0104,
                message=f"No Appium platform profile matches the configured "
                        f"'platformName'. Supported: {supported_platforms()}.",
            )
        return profile

    def _cleanup_existing_driver(self) -> None:
        """Quit and clear current driver if present."""
        if self.driver is None:
            return
        old_session_id = self.driver.session_id
        internal_logger.info(f"Cleaning up old driver with session_id: {old_session_id}")
        try:
            internal_logger.info("Cleaning up existing driver before starting new session")
            self.driver.quit()
        except Exception as cleanup_error:
            internal_logger.warning(f"Failed to clean up existing driver: {cleanup_error}")
        finally:
            self.driver = None

    def _apply_app_identifier_caps(
        self,
        caps: Dict[str, Any],
        app_package: Optional[str],
        app_activity: Optional[str],
    ) -> None:
        """Update caps with app_package/app_activity; mutate caps in place."""
        if app_package:
            platform = caps.get(self.CAP_PLATFORM_NAME) or caps.get(self.CAP_APPIUM_PLATFORM_NAME)
            profile = get_profile(platform) if platform else None
            if profile is not None:
                for cap_key in profile.app_id_caps:
                    caps[cap_key] = app_package
            elif not platform:
                caps[self.CAP_APP_PACKAGE_LEGACY] = caps[self.CAP_APP_PACKAGE] = app_package
                caps[self.CAP_BUNDLE_ID] = caps[self.CAP_APPIUM_BUNDLE_ID] = app_package
            else:
                internal_logger.warning(f"Unknown platform '{platform}', cannot set app identifier.")
        if app_activity:
            caps[self.CAP_APP_ACTIVITY] = caps[self.CAP_APPIUM_APP_ACTIVITY] = app_activity

    def _try_attach_or_clear_session_caps(
        self, all_caps: Dict[str, Any], event_name: Optional[str]
    ) -> Optional[str]:
        """If capabilities contain an existing session id, try to attach; return session_id or None."""
        existing_sid = next(
            (
                str(all_caps[k]) for k in self.SESSION_ID_CAP_KEYS
                if k in all_caps and all_caps[k] and isinstance(all_caps[k], (str, int, float))
            ),
            None,
        )
        if not existing_sid:
            return None
        internal_logger.info(
            f"Existing Appium session id detected in capabilities: {existing_sid}. Attempting to attach to existing session."
        )
        try:
            attached = self.attach_to_session(
                existing_sid, executor_url=self.appium_server_url, event_name=event_name
            )
            return attached.session_id
        except Exception as attach_error:
            internal_logger.warning(f"Failed to attach to existing session {existing_sid}: {attach_error}")
            internal_logger.info("Falling back to creating a new Appium session.")
            for key in self.SESSION_ID_CAP_KEYS:
                all_caps.pop(key, None)
            return None

    def _create_new_driver_session(self, options: Any, event_name: Optional[str]) -> str:
        """Create Remote driver, set self.driver and ui_helper; return session_id. Raises on failure."""
        if event_name:
            self.event_sdk.capture_event(event_name)
        internal_logger.debug(
            f"Starting Appium session with capabilities: {options.to_capabilities()}"
        )
        internal_logger.debug(
            f"Connection/session-creation timeout: {self.CONNECTION_TIMEOUT}s"
        )
        command_executor, client_config = _command_executor_and_config(
            self.appium_server_url, self.CONNECTION_TIMEOUT
        )
        try:
            self.driver = webdriver.Remote(
                command_executor, options=options, client_config=client_config
            )  # type: ignore
            if self.driver is None:
                raise OpticsError(Code.E0102, message="Failed to create Appium WebDriver instance")
            new_session_id = self.driver.session_id
            internal_logger.info(f"NEW Appium session created with session_id: {new_session_id}")
            self.ui_helper = UIHelper(self)
            if self._active_platform() == ANDROID and _starts_in_web(options.to_capabilities()):
                # Web lookups enter the web per call, so a browser or autoWebview session goes back to native.
                self.driver.switch_to.context(self.NATIVE_CONTEXT)
            return new_session_id
        except Exception as e:
            internal_logger.debug(f"Failed to create new Appium session: {e}")
            self.driver = None
            raise OpticsError(
                Code.E0102, message=f"Failed to create new Appium session due to: {e}", cause=e
            ) from e

    def start_session(
        self,
        app_package: Optional[str] = None,
        app_activity: Optional[str] = None,
        event_name: Optional[str] = None,
    ) -> str:
        """
        Start the Appium session if not already started, incorporating custom capabilities.
        Optionally override appPackage and appActivity capabilities for Android.

        Returns:
            str: The session ID of the running Appium session.
        """
        self._cleanup_existing_driver()
        all_caps = self.capabilities.copy() if self.capabilities else {}
        self._apply_app_identifier_caps(all_caps, app_package, app_activity)

        attached_sid = self._try_attach_or_clear_session_caps(all_caps, event_name=event_name)
        if attached_sid is not None:
            return attached_sid

        options, default_options = self._get_platform_and_options(all_caps)
        final_caps = {**default_options, **all_caps}
        internal_logger.debug(f"Final capabilities being applied: {final_caps}")
        for key, value in final_caps.items():
            options.set_capability(key, value)

        return self._create_new_driver_session(options, event_name=event_name)

    def get_session_id(self) -> Optional[str]:
        """Return the current Appium session id, if a session is active."""
        try:
            return self.driver.session_id if self.driver else None
        except Exception:
            return None

    def get_driver_session_id(self) -> Optional[str]:
        """DriverInterface-compliant getter for Appium session id."""
        return self.get_session_id()

    @staticmethod
    def is_dead_session_error(exc: BaseException) -> bool:
        return isinstance(exc, InvalidSessionIdException)

    def _is_session_alive(self) -> bool:
        if self.driver is None:
            return False
        try:
            self.driver.get_window_size()
        except Exception as e:
            if self.is_dead_session_error(e):
                internal_logger.debug(
                    "Cached Appium session %s is no longer active on the server.",
                    self.get_session_id(),
                )
                return False
        return True

    def _normalize_args(self, args: tuple) -> list:
        """
        Normalize args - unwrap lists containing a single dict (common from JSON parsing).

        :param args: Original arguments tuple
        :return: Normalized arguments list
        """
        normalized_args = []
        for arg in args:
            if isinstance(arg, list) and len(arg) == 1 and isinstance(arg[0], dict):
                normalized_args.append(arg[0])
            else:
                normalized_args.append(arg)
        return normalized_args

    def _execute_script_with_args(self, driver: WebDriver, script: str, normalized_args: list) -> Any:
        """
        Execute script with normalized arguments.

        :param driver: Appium WebDriver instance
        :param script: Script to execute
        :param normalized_args: Normalized arguments list
        :return: Script execution result
        """
        if len(normalized_args) == 0:
            return driver.execute_script(script)
        if len(normalized_args) == 1:
            return driver.execute_script(script, normalized_args[0])
        return driver.execute_script(script, list(normalized_args))

    def _handle_script_execution_error(self, script: str, e: Exception) -> None:
        """
        Handle script execution errors with helpful messages.

        :param script: The script that failed
        :param e: The exception that occurred
        :raises OpticsError: For unsupported mobile commands
        """
        error_msg = str(e)
        is_not_implemented = "Method is not implemented" in error_msg or "NotImplementedError" in error_msg

        if is_not_implemented and script.startswith(self.MOBILE_PREFIX):
            internal_logger.debug(
                f"Mobile command '{script}' is not supported by the current Appium driver. "
                f"This command may not be available for UIAutomator2, or the command name may be incorrect. "
                f"Available mobile commands vary by driver. For pressing keys, consider using press_keycode() method directly."
            )
            raise OpticsError(
                Code.E0401,
                message=f"Mobile command '{script}' is not supported. The command may not exist for UIAutomator2 driver, "
                        f"or the driver doesn't support the generic execute command. "
                        f"For pressing keys, use press_keycode() method instead.",
                cause=e
            ) from e

    def execute_script(self, script: str, *args, event_name: Optional[str] = None) -> Any:
        """
        Execute JavaScript/script in the current Appium context.

        For mobile commands (starting with "mobile:"), uses execute_script() with dict parameters.
        For JavaScript code, uses execute_script() with the provided arguments.

        :param script: The JavaScript code or script command to execute.
        :type script: str
        :param *args: Optional arguments to pass to the script.
        :param event_name: The event triggering the script execution, if any.
        :type event_name: Optional[str]
        :return: The result of the script execution.
        :rtype: Any
        """
        driver = self._require_driver()

        if event_name:
            self.event_sdk.capture_event(event_name)

        normalized_args = self._normalize_args(args)

        try:
            result = self._execute_script_with_args(driver, script, normalized_args)
            internal_logger.debug(f"Executed script: {script[:100]}...")  # Log first 100 chars
            internal_logger.debug(f"Script execution result: {result}")
            return result
        except Exception as e:
            self._handle_script_execution_error(script, e)
            raise

    def _get_options_for_attach(self) -> Any:
        """Return options for session attachment; fall back to UiAutomator2Options if platform unknown."""
        try:
            options, _ = self._get_platform_and_options(self.capabilities or {})
            return options
        except Exception:
            return UiAutomator2Options()

    def _populate_attached_driver_capabilities(self, driver: WebDriver) -> None:
        """Fetch capabilities from the attached session and set them on the driver if possible."""
        try:
            resp = driver.execute(Command.GET_SESSION, None)
            value = resp.get(self.RESPONSE_VALUE, {}) if isinstance(resp, dict) else {}
            capabilities = value.get(self.RESPONSE_CAPABILITIES, value)
            if not isinstance(capabilities, dict):
                return
            if hasattr(driver, "caps"):
                driver.caps = capabilities
            if hasattr(driver, "capabilities"):
                driver.capabilities = capabilities
        except Exception as e:
            internal_logger.debug(f"Could not retrieve capabilities from existing session: {e}")

    def attach_to_session(self, session_id: str, executor_url: Optional[str] = None, event_name: Optional[str] = None) -> WebDriver:
        """
        Attach this driver instance to an existing Appium session.

        This allows connecting Optics to an already running Appium session so that
        keywords can be executed without creating a new session.

        Args:
            session_id: The target Appium session id to attach to.
            executor_url: Appium server URL. Defaults to configured `self.appium_server_url`.
            event_name: Optional event name to record when attaching.

        Returns:
            A WebDriver instance bound to the existing session.

        Raises:
            OpticsError: If attach fails.
        """
        if not session_id:
            raise OpticsError(Code.E0104, message="A non-empty session_id is required to attach.")

        self._cleanup_existing_driver()
        executor = executor_url or self.appium_server_url
        if not executor:
            raise OpticsError(Code.E0104, message="Appium server URL is not configured.")

        command_executor, client_config = _command_executor_and_config(executor, self.CONNECTION_TIMEOUT)

        class SessionAttachmentWebDriver(webdriver.Remote):
            def __init__(
                self,
                command_executor: str | AppiumConnection,
                options: Any,
                target_session_id: str,
                client_config: Optional[AppiumClientConfig] = None,
            ) -> None:
                self._target_session_id = target_session_id
                super().__init__(
                    command_executor=command_executor,
                    options=options,
                    client_config=client_config,
                )

            def execute(self, command: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
                if command == Appium.COMMAND_NEW_SESSION:
                    return {
                        Appium.RESPONSE_VALUE: {
                            Appium.CAP_SESSION_ID: self._target_session_id,
                            Appium.RESPONSE_CAPABILITIES: {},
                        },
                    }
                return super().execute(command, params)

        try:
            options = self._get_options_for_attach()
            attached_driver = SessionAttachmentWebDriver(
                command_executor=command_executor,
                options=options,
                target_session_id=session_id,
                client_config=client_config,
            )
            attached_driver.session_id = session_id
            self._populate_attached_driver_capabilities(attached_driver)

            self.driver = attached_driver
            self.ui_helper = UIHelper(self)
            if event_name:
                self.event_sdk.capture_event(event_name)
            internal_logger.info(f"Attached to existing Appium session with session_id: {session_id}")
            return attached_driver
        except Exception as e:
            internal_logger.debug(f"Failed to attach to existing Appium session {session_id}: {e}")
            self.driver = None
            raise OpticsError(
                Code.E0102,
                message=f"Failed to attach to existing Appium session: {session_id} due to: {e}",
                cause=e,
            ) from e


    def _get_platform_and_options(self, all_caps: Dict[str, Any]) -> tuple[Any, Dict[str, Any]]:
        """Helper to determine platform, create options, and set defaults."""
        platform = all_caps.get(self.CAP_PLATFORM_NAME) or all_caps.get(self.CAP_APPIUM_PLATFORM_NAME)

        if not platform:
            # Fallback for case-insensitivity, though keys are usually case-sensitive
            for key in all_caps:
                if key.lower() == self.KEY_PLATFORMNAME:
                    platform = all_caps[key]
                    break
            if not platform:
                raise OpticsError(Code.E0104, message=f"'{self.CAP_PLATFORM_NAME}' capability is required.")

        internal_logger.debug(f"Appium Server URL: {self.appium_server_url}")
        internal_logger.debug(f"All capabilities from config: {all_caps}")

        profile = get_profile(platform)
        if profile is None:
            raise OpticsError(
                Code.E0104,
                message=f"Unsupported platform: {platform}. Supported: {supported_platforms()}.",
            )
        options = profile.options_factory()
        if _starts_in_web(all_caps):
            return options, dict(profile.default_options)
        return options, {**profile.default_options, **profile.native_session_options}

    def force_terminate_app(self, app_name: str, event_name: Optional[str] = None) -> None:
        """
        Forcefully terminates the specified application.

        :param app_name: The name of the application to terminate.
        :param event_name: The event triggering the forced termination, if any.
        """
        if not self.driver:
            internal_logger.debug(self.NOT_INITIALIZED)
            return

        if event_name:
            self.event_sdk.capture_event(event_name)

        internal_logger.debug(f"Force terminating app: {app_name}")
        try:
            self.driver.terminate_app(app_name)
            internal_logger.info(f"Successfully terminated app: {app_name}")
        except Exception as e:
            internal_logger.error(f"Failed to force terminate app '{app_name}': {e}")

    def terminate(self, event_name: Optional[str] = None) -> None:
        """End the Appium session if active."""
        if self.driver:

            current_session_id = self.driver.session_id
            internal_logger.debug(
                f"Terminating Appium session with session_id: {current_session_id}"
            )
            if event_name:
                self.event_sdk.capture_event(event_name)
            self.driver.quit()
            self.driver = None
            self.event_sdk.send_all_events()

    def get_app_version(self, app_package: Optional[str] = None) -> str:
        """
        Get the version of the currently configured application.

        :param app_package: Optional package name / bundle ID override. When provided,
            this takes precedence over the value in capabilities.
        """
        platform = (
            self.capabilities.get(self.CAP_PLATFORM_NAME)
            or self.capabilities.get(self.CAP_APPIUM_PLATFORM_NAME)
            or ""
        )
        platform_lower = str(platform).strip().lower()

        if platform_lower == self.PLATFORM_ANDROID:
            return self._get_android_app_version(app_package_override=app_package)
        if platform_lower == self.PLATFORM_IOS:
            return self._get_ios_app_version(bundle_id_override=app_package)
        return str(self.capabilities.get("appVersion", "unknown"))

    def _get_android_device_serial(self) -> Optional[str]:
        # Prefer the serial set by UiAutomator2 in session capabilities after connection
        if self.driver is not None:
            session_serial = self.driver.capabilities.get(self.CAP_SESSION_DEVICE_UDID)
            if session_serial:
                return str(session_serial)
        # Fall back to user-configured capability values
        return (
            self.capabilities.get(self.CAP_APPIUM_UDID)
            or self.capabilities.get(self.CAP_UDID)
            or None
        )

    def _get_ios_device_udid(self) -> Optional[str]:
        # XCUITest exposes the connected device UDID in session capabilities
        if self.driver is not None:
            session_udid = self.driver.capabilities.get(self.CAP_UDID)
            if session_udid:
                return str(session_udid)
        return (
            self.capabilities.get(self.CAP_APPIUM_UDID)
            or self.capabilities.get(self.CAP_UDID)
        )

    def _get_android_app_version(self, app_package_override: Optional[str] = None) -> str:
        app_package = (
            self.capabilities.get(self.CAP_APP_PACKAGE_LEGACY)
            or self.capabilities.get(self.CAP_APP_PACKAGE)
            or app_package_override
        )
        if not app_package:
            raise OpticsError(
                Code.E0104,
                message=(
                    f"Missing required capability: appPackage or {self.CAP_APP_PACKAGE}. "
                ),
            )

        device_serial = self._get_android_device_serial()

        adb_cmd = ["adb"]
        if device_serial:
            adb_cmd.extend(["-s", device_serial])

        dumpsys_cmd = adb_cmd + ["shell", "pm", "dump", app_package]

        output = ""
        try:
            internal_logger.info(f"Executing adb command: {' '.join(dumpsys_cmd)}")
            output = subprocess.check_output(  # nosec B603 - static trusted args; package name sourced from session caps
                dumpsys_cmd,
                stderr=subprocess.STDOUT,
                text=True
            )
            for line in output.splitlines():
                if self.VERSION_NAME_PREFIX in line:
                    appver = line.split(self.VERSION_NAME_PREFIX)[-1].strip()
                    internal_logger.info(f"App version: {appver}")
                    return appver

        except Exception as e:
            output = getattr(e, "output", "") or output
            internal_logger.info(f"Error executing adb command {dumpsys_cmd}: {e}")
            raise OpticsError(
                Code.E0401,
                message=(
                    f"Error executing adb command {dumpsys_cmd}: {e}. "
                    f"Output:\n{_summarize_command_output(output)}"
                ),
                details=str(e),
                cause=e
            ) from e

        raise OpticsError(
            Code.E0401,
            message=(
                f"Could not find versionName for package: {app_package} in "
                f"'{' '.join(dumpsys_cmd)}' output.\n"
                f"Output:\n{_summarize_command_output(output)}"
            )
        )

    def _get_ios_app_version(self, bundle_id_override: Optional[str] = None) -> str:
        bundle_id = (
            self.capabilities.get(self.CAP_BUNDLE_ID)
            or self.capabilities.get(self.CAP_APPIUM_BUNDLE_ID)
            or bundle_id_override
        )
        if not bundle_id:
            raise OpticsError(
                Code.E0104,
                message=f"Missing required capability: bundleId or {self.CAP_APPIUM_BUNDLE_ID}. Can't find iOS version.",
            )

        device_udid = self._get_ios_device_udid()
        cmd = ["ideviceinstaller"]
        if device_udid:
            cmd.extend(["-u", device_udid])
        cmd.append("-l")

        output = ""
        try:
            internal_logger.info(f"Executing ideviceinstaller command: {' '.join(cmd)}")
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)  # nosec - static trusted args; udid sourced from session caps
        except FileNotFoundError as e:
            raise OpticsError(
                Code.E0401,
                message="ideviceinstaller not found on PATH. Please install it via 'brew install ideviceinstaller'.",
                details=str(e),
                cause=e,
            ) from e
        except Exception as e:
            raise OpticsError(
                Code.E0401,
                message=(
                    f"ideviceinstaller command failed: {e}. "
                    f"Output:\n{_summarize_command_output(output)}"
                ),
                details=str(e),
                cause=e,
            ) from e

        for line in output.splitlines():
            stripped = line.strip()
            if not stripped or ', ' not in stripped:
                continue
            # Format: bundleId, "CFBundleVersion", "CFBundleDisplayName"
            parts = [p.strip() for p in stripped.split(', ', 2)]
            if len(parts) >= 2 and parts[0].strip() == bundle_id.strip():
                version = parts[1].strip('"')
                internal_logger.info(f"iOS app version for {bundle_id}: {version}")
                return version

        raise OpticsError(
            Code.E0401,
            message=(
                f"Could not find bundle '{bundle_id}' in ideviceinstaller output.\n"
                f"Output:\n{_summarize_command_output(output)}"
            ),
        )

    def initialise_setup(self) -> None:
        """Initialize the Appium setup by starting the session."""
        self.start_session()
        internal_logger.debug("Appium setup initialized.")

    def launch_app(
        self,
        app_identifier: Optional[str] = None,
        app_activity: Optional[str] = None,
        event_name: Optional[str] = None,
    ) -> str:
        """Launch the app, or open a URL or deep link in the app that handles it, starting a session if none is alive."""
        session_id = self.get_session_id()
        if app_identifier and self._is_deeplink(app_identifier):
            if self.driver is None or not self._is_session_alive():
                session_id = self.start_session(event_name=event_name)
            self._open_deeplink(self._require_driver(), app_identifier)
            return session_id
        if self.driver is None or not self._is_session_alive():
            session_id = self.start_session(
                app_package=app_identifier,
                app_activity=app_activity,
                event_name=event_name,
            )
        elif app_identifier:
            try:
                self._require_driver().activate_app(app_identifier)
            except Exception as exc:
                raise OpticsError(
                    Code.E0401, message=f"Failed to launch: {app_identifier}", cause=exc
                ) from exc

        internal_logger.debug(f"Launched application with event: {event_name}")
        return session_id if session_id else None

    def launch_other_app(
            self,
            app_name: str,
            event_name: Optional[str] = None,
    ) -> None:
        """Launch application or deep link."""

        # If no driver, start session but avoid double-counting the event
        if self.driver is None:
            self.start_session(event_name=event_name)
            capture_event_here = False
        else:
            capture_event_here = True

        driver = self._require_driver()

        # Only capture event here if session was already active
        if event_name and capture_event_here:
            self.event_sdk.capture_event(event_name)

        try:
            if self._is_deeplink(app_name):
                self._open_deeplink(driver, app_name)
                return

            driver.activate_app(app_name)
            internal_logger.debug(f"Activated app: {app_name}")

        except OpticsError:
            # Preserve original OpticsError without wrapping
            raise
        except Exception as exc:
            raise OpticsError(
                Code.E0401,
                message=f"Failed to launch: {app_name}",
                cause=exc,
            ) from exc


    def get_driver(self) -> Optional[WebDriver]:
        """Return the Appium driver instance."""
        return self.driver

    # APPIUM api wrappers
    @supported_on(*MOBILE)
    def click_element(self, element: Any, event_name: Optional[str] = None) -> None:
        """
        Click on the specified element using Appium's click method.
        """
        timestamp = self.event_sdk.get_current_time_for_events()
        try:
            element.click()
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
                internal_logger.debug(f"Clicked on element: {element} at {timestamp}")
        except Exception as e:
            internal_logger.debug(e)

    @supported_on(*MOBILE)
    def tap_at_coordinates(self, x: int, y: int, event_name: Optional[str] = None) -> None:
        """
        Simulates a tap gesture at the specified screen coordinates using Appium's `tap` method.
        """
        driver = self._require_driver()
        timestamp = self.event_sdk.get_current_time_for_events()
        try:
            driver.tap([(x, y)], 100)
        except WebDriverException as e:
            raise OpticsError(Code.E0401, message=f"Failed to tap at ({x}, {y})", cause=e) from e
        if event_name:
            self.event_sdk.capture_event_with_time_input(event_name, timestamp)
        internal_logger.debug(f"Tapped at coordinates ({x}, {y})")

    @supported_on(*MOBILE)
    def swipe(
        self,
        x_coor: int,
        y_coor: int,
        direction: str,
        swipe_length: int,
        event_name: Optional[str] = None
    ) -> None:

        driver = self._require_driver()

        x_coor = int(x_coor)
        y_coor = int(y_coor)
        swipe_length = int(swipe_length)

        end_x: int = x_coor
        end_y: int = y_coor

        if direction == "up":
            end_y = y_coor - swipe_length
        elif direction == "down":
            end_y = y_coor + swipe_length
        elif direction == "left":
            end_x = x_coor - swipe_length
        elif direction == "right":
            end_x = x_coor + swipe_length
        else:
            internal_logger.error(f"Unknown swipe direction: {direction}")
            return

        platform = (
            self.capabilities.get(self.CAP_PLATFORM_NAME)
            or self.capabilities.get(self.CAP_APPIUM_PLATFORM_NAME)
        )

        timestamp = self.event_sdk.get_current_time_for_events()

        try:
            # The standard driver.swipe() is flaky on iOS, so we're using a script for getting consistent results
            if str(platform).lower() == self.PLATFORM_IOS:
                internal_logger.debug(
                    f"iOS swipe (W3C) from ({x_coor},{y_coor}) to ({end_x},{end_y})"
                )
                actions = ActionBuilder(
                    driver,
                    mouse=PointerInput(interaction.POINTER_TOUCH, "touch")
                )
                actions.pointer_action.move_to_location(x_coor, y_coor)
                actions.pointer_action.pointer_down()
                actions.pointer_action.pause(0.4)   # Pausing before next action is important due to API latency
                actions.pointer_action.move_to_location(end_x, end_y)
                actions.pointer_action.pointer_up()
                actions.perform()
            else:
                internal_logger.debug(
                    f"Android swipe from ({x_coor},{y_coor}) to ({end_x},{end_y})"
                )
                driver.swipe(x_coor, y_coor, end_x, end_y, 1000)

            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)

        except Exception as e:
            internal_logger.debug(
                f"Failed swipe from ({x_coor},{y_coor}) to ({end_x},{end_y}): {e}"
            )


    @supported_on(*MOBILE)
    def swipe_percentage(
        self,
        x_percentage: int,
        y_percentage: int,
        direction: str,
        swipe_length_percentage: int,
        event_name: Optional[str] = None
    ) -> None:
        driver = self._require_driver()
        window_size = driver.get_window_size()
        width = window_size["width"]
        height = window_size["height"]
        start_x = int(width * x_percentage / 100)
        start_y = int(height * y_percentage / 100)
        swipe_length: int
        if direction in ("up", "down"):
            swipe_length = int(height * swipe_length_percentage / 100)
        elif direction in ("left", "right"):
            swipe_length = int(width * swipe_length_percentage / 100)
        else:
            internal_logger.error(f"Unknown swipe direction: {direction}")
            return
        self.swipe(start_x, start_y, direction, swipe_length, event_name=event_name)

    @supported_on(*MOBILE)
    def swipe_element(
        self,
        element: Any,
        direction: str,
        swipe_length: int,
        event_name: Optional[str] = None
    ) -> None:
        driver = self._require_driver()
        location = element.location
        swipe_length = int(swipe_length)
        size = element.size
        start_x: int = location["x"]
        start_y: int = location["y"]
        end_x: int
        end_y: int
        dir_lower = direction.lower() if isinstance(direction, str) else direction
        if dir_lower in ("up", "down"):
            start_x = location["x"] + size["width"] // 2
            end_x = start_x
            end_y = start_y + swipe_length if dir_lower == "down" else start_y - swipe_length
        elif dir_lower in ("left", "right"):
            start_y = location["y"] + size["height"] // 2
            end_y = start_y
            end_x = start_x + swipe_length if dir_lower == "right" else start_x - swipe_length
        else:
            internal_logger.error(f"Unknown swipe direction: {direction}")
            return
        timestamp = self.event_sdk.get_current_time_for_events()
        try:
            internal_logger.debug(
                f"Swiping (W3C Action) from ({start_x}, {start_y}) to ({end_x}, {end_y})"
            )
            try:
                # Use ActionChains + ActionBuilder with a touch pointer for W3C actions
                actions = ActionChains(driver)
                actions.w3c_actions = ActionBuilder(driver, mouse=PointerInput(interaction.POINTER_TOUCH, "touch"))
                # Move to start location, press, move to end location, then release
                actions.w3c_actions.pointer_action.move_to_location(int(start_x), int(start_y))
                actions.w3c_actions.pointer_action.pointer_down()
                actions.w3c_actions.pointer_action.pause(0.2)
                actions.w3c_actions.pointer_action.move_to_location(int(end_x), int(end_y))
                actions.w3c_actions.pointer_action.pause(0.2)
                actions.w3c_actions.pointer_action.pointer_up()
                actions.perform()

                if event_name:
                    self.event_sdk.capture_event_with_time_input(event_name, timestamp)
            except Exception as w3c_e:
                # Fallback: log and continue (keeps previous behavior of not raising)
                internal_logger.debug(
                    f"W3C Action swipe failed from ({start_x}, {start_y}) to ({end_x}, {end_y}): {w3c_e}"
                )
        except Exception as e:
            internal_logger.debug(
                f"Failed to perform TouchAction swipe from ({start_x}, {start_y}) to ({end_x}, {end_y}): {e}"
            )

    @supported_on(*MOBILE)
    def scroll(
        self,
        direction: str,
        duration: int = 1000,
        event_name: Optional[str] = None
    ) -> None:
        driver = self._require_driver()
        window_size = driver.get_window_size()
        width = window_size["width"]
        height = window_size["height"]
        start_x: int
        start_y: int
        end_y: int
        if direction == "up":
            start_x = width // 2
            start_y = int(height * 0.2)
            end_y = int(height * 0.8)
        elif direction == "down":
            start_x = width // 2
            start_y = int(height * 0.8)
            end_y = int(height * 0.2)
        else:
            internal_logger.error(f"Scroll direction '{direction}' not supported.")
            return
        timestamp = self.event_sdk.get_current_time_for_events()
        try:
            internal_logger.debug(
                f"Scrolling {direction} from ({start_x}, {start_y}) to ({start_x}, {end_y})"
            )
            driver.swipe(start_x, start_y, start_x, end_y, duration)
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
        except Exception as e:
            internal_logger.debug(f"Failed to scroll {direction}: {e}")

    @supported_on(*MOBILE)
    def enter_text_element(self, element: Any, text: Union[str, SpecialKey], event_name: Optional[str] = None) -> None:
        if event_name:
            self.event_sdk.capture_event(event_name)

        if isinstance(text, SpecialKey):
            keycode = self.KEYCODE_MAP.get(text)
            if keycode:
                internal_logger.debug(
                    f"Pressing Detected SpecialKey in element: {text}. Keycode: {keycode}"
                )
                internal_logger.debug(f"Pressing SpecialKey in element: {text}")
                driver = self._require_driver()
                driver.press_keycode(keycode)
            else:
                internal_logger.warning(f"Unknown special key in element: {text}")
                internal_logger.debug(f"Unknown special key in element, treating as text: {text}")
                element.send_keys(utils.strip_sensitive_prefix(str(text)))
        else:
            internal_logger.debug(f"Entering text '{text}' into element: {element}")
            element.send_keys(utils.strip_sensitive_prefix(str(text)))

    @supported_on(*MOBILE)
    def clear_text_element(self, element: Any, event_name: Optional[str] = None) -> None:
        if event_name:
            self.event_sdk.capture_event(event_name)
        internal_logger.debug(f"Clearing text in element: {element}")
        element.clear()

    def _is_ios(self) -> bool:
        return self._active_platform() == self.PLATFORM_IOS

    def _enter_text_ios(self, driver: WebDriver, text: str | SpecialKey) -> None:
        """XCUITest has no 'mobile: type' or keycodes; type into the element that has keyboard focus.

        With nothing focused this raises: 'mobile: keys' answers 200 there but types nothing, which
        would let the keyword pass without entering any text.
        """
        if isinstance(text, SpecialKey):
            keys = self.IOS_SPECIAL_KEYS.get(text)
            if keys is None:
                raise OpticsError(Code.E0401, message=f"Special key '{text.value}' is not supported on iOS")
        else:
            keys = utils.strip_sensitive_prefix(str(text))
        try:
            focused = driver.switch_to.active_element
        except WebDriverException as e:
            raise OpticsError(
                Code.E0401, message="No element has keyboard focus on iOS; tap the field before entering text", cause=e
            ) from e
        focused.send_keys(keys)
        if not isinstance(text, SpecialKey):
            self._wait_for_keyboard(driver)

    @staticmethod
    def _wait_for_keyboard(driver: WebDriver) -> None:
        """Return once the keyboard is on screen and still: iOS can raise it a second after typing, moving the page."""
        bottom = driver.get_window_size()["height"]
        deadline = time.monotonic() + _KEYBOARD_TIMEOUT_S
        last = None
        while time.monotonic() < deadline:
            keyboards = driver.find_elements(AppiumBy.CLASS_NAME, "XCUIElementTypeKeyboard")
            if not keyboards:
                return
            top = keyboards[0].rect["y"]
            if top < bottom and top == last:
                return
            last = top
            time.sleep(_KEYBOARD_POLL_S)

    @supported_on(*MOBILE)
    def enter_text(self, text: Union[str, SpecialKey], event_name: Optional[str] = None) -> None:
        driver = self._require_driver()
        if event_name:
            self.event_sdk.capture_event(event_name)

        if self._is_ios():
            self._enter_text_ios(driver, text)
            return

        if isinstance(text, SpecialKey):
            keycode = self.KEYCODE_MAP.get(text)
            if keycode:
                internal_logger.debug(
                    f"Pressing Detected SpecialKey: {text}. Keycode: {keycode}"
                )
                internal_logger.debug(f"Pressing SpecialKey: {text}")
                driver.press_keycode(keycode)
            else:
                internal_logger.warning(f"Unknown special key: {text}")
                internal_logger.debug(f"Unknown special key, treating as text: {text}")
                text_to_send = utils.strip_sensitive_prefix(str(text))
                driver.execute_script(self.MOBILE_TYPE_COMMAND, {"text": text_to_send})
        else:
            internal_logger.debug(f"Entering text: {text}")
            text_to_send = utils.strip_sensitive_prefix(str(text))
            driver.execute_script(self.MOBILE_TYPE_COMMAND, {"text": text_to_send})

    @supported_on(*MOBILE)
    def clear_text(self, event_name: Optional[str] = None) -> None:
        driver = self._require_driver()
        if event_name:
            self.event_sdk.capture_event(event_name)
        internal_logger.debug("Clearing text input")
        driver.execute_script(self.MOBILE_CLEAR)

    def press_keycode(self, keycode: str, event_name: Optional[str] = None) -> None:
        driver = self._require_driver()
        if event_name:
            self.event_sdk.capture_event(event_name)
        internal_logger.debug(f"Pressing keycode: {keycode}")
        profile = self._require_profile()
        if profile.keycode_strategy == KEYCODE_RC_NAMED:
            rc_key = profile.resolve_rc_key(utils.strip_sensitive_prefix(str(keycode)))
            payload = {"key": rc_key, **profile.rc_extra_payload}
            internal_logger.debug(f"{profile.rc_command} -> {payload}")
            driver.execute_script(profile.rc_command, payload)
        else:
            driver.press_keycode(int(utils.strip_sensitive_prefix(keycode)))

    def _handle_special_key_keyboard_input(self, driver: WebDriver, text: SpecialKey) -> None:
        """Send a SpecialKey via keycode or mobile:type fallback."""
        keycode = self.KEYCODE_MAP.get(text)
        if keycode is not None:
            internal_logger.debug(f"Pressing Detected SpecialKey: {text}. Keycode: {keycode}")
            internal_logger.debug(f"Pressing SpecialKey: {text}")
            driver.press_keycode(keycode)
            return
        internal_logger.warning(f"Unknown special key: {text}")
        internal_logger.debug(f"Unknown special key, treating as text: {text}")
        driver.execute_script(
            self.MOBILE_TYPE_COMMAND, {"text": utils.strip_sensitive_prefix(str(text))}
        )

    def _handle_string_keyboard_input(self, driver: WebDriver, text_value: str) -> None:
        """Type a string character-by-character, flushing unmapped chars via mobile:type."""
        internal_logger.debug(f"Entering text using keyboard (per-char): {text_value}")
        buffer: List[str] = []
        for ch in text_value:
            keycode = self.get_char_as_keycode(ch)
            if keycode is not None:
                self._flush_keyboard_buffer(driver, buffer)
                self._press_keycode_or_type_char(driver, ch, keycode)
            else:
                buffer.append(ch)
        self._flush_keyboard_buffer(driver, buffer)

    def _flush_keyboard_buffer(self, driver: WebDriver, buffer: List[str]) -> None:
        """Send buffered chars via mobile:type and clear the buffer."""
        if not buffer:
            return
        segment = "".join(buffer)
        internal_logger.debug(f"Flushing unmapped segment via script: '{segment}'")
        driver.execute_script(
            self.MOBILE_TYPE_COMMAND, {"text": utils.strip_sensitive_prefix(segment)}
        )
        buffer.clear()

    def _press_keycode_or_type_char(self, driver: WebDriver, ch: str, keycode: int) -> None:
        """Press keycode for char, or fall back to mobile:type on failure."""
        metastate = 1 if ch.isupper() else None
        internal_logger.debug(f"Pressing keycode for char '{ch}': {keycode} (metastate={metastate})")
        try:
            if metastate is not None:
                driver.press_keycode(int(keycode), metastate=metastate)
            else:
                driver.press_keycode(int(keycode))
        except Exception:
            internal_logger.debug(f"press_keycode failed for '{ch}', falling back to script typing")
            driver.execute_script(
                self.MOBILE_TYPE_COMMAND, {"text": utils.strip_sensitive_prefix(ch)}
            )

    @supported_on(*MOBILE)
    def enter_text_using_keyboard(
        self,
        text: Union[str, SpecialKey],
        event_name: Optional[str] = None
    ) -> None:
        driver = self._require_driver()
        try:
            timestamp = self.event_sdk.get_current_time_for_events()
            if self._is_ios():
                self._enter_text_ios(driver, text)
            elif isinstance(text, SpecialKey):
                self._handle_special_key_keyboard_input(driver, text)
            else:
                self._handle_string_keyboard_input(driver, str(text))
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
        except Exception as e:
            raise OpticsError(Code.E0401, message=f"Error during text input: {e}", cause=e) from e

    def get_char_as_keycode(self, char: str) -> Optional[int]:
        # Basic lowercase mapping; extend as needed
        mapping = {
            "a": 29,
            "b": 30,
            "c": 31,
            "d": 32,
            "e": 33,
            "f": 34,
            "g": 35,
            "h": 36,
            "i": 37,
            "j": 38,
            "k": 39,
            "l": 40,
            "m": 41,
            "n": 42,
            "o": 43,
            "p": 44,
            "q": 45,
            "r": 46,
            "s": 47,
            "t": 48,
            "u": 49,
            "v": 50,
            "w": 51,
            "x": 52,
            "y": 53,
            "z": 54,
            "0": 7,
            "1": 8,
            "2": 9,
            "3": 10,
            "4": 11,
            "5": 12,
            "6": 13,
            "7": 14,
            "8": 15,
            "9": 16,
            " ": 62,
            "\n": 66,  # Enter key
        }

        # Handle lowercase input so uppercase letters map to correct keycodes
        return mapping.get(char.lower())

    @supported_on(*MOBILE)
    def go_back(self, event_name: Optional[str] = None) -> None:
        """iOS has no system back: XCUITest taps a navigation bar button, and when the screen has
        none it only logs, which would let the keyword pass without going back.
        """
        driver = self._require_driver()
        if self._is_ios() and not driver.find_elements(
            AppiumBy.IOS_CLASS_CHAIN, "**/XCUIElementTypeNavigationBar/XCUIElementTypeButton"
        ):
            raise OpticsError(
                Code.E0401, message="No navigation bar back button on this iOS screen; Go Back has nothing to tap"
            )
        if event_name:
            self.event_sdk.capture_event(event_name)
        driver.back()

    @supported_on(*MOBILE)
    def get_text_element(self, element: Any) -> str:
        text = element.get_attribute("text")
        if text is None:
            try:
                text = element.get_attribute("value")
            except WebDriverException:
                text = None
        internal_logger.info(f"Text of element: {text}")
        if text is None:
            raise OpticsError(Code.E0401, message="Element text is None")
        return text

    # helper functions
    def pixel_2_appium(self, x: int, y: int, screenshot: Any) -> Optional[tuple[int, int]]:
        driver = self._require_driver()
        if not x or not y:
            return None
        window_size = driver.get_window_size()
        screen_width = window_size["width"]
        screen_height = window_size["height"]
        internal_logger.debug(f"Appium Window Size: {screen_width, screen_height}")
        screenshot_height, screenshot_width = screenshot.shape[:2]
        internal_logger.debug(f"screenshot size: {screenshot_width, screen_height}")
        scaled_x = int(x * screen_width / screenshot_width)
        scaled_y = int(y * screen_height / screenshot_height)
        internal_logger.debug(f"scaled values : {scaled_x, scaled_y}")
        return scaled_x, scaled_y

    # action keywords

    @supported_on(*MOBILE)
    def press_element(self, element: Any, repeat: int, event_name: Optional[str] = None) -> None:
        timestamp = None
        for _ in range(repeat):
            try:
                timestamp = self.event_sdk.get_current_time_for_events()
                element.click()
            except Exception as e:
                raise OpticsError(Code.E0401, message=f"Error occurred while clicking on element: {e}", cause=e) from e
        if event_name and timestamp is not None:
            self.event_sdk.capture_event_with_time_input(event_name, timestamp)
            internal_logger.debug("Clicked on element: %s at %s", element, timestamp)

    @supported_on(*MOBILE)
    def press_coordinates(self, coor_x: int, coor_y: int, event_name: Optional[str] = None) -> None:
        """
        Press an element by absolute coordinates.

        Args:
            self.tap_at_coordinates(coor_x, coor_y, event_name)
            coor_y (int): The y-coordinate to press.
            repeat (int): The number of times to repeat the press.
            event_name (str | None): The name of the event to trigger, if any.
        """
        coor_x, coor_y = int(coor_x), int(coor_y)
        internal_logger.debug(f"Pressing at coordinates: ({coor_x}, {coor_y})")
        self.tap_at_coordinates(coor_x, coor_y, event_name=event_name)

    @supported_on(*MOBILE)
    def press_percentage_coordinates(
        self,
        percentage_x: float,
        percentage_y: float,
        repeat: int,
        event_name: Optional[str] = None
    ) -> None:
        percentage_x, percentage_y = int(percentage_x), int(percentage_y)
        driver = self._require_driver()
        window_size = driver.get_window_size()
        x = int(window_size["width"] * percentage_x / 100)
        y = int(window_size["height"] * percentage_y / 100)
        for _ in range(repeat):
            internal_logger.debug(
                f"Pressing at percentage coordinates: ({percentage_x}%, {percentage_y}%)"
            )
            self.press_coordinates(x, y, event_name=event_name)

    @supported_on(*MOBILE)
    def press_xpath_using_coordinates(self, xpath: str, event_name: Optional[str] = None) -> None:
        """
        Press an element by its XPath using the bounding box coordinates.
        Can be used as a fallback method when interacting with element is not possible.
        Args:
            xpath (str): The XPath of the element to press.
            event_name (str | None): The name of the event to trigger, if any.
        """
        if self.ui_helper is None:
            raise OpticsError(Code.E0101, message="UIHelper is not initialized.")
        bbox = self.ui_helper.get_bounding_box_for_xpath(xpath)
        if bbox:
            # Unpack bbox as ((x1, y1), (x2, y2))
            (x1, y1), (x2, y2) = bbox
            x_centre = (x1 + x2) // 2
            y_centre = (y1 + y2) // 2
            self.tap_at_coordinates(x_centre, y_centre, event_name=event_name)
        else:
            internal_logger.debug(
                f"Bounding box not found for element with xpath: {xpath}"
            )

    def appium_find_element(self, element: str) -> Optional[Any]:
        element_type: str = utils.determine_element_type(element)
        if self.driver is None:
            internal_logger.error(self.NOT_INITIALIZED)
            return None
        if element_type == "XPath":
            return self.driver.find_element(AppiumBy.XPATH, element)
        elif element_type == "Text":
            return self.driver.find_element(AppiumBy.ACCESSIBILITY_ID, element)
        else:
            internal_logger.error(f"Unknown element type: {element_type}")
            return None

    def _is_deeplink(self, value: str) -> bool:
        return isinstance(value, str) and "://" in value

    @supported_on(*MOBILE)
    @contextmanager
    def web_context(self, queries: Sequence[tuple[str, str]] = ()) -> Iterator[WebDriver]:
        """Run the body in the foreground app's front web page, on iOS the first holding a ``queries`` match; then go native."""
        driver = self._require_driver()
        try:
            if self._is_ios():
                self._enter_ios_web_context(driver, queries)
            else:
                self._enter_android_web_context(driver)
            yield driver
        finally:
            try:
                driver.switch_to.context(self.NATIVE_CONTEXT)
            except WebDriverException as e:
                raise_if_session_dead(e)
                raise OpticsError(
                    Code.E0106,
                    message="The session could not return to the native context after reading the web page; "
                            "start a new session.",
                    cause=e,
                ) from e

    def _enter_android_web_context(self, driver: WebDriver) -> None:
        package = driver.current_package
        if not self._reenter_web_context(driver, package):
            context, page_url, app_webviews = self._foreground_web_context(driver, package)
            try:
                driver.switch_to.context(context)
            except NoSuchContextException:
                # A browserName session exposes its browser under one fixed context name.
                if self.BROWSER_SESSION_CONTEXT not in driver.contexts:
                    raise
                context = self.BROWSER_SESSION_CONTEXT
                driver.switch_to.context(context)
            except WebDriverException as e:
                # Seen with Samsung Internet, whose DevTools socket chromedriver cannot attach to.
                reason = e.msg.splitlines()[0] if e.msg else type(e).__name__
                raise OpticsError(
                    Code.E0201,
                    message=f"Cannot read the web page shown by '{package}': {reason}. Web locators work "
                            "in Chrome and in WebViews with debugging enabled; use text locators or OCR here.",
                    cause=e,
                ) from e
            self._focus_window(driver, page_url)
            self._known_web_contexts[(driver.session_id, package)] = (context, app_webviews)

    def _enter_ios_web_context(self, driver: WebDriver, queries: Sequence[tuple[str, str]] = ()) -> None:
        """Enter the visible page of the app in front: its WKWebView, a Safari sheet (SafariViewService) or Safari."""
        pid = driver.execute_script("mobile: activeAppInfo").get("pid")
        listed = [
            context for context in driver.execute_script(_GET_CONTEXTS) or []
            if str(context.get("id", "")).startswith("WEBVIEW_")
        ]
        self._frozen_web_contexts &= {_ios_page(context) for context in listed}
        candidates = sorted(
            (
                context for context in listed
                if _ios_page(context) not in self._frozen_web_contexts
                and (context.get("bundleId") == _SAFARI_VIEW_SERVICE or _ios_context_pid(context["id"]) == pid)
            ),
            key=lambda context: (context.get("bundleId") != _SAFARI_VIEW_SERVICE, context["id"] != self._ios_web_context),
        )
        fallback = None
        for context in candidates:
            try:
                driver.switch_to.context(context["id"])
                if not self._attached_tab_visible(driver):
                    continue
                if context.get("bundleId") in _ONE_PAGE_ON_SCREEN or _page_holds(driver, queries):
                    self._ios_web_context = context["id"]
                    return
                fallback = fallback or context["id"]
            except TimeoutException:
                # A page left behind by a closed view answers nothing until webviewAtomWaitTimeout; skip it until
                # its address or title changes, as a live page that was only busy loading does.
                self._frozen_web_contexts.add(_ios_page(context))
            except WebDriverException as e:
                raise_if_session_dead(e)
                internal_logger.debug(f"Appium: cannot read web context {context['id']}: {e.msg}")
        if fallback is not None:
            driver.switch_to.context(fallback)
            self._ios_web_context = fallback
            return
        raise OpticsError(
            Code.E0201,
            message="No visible web content in the app in front. An app's WKWebView is automatable only when the app "
                    "sets isInspectable; on a real device, also turn on Web Inspector and Remote Automation in "
                    "Settings > Apps > Safari > Advanced.",
        )

    def _reenter_web_context(self, driver: WebDriver, package: str) -> bool:
        """Re-enter ``package``'s known web context, skipping ``mobile: getContexts``, which hangs on frozen apps."""
        known = self._known_web_contexts.get((driver.session_id, package))
        if known is None:
            return False
        context, app_webviews = known
        try:
            driver.switch_to.context(context)
            handles = driver.window_handles
            if len(handles) > 1 and (app_webviews or not self._attached_tab_visible(driver)):
                return False
        except WebDriverException:
            return False
        return True

    def _foreground_web_context(self, driver: WebDriver, package: str) -> tuple[str, str | None, int]:
        """The foreground package's web context, its front page URL (pages list most recent first), and app WebView count."""
        owned = [
            webview for webview in driver.execute_script(_GET_CONTEXTS) or []
            if _context_name(webview) and webview.get("pages")
            and ((webview.get("info") or {}).get("Android-Package") == package
                 or _context_name(webview) == f"WEBVIEW_{package}")
        ]
        if owned:
            app_webviews = sum(_is_app_webview_page(page) for webview in owned for page in webview["pages"])
            return _context_name(owned[0]), owned[0]["pages"][0].get("url"), app_webviews
        raise OpticsError(
            Code.E0201,
            message=f"No web content found in the foreground app '{package}'. An app's "
                    "WebView is automatable only when the app enables WebView debugging.",
        )

    @staticmethod
    def _focus_window(driver: WebDriver, page_url: str | None) -> None:
        """Focus the tab showing the front page, matched by URL in the DevTools target list, which has no tab order."""
        handles = driver.window_handles
        if len(handles) < 2 or not page_url:
            return
        driver.command_executor.add_command(_CDP_EXECUTE, "POST", "/session/$sessionId/goog/cdp/execute")
        targets = driver.execute(_CDP_EXECUTE, {"cmd": "Target.getTargets", "params": {}})["value"]["targetInfos"]
        matches = [
            target["targetId"] for target in targets
            if target.get("url") == page_url and target.get("targetId") in handles
        ]
        try:
            attached = driver.current_window_handle
        except NoSuchWindowException:
            attached = None
        if len(matches) > 1 and attached in matches:
            # Same-URL tabs (a reopened Custom Tab link): keep the attached one only if it is the visible one.
            matches = [attached] if Appium._attached_tab_visible(driver) else [h for h in matches if h != attached]
        if len(matches) != 1:
            reason = "several tabs show" if matches else "no tab shows"
            raise OpticsError(
                Code.E0201,
                message=f"Cannot tell which browser tab is on screen: {reason} '{page_url}'. "
                        "Close the other tabs, or open the page once.",
            )
        if matches[0] != attached:
            driver.switch_to.window(matches[0])

    @staticmethod
    def _attached_tab_visible(driver: WebDriver) -> bool:
        return driver.execute_script(_PAGE_SHOWN_JS) is True

    def web_element_screen_rect(self, element: Any) -> dict[str, int]:
        """Device-screen rectangle of an element found inside :meth:`web_context`."""
        left, top, width, height = self._require_driver().execute_script(_WEB_RECT_JS, element)
        return self.web_rects_to_screen([{"x": left, "y": top, "width": width, "height": height}])[0]

    def web_rects_to_screen(self, rects: list[dict[str, Any] | None]) -> list[dict[str, Any] | None]:
        """Unclamped screen rects of page boxes."""
        origin_x, origin_y, factor_x, factor_y = self._web_transform(self._require_driver())
        return [
            None if rect is None else {
                **rect,
                "x": round(origin_x + rect["x"] * factor_x),
                "y": round(origin_y + rect["y"] * factor_y),
                "width": round(rect["width"] * factor_x),
                "height": round(rect["height"] * factor_y),
            }
            for rect in rects
        ]

    def _web_transform(self, driver: WebDriver) -> tuple[float, float, float, float]:
        """Screen origin and screen units per CSS pixel of the page: browsers draw it under toolbars, so measure a probe."""
        label = f"optics-probe-{uuid.uuid4().hex}"
        context = driver.current_context
        left, top, width, height = driver.execute_script(_PROBE_JS, label)
        try:
            driver.switch_to.context(self.NATIVE_CONTEXT)
            try:
                rects: list[dict[str, int]] = []
                deadline = time.monotonic() + _PROBE_TIMEOUT_S
                # Android lists a new node in its accessibility tree a moment after the page adds it.
                while not rects and time.monotonic() < deadline:
                    rects = [element.rect for element in driver.find_elements(AppiumBy.ACCESSIBILITY_ID, label)]
            finally:
                driver.switch_to.context(context)
        finally:
            driver.execute_script(_PROBE_REMOVE_JS, label)
        if len(rects) != 1 or not width or not height:
            raise OpticsError(
                Code.E0201,
                message="Cannot place the web element: the page is not in the screen's accessibility tree.",
            )
        native = rects[0]
        factor_x, factor_y = native["width"] / width, native["height"] / height
        return native["x"] - left * factor_x, native["y"] - top * factor_y, factor_x, factor_y

    def _open_deeplink(
            self,
            driver: WebDriver,
            deeplink: str,
    ) -> None:

        platform = (
                self.capabilities.get(self.CAP_PLATFORM_NAME)
                or self.capabilities.get(self.CAP_APPIUM_PLATFORM_NAME)
        )

        if not platform:
            raise OpticsError(
                Code.E0104,
                message="platformName capability missing",
            )

        platform = str(platform).lower()

        if platform == self.PLATFORM_ANDROID:
            self._open_android_deeplink(driver, deeplink)
            return

        if platform == self.PLATFORM_IOS:
            self._open_ios_deeplink(driver, deeplink)
            return

        raise OpticsError(
            Code.E0104,
            message=f"Unsupported platform: {platform}",
        )

    def _open_android_deeplink(
            self,
            driver: WebDriver,
            deeplink: str,
    ) -> None:

        args = {"url": deeplink}
        # A browser session opens web links in its own browser, not the device default.
        browser = self.ANDROID_BROWSER_PACKAGES.get(str(self._session_capabilities().get("browserName", "")).lower())
        if browser and urlsplit(deeplink).scheme in ("http", "https"):
            args["package"] = browser
        try:
            driver.execute_script("mobile: deepLink", args)
        except WebDriverException as e:
            raise OpticsError(Code.E0401, message=f"Failed to open '{deeplink}': {e.msg}", cause=e) from e
        internal_logger.debug(f"Android deep link launched: {deeplink}")

    def _open_ios_deeplink(
            self,
            driver: WebDriver,
            deeplink: str,
    ) -> None:

        driver.execute_script(
            "mobile: launchApp",
            {"bundleId": "com.apple.mobilesafari"},
        )

        driver.get(deeplink)

        internal_logger.debug(
            f"iOS deep link launched: {deeplink}"
        )
