from typing import Optional, Dict, List, Any, Callable, TypeVar, cast
from functools import wraps
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.config_handler import ConfigHandler, DependencyConfig
from optics_framework.common.session_manager import SessionManager
from optics_framework.api.app_management import AppManagement
from optics_framework.api.action_keyword import ActionKeyword
from optics_framework.api.verifier import Verifier
from optics_framework.common.optics_builder import OpticsBuilder


T = TypeVar("T", bound=Callable[..., Any])

try:
    from robot.api.deco import keyword, library # type: ignore
except ImportError:

    def keyword(name: Optional[str] = None) -> Callable[[T], T]:
        def decorator(func: T) -> T:
            @wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return func(*args, **kwargs)

            return cast(T, wrapper)
        _ = name
        return decorator


    def library(scope: Optional[str] = None) -> Callable[[T], T]:
        def decorator(cls: T) -> T:
            return cls

        _ = scope
        return decorator
INVALID_SETUP = "Setup not complete. Call setup() first."


@library(scope="GLOBAL")
class Optics:
    """
    A lightweight interface to interact with the Optics Framework.
    Provides direct access to app management, action, and verification keywords with a single setup
    method.
    Supports Robot Framework as a library when Robot Framework is installed.
    """

    def __init__(self):
        """
        Initialize the Optics instance. Call setup() to configure the driver and sources.
        """
        self.config_handler = ConfigHandler.get_instance()
        self.session_manager = SessionManager()
        self.config = self.config_handler.config
        self.builder = OpticsBuilder()
        self.app_management: Optional[AppManagement] = None
        self.action_keyword: Optional[ActionKeyword] = None
        self.verifier: Optional[Verifier] = None
        self.session_id: Optional[str] = None

    def _create_dependency_config(
        self, config_dict: Dict[str, Any]
    ) -> DependencyConfig:
        """
        Convert a dictionary to a DependencyConfig instance.

        Args:
            config_dict: Dictionary containing configuration (enabled, url, capabilities).

        Returns:
            DependencyConfig: Validated configuration object.
        """
        return DependencyConfig(
            enabled=config_dict.get("enabled", True),
            url=config_dict.get("url"),
            capabilities=config_dict.get("capabilities", {}),
        )

    def _process_config_list(
        self, config_list: List[Dict[str, Any]]
    ) -> List[Dict[str, DependencyConfig]]:
        """
        Convert a list of configuration dictionaries to a list of DependencyConfig dictionaries.

        Args:
            config_list: List of configuration dictionaries.

        Returns:
            List of dictionaries with DependencyConfig values.
        """
        return [
            {key: self._create_dependency_config(value)}
            for item in config_list
            for key, value in item.items()
        ]

    @keyword("Setup")
    def setup(
        self,
        driver_config: List[Dict[str, Dict[str, Any]]],
        element_source_config: List[Dict[str, Dict[str, Any]]],
        image_config: Optional[List[Dict[str, Dict[str, Any]]]] = None,
        text_config: Optional[List[Dict[str, Dict[str, Any]]]] = None,
        execution_output_path: Optional[str] = None,
    ) -> None:
        """
        Configure the Optics Framework with required driver and element source settings.

        Args:
            driver_config: List of driver configurations (e.g., [{"appium": {"enabled": True, "url": "...", "capabilities": {...}}}]).
            element_source_config: List of element source configurations.
            image_config: Optional list of image detection configurations.
            text_config: Optional list of text detection configurations.

        Raises:
            ValueError: If configuration or session creation fails.
        """
        # Convert user-provided dictionaries to DependencyConfig
        driver_deps = self._process_config_list(driver_config)
        element_deps = self._process_config_list(element_source_config)
        image_deps = self._process_config_list(image_config) if image_config else []
        text_deps = self._process_config_list(text_config) if text_config else []

        # Update ConfigHandler
        self.config_handler.load()
        self.config_handler.config.driver_sources = driver_deps
        self.config_handler.config.elements_sources = element_deps
        self.config_handler.config.image_detection = image_deps
        self.config_handler.config.text_detection = text_deps
        if execution_output_path:
            self.config_handler.config.execution_output_path = execution_output_path


        # # Initialize session
        try:
            self.session_id = self.session_manager.create_session(
                self.config_handler.config
            )
        except Exception as e:
            internal_logger.error(f"Failed to create session: {e}")
            raise ValueError(f"Failed to create session: {e}") from e

        self.action_keyword = self.session_manager.sessions[
            self.session_id
        ].optics.build(ActionKeyword)
        self.app_management = self.session_manager.sessions[
            self.session_id
        ].optics.build(AppManagement)
        self.verifier = self.session_manager.sessions[self.session_id].optics.build(
            Verifier
        )

    ### AppManagement Methods ###
    @keyword("Launch App")
    def launch_app(self, event_name: Optional[str] = None) -> None:
        """Launch the application."""
        if not self.app_management:
            raise ValueError(INVALID_SETUP)
        self.app_management.launch_app(event_name)

    @keyword("Launch Other App")
    def launch_other_app(self, bundleid: str) -> None:
        """Launch another application."""
        if not self.app_management:
            raise ValueError(INVALID_SETUP)
        self.app_management.launch_other_app(bundleid)

    @keyword("Start Appium Session")
    def start_appium_session(self, event_name: Optional[str] = None) -> None:
        """Start an Appium session."""
        if not self.app_management:
            raise ValueError(INVALID_SETUP)
        self.app_management.start_appium_session(event_name)

    @keyword("Close and Terminate App")
    def close_and_terminate_app(self) -> None:
        """Close and terminate an application."""
        if not self.app_management:
            raise ValueError(INVALID_SETUP)
        self.app_management.close_and_terminate_app()

    @keyword("Get App Version")
    def get_app_version(self) -> Optional[str]:
        """Get the application version."""
        if not self.app_management:
            raise ValueError(INVALID_SETUP)
        return self.app_management.get_app_version()

    ### ActionKeyword Methods ###
    @keyword("Press Element")
    def press_element(
        self,
        element: str,
        repeat: int = 1,
        offset_x: int = 0,
        offset_y: int = 0,
        event_name: Optional[str] = None,
    ) -> None:
        """Press an element with specified parameters."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.press_element(
            element, repeat, offset_x, offset_y, event_name
        )

    @keyword("Press By Percentage")
    def press_by_percentage(
        self,
        percent_x: int,
        percent_y: int,
        repeat: int = 1,
        event_name: Optional[str] = None,
    ) -> None:
        """Press at percentage coordinates."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.press_by_percentage(
            percent_x, percent_y, repeat, event_name
        )

    @keyword("Press By Coordinates")
    def press_by_coordinates(
        self,
        coor_x: int,
        coor_y: int,
        repeat: int = 1,
        event_name: Optional[str] = None,
    ) -> None:
        """Press at absolute coordinates."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.press_by_coordinates(coor_x, coor_y, repeat, event_name)

    @keyword("Press Element With Index")
    def press_element_with_index(
        self, element: str, index: int = 0, event_name: Optional[str] = None
    ) -> None:
        """Press an element at a specific index."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.press_element_with_index(element, index, event_name)

    @keyword("Detect and Press")
    def detect_and_press(
        self, element: str, timeout: int = 10, event_name: Optional[str] = None
    ) -> None:
        """Detect and press an element."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.detect_and_press(element, timeout, event_name)

    @keyword("Swipe")
    def swipe(
        self,
        coor_x: int,
        coor_y: int,
        direction: str = "right",
        swipe_length: int = 50,
        event_name: Optional[str] = None,
    ) -> None:
        """Perform a swipe gesture."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.swipe(coor_x, coor_y, direction, swipe_length, event_name)

    @keyword("Swipe Until Element Appears")
    def swipe_until_element_appears(
        self,
        element: str,
        direction: str = "down",
        timeout: int = 30,
        event_name: Optional[str] = None,
    ) -> None:
        """Swipe until an element appears."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.swipe_until_element_appears(
            element, direction, timeout, event_name
        )

    @keyword("Swipe From Element")
    def swipe_from_element(
        self,
        element: str,
        direction: str = "right",
        swipe_length: int = 50,
        event_name: Optional[str] = None,
    ) -> None:
        """Swipe starting from an element."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.swipe_from_element(
            element, direction, swipe_length, event_name
        )

    @keyword("Scroll")
    def scroll(self, direction: str = "down", event_name: Optional[str] = None) -> None:
        """Perform a scroll gesture."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.scroll(direction, event_name)

    @keyword("Scroll Until Element Appears")
    def scroll_until_element_appears(
        self,
        element: str,
        direction: str = "down",
        timeout: int = 30,
        event_name: Optional[str] = None,
    ) -> None:
        """Scroll until an element appears."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.scroll_until_element_appears(
            element, direction, timeout, event_name
        )

    @keyword("Scroll From Element")
    def scroll_from_element(
        self,
        element: str,
        direction: str = "down",
        scroll_length: int = 100,
        event_name: Optional[str] = None,
    ) -> None:
        """Scroll starting from an element."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.scroll_from_element(
            element, direction, scroll_length, event_name
        )

    @keyword("Enter Text")
    def enter_text(
        self, element: str, text: str, event_name: Optional[str] = None
    ) -> None:
        """Enter text into an element."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.enter_text(element, text, event_name)

    @keyword("Enter Text Direct")
    def enter_text_direct(self, text: str, event_name: Optional[str] = None) -> None:
        """Enter text using the keyboard."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.enter_text_direct(text, event_name)

    @keyword("Enter Text Using Keyboard")
    def enter_text_using_keyboard(
        self, text_input: str, event_name: Optional[str] = None
    ) -> None:
        """Enter text or press a special key."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.enter_text_using_keyboard(text_input, event_name)

    @keyword("Enter Number")
    def enter_number(
        self, element: str, number: float, event_name: Optional[str] = None
    ) -> None:
        """Enter a number into an element."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.enter_number(element, number, event_name)

    @keyword("Press Keycode")
    def press_keycode(self, keycode: int, event_name: Optional[str] = None) -> None:
        """Press a keycode."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.press_keycode(
            keycode, event_name if event_name is not None else ""
        )

    @keyword("Clear Element Text")
    def clear_element_text(
        self, element: str, event_name: Optional[str] = None
    ) -> None:
        """Clear text from an element."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.clear_element_text(element, event_name)

    @keyword("Get Text")
    def get_text(self, element: str) -> Optional[str]:
        """Get text from an element."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        return self.action_keyword.get_text(element)

    @keyword("Sleep")
    def sleep(self, duration: int) -> None:
        """Sleep for a specified duration."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.sleep(duration)

    ### Verifier Methods ###
    @keyword("Validate Element")
    def validate_element(
        self,
        element: str,
        timeout: int = 10,
        rule: str = "all",
        event_name: Optional[str] = None,
    ) -> None:
        """Validate an element's presence."""
        if not self.verifier:
            raise ValueError(INVALID_SETUP)
        self.verifier.validate_element(element, timeout, rule, event_name)

    @keyword("Assert Presence")
    def assert_presence(
        self,
        elements: str,
        timeout: int = 30,
        rule: str = "any",
        event_name: Optional[str] = None,
    ) -> bool:
        """Assert the presence of elements."""
        if not self.verifier:
            raise ValueError(INVALID_SETUP)
        return self.verifier.assert_presence(elements, timeout, rule, event_name)

    @keyword("Validate Screen")
    def validate_screen(
        self,
        elements: str,
        timeout: int = 30,
        rule: str = "any",
        event_name: Optional[str] = None,
    ) -> None:
        """Validate a screen by checking element presence."""
        if not self.verifier:
            raise ValueError(INVALID_SETUP)
        self.verifier.validate_screen(elements, timeout, rule, event_name)

    @keyword("Get Interactive Elements")
    def get_interactive_elements(self) -> List:
        """Get interactive elements on the screen."""
        if not self.verifier:
            raise ValueError(INVALID_SETUP)
        return self.verifier.get_interactive_elements()

    @keyword("Capture Screenshot")
    def capture_screenshot(self):
        """Capture a screenshot of the current screen."""
        if not self.verifier:
            raise ValueError(INVALID_SETUP)
        return self.verifier.capture_screenshot()

    @keyword("Capture Page Source")
    def capture_pagesource(self) -> str:
        """Capture the page source of the current screen."""
        if not self.verifier:
            raise ValueError(INVALID_SETUP)
        return self.verifier.capture_pagesource()

    @keyword("Quit")
    def quit(self) -> None:
        """Clean up session resources and terminate the session."""
        if self.session_id:
            try:
                self.session_manager.terminate_session(self.session_id)
                self.session_id = None
                self.app_management = None
                self.action_keyword = None
                self.verifier = None
            except Exception as e:
                internal_logger.error(
                    f"Failed to terminate session {self.session_id}: {e}"
                )

    def __enter__(self) -> "Optics":
        """Support for context manager to ensure cleanup."""
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_value: Optional[Exception],
        traceback: Optional[object],
    ) -> None:
        """Clean up on context exit."""
        self.quit()


if __name__ == "__main__":
    # Element dictionary from elements.csv
    ELEMENTS = {
        "driver": "Appium",
        "PLATFORM_NAME": "Android",
        "APP_PACKAGE": "com.google.android.contacts",
        "AUTOMATION_NAME": "UiAutomator2",
        "Add_Contact_Button": '//com.google.android.material.floatingactionbutton.FloatingActionButton[@content-desc="Create contact"]',
        "Add_Contact_Page": '//android.widget.TextView[@text="Create contact"]',
        "First_Name_element": '//android.widget.EditText[@text="First name"]',
        "Last_Name_element": '//android.widget.EditText[@text="Last name"]',
        "Phone_element": '//android.widget.EditText[@text="+1"]',
        "Company_element": "//androidx.compose.ui.platform.ComposeView/android.view.View/android.view.View/android.view.View[1]/android.view.View/android.view.View/android.view.View[4]/android.widget.EditText",
        "Save_Button": "//androidx.compose.ui.platform.ComposeView/android.view.View/android.view.View/android.view.View[2]/android.view.View[2]/android.widget.Button",
        "First_Name": "John",
        "Last_Name": "Doe",
        "Phone": "1234567890",
        "Company": "Mozark",
        "Contact_Name": '//android.widget.TextView[@resource-id="com.google.android.contacts:id/title" and @text="John Doe"]',
    }

    # Configuration from config.yaml (updated to use dictionaries instead of DependencyConfig)
    CONFIG = {
        "driver_config": [
            {
                "appium": {
                    "enabled": True,
                    "url": "http://localhost:4723",
                    "capabilities": {
                        "appActivity": "com.android.contacts.activities.PeopleActivity",
                        "appPackage": "com.google.android.contacts",
                        "automationName": "UiAutomator2",
                        "deviceName": "emulator-5554",
                        "platformName": "Android",
                    },
                }
            },
        ],
        "element_source_config": [
            {"appium_find_element": {"enabled": True, "url": None, "capabilities": {}}},
            {"appium_page_source": {"enabled": True, "url": None, "capabilities": {}}},
            {"appium_screenshot": {"enabled": True, "url": None, "capabilities": {}}},
        ],
        "text_detection": [
            {"easyocr": {"enabled": False, "url": None, "capabilities": {}}},
        ],
        "image_detection": [
            {"templatematch": {"enabled": False, "url": None, "capabilities": {}}}
        ],
    }

    def launch_contact_application(optics: Optics) -> None:
        """Launch Contact Application module."""
        optics.launch_app()

    def navigate_to_add_contact_page(optics: Optics) -> None:
        """Navigate to Add Contact page."""
        optics.assert_presence(
            ELEMENTS["Add_Contact_Button"],
            timeout=10,
        )
        optics.press_element(ELEMENTS["Add_Contact_Button"])
        optics.assert_presence(
            ELEMENTS["Add_Contact_Page"],
            timeout=10,
        )

    def enter_contact_details(optics: Optics) -> None:
        """Enter contact details in the contact form."""
        optics.enter_text(
            element=ELEMENTS["First_Name_element"],
            text=ELEMENTS["First_Name"],
        )
        optics.enter_text(
            element=ELEMENTS["Last_Name_element"],
            text=ELEMENTS["Last_Name"],
        )
        optics.enter_text(
            element=ELEMENTS["Phone_element"],
            text=ELEMENTS["Phone"],
        )
        optics.enter_text(
            element=ELEMENTS["Company_element"],
            text=ELEMENTS["Company"],
        )

    def click_save_button(optics: Optics) -> None:
        """Click the save button."""
        optics.press_element(
            ELEMENTS["Save_Button"],
        )

    def verify_contact_added(optics: Optics) -> None:
        """Verify the contact was added."""
        optics.assert_presence(ELEMENTS["Contact_Name"], timeout=10)

    def add_contact_with_contact_app(optics: Optics) -> None:
        """Test case: Add contact with Contact app."""
        launch_contact_application(optics)
        navigate_to_add_contact_page(optics)
        enter_contact_details(optics)
        click_save_button(optics)
        verify_contact_added(optics)

    if __name__ == "__main__":
        # Initialize Optics
        optics = Optics()

        try:
            # Setup configuration
            optics.setup(
                driver_config=CONFIG["driver_config"],
                element_source_config=CONFIG["element_source_config"],
                image_config=CONFIG["image_detection"],
                text_config=CONFIG["text_detection"],
            )

            # Run test case
            add_contact_with_contact_app(optics)

        finally:
            # Cleanup
            optics.quit()
