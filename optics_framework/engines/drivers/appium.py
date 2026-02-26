import subprocess  # nosec
from typing import Any, Dict, List, Optional, Union
from appium import webdriver
from appium.webdriver.webdriver import WebDriver
from appium.webdriver.client_config import AppiumClientConfig
from selenium.webdriver.remote.command import Command  # type: ignore
from appium.options.android.uiautomator2.base import UiAutomator2Options
from appium.options.ios import XCUITestOptions # type: ignore
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
from optics_framework.common.error import OpticsError, Code



class Appium(DriverInterface):
    DEPENDENCY_TYPE = "driver_sources"
    NAME = "appium"
    NOT_INITIALIZED = "Appium driver is not initialized. Please start the session first."
    MOBILE_TYPE_COMMAND = "mobile: type"
    MOBILE_PREFIX = "mobile:"
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

    def _require_driver(self) -> WebDriver:
        """Helper to ensure self.driver is initialized, else raise error."""
        if self.driver is None:
            internal_logger.debug(self.NOT_INITIALIZED)
            raise OpticsError(Code.E0101, message=self.NOT_INITIALIZED)
        return self.driver

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
            if platform:
                pl = str(platform).lower()
                if pl == self.PLATFORM_ANDROID:
                    caps[self.CAP_APP_PACKAGE_LEGACY] = caps[self.CAP_APP_PACKAGE] = app_package
                elif pl == self.PLATFORM_IOS:
                    caps[self.CAP_BUNDLE_ID] = caps[self.CAP_APPIUM_BUNDLE_ID] = app_package
                else:
                    internal_logger.warning(f"Unknown platform '{platform}', cannot set app identifier.")
            else:
                caps[self.CAP_APP_PACKAGE_LEGACY] = caps[self.CAP_APP_PACKAGE] = app_package
                caps[self.CAP_BUNDLE_ID] = caps[self.CAP_APPIUM_BUNDLE_ID] = app_package
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
        client_config = AppiumClientConfig(
            remote_server_addr=self.appium_server_url, timeout=self.CONNECTION_TIMEOUT
        )
        try:
            self.driver = webdriver.Remote(
                self.appium_server_url, options=options, client_config=client_config
            )  # type: ignore
            if self.driver is None:
                raise OpticsError(Code.E0102, message="Failed to create Appium WebDriver instance")
            new_session_id = self.driver.session_id
            internal_logger.info(f"NEW Appium session created with session_id: {new_session_id}")
            self.ui_helper = UIHelper(self)
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

        attached_sid = self._try_attach_or_clear_session_caps(all_caps, event_name)
        if attached_sid is not None:
            return attached_sid

        options, default_options = self._get_platform_and_options(all_caps)
        final_caps = {**default_options, **all_caps}
        internal_logger.debug(f"Final capabilities being applied: {final_caps}")
        for key, value in final_caps.items():
            options.set_capability(key, value)

        return self._create_new_driver_session(options, event_name)

    def get_session_id(self) -> Optional[str]:
        """Return the current Appium session id, if a session is active."""
        try:
            return self.driver.session_id if self.driver else None
        except Exception:
            return None

    def get_driver_session_id(self) -> Optional[str]:
        """DriverInterface-compliant getter for Appium session id."""
        return self.get_session_id()

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

        client_config = AppiumClientConfig(
            remote_server_addr=executor, timeout=self.CONNECTION_TIMEOUT
        )

        class SessionAttachmentWebDriver(webdriver.Remote):
            def __init__(
                self,
                command_executor: str,
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
                command_executor=executor,
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

        # Set default options that can be overridden by user config
        default_options = {
            "newCommandTimeout": 3600,
            "ensureWebviewsHavePages": True,
            "nativeWebScreenshot": True,
            "noReset": True,
            "shouldTerminateApp": True,
            "forceAppLaunch": True,
            "connectHardwareKeyboard": True,
        }

        if platform.lower() == self.PLATFORM_ANDROID:
            options = UiAutomator2Options()
            # Add Android-specific defaults
            default_options["ignoreHiddenApiPolicyError"] = True
        elif platform.lower() == self.PLATFORM_IOS:
            options = XCUITestOptions()
        else:
            raise OpticsError(Code.E0104, message=f"Unsupported platform: {platform}. Use 'Android' or 'iOS'.")
        return options, default_options

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

    def get_app_version(self) -> str:
        """Get the version of the application."""
        app_package = self.capabilities.get(self.CAP_APP_PACKAGE_LEGACY) or self.capabilities.get(
            self.CAP_APP_PACKAGE
        )
        if not app_package:
            raise OpticsError(
                Code.E0104,
                message=f"Missing required capability: appPackage or {self.CAP_APP_PACKAGE}",
            )

        command = f"adb shell dumpsys package {app_package} | grep versionName"
        try:
            # Run the adb command and capture the output.
            output = subprocess.check_output(command, shell=False, stderr=subprocess.STDOUT, text=True) # nosec B603
            # Process the output to find the line containing "versionName"
            for line in output.splitlines():
                if self.VERSION_NAME_PREFIX in line:
                    # Extract the version string.
                    return line.split(self.VERSION_NAME_PREFIX)[-1].strip()
        except subprocess.CalledProcessError as e:
            internal_logger.debug(f"Error executing adb command: {e.output}")
            raise OpticsError(Code.E0401, message="Error executing adb command", details=e.output, cause=e) from e
        raise OpticsError(Code.E0401, message=f"Could not find versionName for package: {app_package}")

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
        """Launch the app using the Appium driver."""
        if self.driver is None:
            session_id = self.start_session(
                app_package=app_identifier,
                app_activity=app_activity,
                event_name=event_name,
            )
        internal_logger.debug(f"Launched application with event: {event_name}")
        return session_id if session_id else None


    def launch_other_app(self, app_name: str, event_name: Optional[str] = None) -> None:
        """Launch an app on the Appium-connected device using ADB by fuzzy matching the app name."""
        if self.driver is None:
            self.start_session(event_name=event_name)
        if self.driver:
            self.driver.activate_app(app_name)
            internal_logger.debug(f"Activated app: {app_name} with event: {event_name}")
        else:
            internal_logger.error(self.NOT_INITIALIZED)

    def get_driver(self) -> Optional[WebDriver]:
        """Return the Appium driver instance."""
        return self.driver

    # APPIUM api wrappers
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

    def tap_at_coordinates(self, x: int, y: int, event_name: Optional[str] = None) -> None:
        """
        Simulates a tap gesture at the specified screen coordinates using Appium's `tap` method.
        """
        try:
            driver = self._require_driver()
            timestamp = self.event_sdk.get_current_time_for_events()
            driver.tap([(x, y)], 100)
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
            internal_logger.debug(f"Tapped at coordinates ({x}, {y})")
        except Exception as e:
            internal_logger.debug(f"Failed to tap at ({x}, {y}): {e}")

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
        timestamp = self.event_sdk.get_current_time_for_events()
        try:
            internal_logger.debug(
                f"Swiping from ({x_coor}, {y_coor}) to ({end_x}, {end_y})"
            )
            driver.swipe(x_coor, y_coor, end_x, end_y, 1000)
            if event_name:
                self.event_sdk.capture_event_with_time_input(event_name, timestamp)
        except Exception as e:
            internal_logger.debug(
                f"Failed to swipe from ({x_coor}, {y_coor}) to ({end_x}, {end_y}): {e}"
            )


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
        self.swipe(start_x, start_y, direction, swipe_length, event_name)

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
            start_y = int(height * 0.8)
            end_y = int(height * 0.2)
        elif direction == "down":
            start_x = width // 2
            start_y = int(height * 0.2)
            end_y = int(height * 0.8)
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

    def clear_text_element(self, element: Any, event_name: Optional[str] = None) -> None:
        if event_name:
            self.event_sdk.capture_event(event_name)
        internal_logger.debug(f"Clearing text in element: {element}")
        element.clear()

    def enter_text(self, text: Union[str, SpecialKey], event_name: Optional[str] = None) -> None:
        driver = self._require_driver()
        if event_name:
            self.event_sdk.capture_event(event_name)

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
        internal_logger.debug(f"Pressing keycode for char '{ch}': {keycode}")
        try:
            driver.press_keycode(int(keycode))
        except Exception:
            internal_logger.debug(f"press_keycode failed for '{ch}', falling back to script typing")
            driver.execute_script(
                self.MOBILE_TYPE_COMMAND, {"text": utils.strip_sensitive_prefix(ch)}
            )

    def enter_text_using_keyboard(
        self,
        text: Union[str, SpecialKey],
        event_name: Optional[str] = None
    ) -> None:
        driver = self._require_driver()
        try:
            timestamp = self.event_sdk.get_current_time_for_events()
            if isinstance(text, SpecialKey):
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

        return mapping.get(char.lower())  # handle lowercase input

    def get_text_element(self, element: Any) -> str:
        text = element.get_attribute("text") or element.get_attribute("value")
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
        self.tap_at_coordinates(coor_x, coor_y, event_name)

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
            self.press_coordinates(x, y, event_name)

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
            self.tap_at_coordinates(x_centre, y_centre, event_name)
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
