from typing import Optional, Dict, List, Any, Callable, TypeVar, cast, Union
from functools import wraps
import json
import yaml
import os
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.config_handler import ConfigHandler, DependencyConfig, Config
from optics_framework.common.session_manager import SessionManager
from optics_framework.api.app_management import AppManagement
from optics_framework.api.action_keyword import ActionKeyword
from optics_framework.api.verifier import Verifier
from optics_framework.api.flow_control import FlowControl
from optics_framework.common.runner.keyword_register import KeywordRegistry
from optics_framework.common.models import (
    TestCaseNode,
    ModuleData,
    ElementData,
    ApiData,
)

T = TypeVar("T", bound=Callable[..., Any])

try:
    from robot.api.deco import keyword, library  # type: ignore
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

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the Optics instance. Call setup() to configure the driver and sources.
        """
        self.config_handler: Optional[ConfigHandler] = None
        self.session_manager = SessionManager()
        self.config = None
        self.app_management = None
        self.action_keyword = None
        self.verifier = None
        self.session_id = None
        self.flow_control = None
        if config is not None:
            self.setup(config)

    def _parse_config_string(self, config_string: str) -> Dict[str, Any]:
        """
        Parse a JSON or YAML configuration string.
        """
        config_string = config_string.strip()
        try:
            if config_string.startswith("{") or config_string.startswith("["):
                return json.loads(config_string)
            else:
                return yaml.safe_load(config_string)
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            raise OpticsError(Code.E0503, message=f"Invalid configuration format: {e}", details={"exception": str(e)})

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
        config: Union[str, Dict[str, Any], None] = None,
        driver_sources: Optional[List[Dict[str, Dict[str, Any]]]] = None,
        elements_sources: Optional[List[Dict[str, Dict[str, Any]]]] = None,
        image_detection: Optional[List[Dict[str, Dict[str, Any]]]] = None,
        text_detection: Optional[List[Dict[str, Dict[str, Any]]]] = None,
        execution_output_path_param: Optional[str] = None,
    ) -> None:
        """
        Configure the Optics Framework with required driver and element source settings.
        """
        config_data = self._extract_config_data(
            config,
            driver_sources,
            elements_sources,
            image_detection,
            text_detection,
            execution_output_path_param,
        )
        config_obj = Config(**{k: v for k, v in config_data.items() if v is not None})
        self.config_handler = ConfigHandler(config_obj)
        self.config = config_obj
        self._initialize_session_and_keywords()

    def _extract_config_data(
        self,
        config: Union[str, Dict[str, Any], None],
        driver_sources: Optional[List[Dict[str, Dict[str, Any]]]],
        elements_sources: Optional[List[Dict[str, Dict[str, Any]]]],
        image_detection: Optional[List[Dict[str, Dict[str, Any]]]],
        text_detection: Optional[List[Dict[str, Dict[str, Any]]]],
        execution_output_path_param: Optional[str],
    ) -> Dict[str, Any]:
        """
        Extract and validate configuration data from parameters.
        """
        project_path = None
        execution_output_path = execution_output_path_param
        event_attributes_json = None
        _driver_config = driver_sources
        _element_source_config = elements_sources
        _image_config = image_detection
        _text_config = text_detection

        if config is not None:
            if isinstance(config, str):
                parsed_config = self._parse_config_string(config)
            elif isinstance(config, dict):
                parsed_config = config
            else:
                raise OpticsError(Code.E0503, message="Config must be a string (JSON/YAML) or dictionary")

            self._validate_required_keys(parsed_config)

            _driver_config = parsed_config["driver_sources"]
            _element_source_config = parsed_config["elements_sources"]
            _image_config = parsed_config.get("image_detection")
            _text_config = parsed_config.get("text_detection")
            project_path = parsed_config.get("project_path")
            execution_output_path = parsed_config.get(
                "execution_output_path", execution_output_path_param
            )
            event_attributes_json = parsed_config.get("event_attributes_json")
        elif _driver_config is not None and _element_source_config is not None:
            internal_logger.warning(
                "Using deprecated parameter format. Consider migrating to the new config parameter."
            )
        else:
            raise ValueError(
                "Either 'config' parameter or legacy parameters ('driver_sources' and 'elements_sources') must be provided"
            )

        return {
            "driver_sources": _driver_config or [],
            "elements_sources": _element_source_config or [],
            "image_detection": _image_config or [],
            "text_detection": _text_config or [],
            "project_path": project_path,
            "execution_output_path": execution_output_path,
            "event_attributes_json": event_attributes_json,
        }


    def _initialize_session_and_keywords(self) -> None:
        """
        Initialize session and register keywords.
        """
        if self.config is None:
            raise ValueError("Optics config is not set. Call setup() with a valid config before creating a session.")
        try:
            self.session_id = self.session_manager.create_session(
                self.config,
                test_cases=TestCaseNode(name="default"),
                modules=ModuleData(),
                elements=ElementData(),
                apis=ApiData(),
            )
        except Exception as e:
            internal_logger.error(f"Failed to create session: {e}")
            raise ValueError(f"Failed to create session: {e}") from e

        session = self.session_manager.sessions[self.session_id]
        registry = KeywordRegistry()
        self.action_keyword = session.optics.build(ActionKeyword)
        self.app_management = session.optics.build(AppManagement)
        self.verifier = session.optics.build(Verifier)

        registry.register(self.action_keyword)
        registry.register(self.app_management)
        registry.register(self.verifier)
        self.flow_control = FlowControl(
            session=session, keyword_map=registry.keyword_map
        )
        registry.register(self.flow_control)

    @keyword("Setup From File")
    def setup_from_file(self, config_file_path: str) -> None:
        """
        Configure the Optics Framework from a JSON or YAML configuration file.

        Args:
            config_file_path: Path to the configuration file (JSON or YAML).

        Raises:
            ValueError: If the file cannot be read or parsed.
            FileNotFoundError: If the configuration file doesn't exist.
        """
        try:
            with open(config_file_path, "r", encoding="utf-8") as file:
                config_content = file.read()

            # Parse the configuration directly
            config = self._parse_config_string(config_content)

            # Validate structure
            self._validate_required_keys(config)

            # Call existing setup method with extracted parameters
            self.setup(
                driver_sources=config["driver_sources"],
                elements_sources=config["elements_sources"],
                image_detection=config.get("image_detection"),
                text_detection=config.get("text_detection"),
                execution_output_path_param=config.get("execution_output_path"),
            )

        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Configuration file not found: {config_file_path}"
            ) from exc
        except Exception as e:
            raise ValueError(
                f"Failed to read configuration file {config_file_path}: {e}"
            ) from e

    def _validate_required_keys(self, config: Dict[str, Any]) -> None:
        """
        Validate required configuration keys.

        Args:
            config: Configuration dictionary to validate.

        Raises:
            ValueError: If required keys are missing or invalid.
        """
        required_keys = ["driver_sources", "elements_sources"]

        for key in required_keys:
            if key not in config:
                raise ValueError(f"Missing required configuration key: {key}")

            if not isinstance(config[key], list):
                raise ValueError(f"Configuration key '{key}' must be a list")

    @keyword("Add Element")
    def add_element(self, name: str, value: Any) -> None:
        """Add or update an element in the current session."""
        if not self.session_id or self.session_id not in self.session_manager.sessions:
            raise ValueError(INVALID_SETUP)
        session = self.session_manager.sessions[self.session_id]
        if hasattr(session, "elements") and session.elements:
            session.elements.add_element(name, value)
        else:
            raise ValueError("Session does not have an elements store.")

    @keyword("Get Element Value")
    def get_element_value(self, name: str) -> Any:
        """Get the value of an element by name from the current session."""
        if not self.session_id or self.session_id not in self.session_manager.sessions:
            raise ValueError(INVALID_SETUP)
        session = self.session_manager.sessions[self.session_id]
        if hasattr(session, "elements") and session.elements:
            return session.elements.get_element(name)
        else:
            raise ValueError(f"Session does not have an element '{name}' stored.")

    @keyword("Add API")
    def add_api(self, api_data: Union[str, dict]) -> None:
        """Add or update an API definition in the current session by fully replacing session.apis."""
        if not self.session_id or self.session_id not in self.session_manager.sessions:
            raise ValueError(INVALID_SETUP)
        session = self.session_manager.sessions[self.session_id]
        if isinstance(api_data, str):
            if not os.path.isabs(api_data):
                project_path = getattr(self.config, "project_path", None)
                if project_path:
                    api_data = os.path.join(project_path, api_data)
            if not os.path.exists(api_data):
                raise FileNotFoundError(f"API YAML file not found: {api_data}")
            with open(api_data, "r", encoding="utf-8") as f:
                try:
                    data = yaml.safe_load(f)
                except yaml.YAMLError as e:
                    raise ValueError(f"Failed to parse API YAML file: {e}") from e
                api_data_content = data.get("api", data)
        elif isinstance(api_data, dict):
            api_data_content = api_data.get("api", api_data)
        else:
            raise ValueError("api_data must be a file path or a dictionary")
        session.apis = ApiData(**api_data_content)

    @keyword("Add Testcase")
    def add_testcase(self, testcase: Any) -> None:
        """Add or update a testcase in the current session."""
        if not self.session_id or self.session_id not in self.session_manager.sessions:
            raise ValueError(INVALID_SETUP)
        session = self.session_manager.sessions[self.session_id]
        if hasattr(session, "test_cases"):
            session.test_cases = testcase
        else:
            raise ValueError("Session does not have a test_cases store.")

    @keyword("Add Module")
    def add_module(self, module_name: str, module_def: Any) -> None:
        """Add or update a module in the current session."""
        if not self.session_id or self.session_id not in self.session_manager.sessions:
            raise OpticsError(Code.E0101, message=INVALID_SETUP)
        session = self.session_manager.sessions[self.session_id]
        if hasattr(session, "modules") and session.modules:
            session.modules.modules[module_name] = module_def
        else:
            raise ValueError("Session does not have a modules store.")

    ### AppManagement Methods ###
    @keyword("Launch App")
    def launch_app(
        self,
        app_identifier: Optional[str] = None,
        app_activity: Optional[str] = None,
        event_name: Optional[str] = None,
    ) -> None:
        """Launch the application."""
        if not self.app_management:
            raise ValueError(INVALID_SETUP)
        self.app_management.launch_app(
            app_identifier=app_identifier,
            app_activity=app_activity,
            event_name=event_name,
        )

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

    @keyword("Force Terminate App")
    def force_terminate_app(
        self, app_name: str, event_name: Optional[str] = None
    ) -> None:
        """
        Forcefully terminate the specified application.

        :param app_name: The name of the application to terminate.
        :param event_name: The event triggering the forced termination, if any.
        """
        if not self.app_management:
            raise ValueError(INVALID_SETUP)
        self.app_management.force_terminate_app(app_name, event_name)

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
        repeat: str = "1",
        offset_x: str = "0",
        offset_y: str = "0",
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
        percent_x: str,
        percent_y: str,
        repeat: str = "1",
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
        coor_x: str,
        coor_y: str,
        repeat: str = "1",
        event_name: Optional[str] = None,
    ) -> None:
        """Press at absolute coordinates."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.press_by_coordinates(coor_x, coor_y, repeat, event_name)

    @keyword("Press Element With Index")
    def press_element_with_index(
        self, element: str, index: str = "0", event_name: Optional[str] = None
    ) -> None:
        """Press an element at a specific index."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.press_element_with_index(element, index, event_name)

    @keyword("Detect and Press")
    def detect_and_press(
        self, element: str, timeout: str = "10", event_name: Optional[str] = None
    ) -> None:
        """Detect and press an element."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.detect_and_press(element, timeout, event_name)

    @keyword("Swipe")
    def swipe(
        self,
        coor_x: str,
        coor_y: str,
        direction: str = "right",
        swipe_length: str = "50",
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
        timeout: str = "30",
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
        swipe_length: str = "50",
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
        timeout: str = "30",
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
        self, element: str, number: str, event_name: Optional[str] = None
    ) -> None:
        """Enter a number into an element."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.enter_number(element, number, event_name)

    @keyword("Press Keycode")
    def press_keycode(self, keycode: str, event_name: Optional[str] = None) -> None:
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
    def sleep(self, duration: str) -> None:
        """Sleep for a specified duration."""
        if not self.action_keyword:
            raise ValueError(INVALID_SETUP)
        self.action_keyword.sleep(duration)

    ### Verifier Methods ###
    @keyword("Validate Element")
    def validate_element(
        self,
        element: str,
        timeout: str = "10",
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
        timeout: str = "30",
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
        timeout: str = "30",
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

    @keyword("Invoke API")
    def invoke_api(self, api) -> Any:
        """Invoke a REST API endpoint."""
        if not self.flow_control:
            raise ValueError(INVALID_SETUP)
        return self.flow_control.invoke_api(api)

    @keyword("Read Data")
    def read_data(self, element: str, source: str, query: str = "") -> Any:
        """Read data from a specified source."""
        if not self.flow_control:
            raise ValueError(INVALID_SETUP)
        return self.flow_control.read_data(element, source, query)

    @keyword("Run Loop")
    def run_loop(self, target: str, *args: str) -> Any:
        """Run a loop over a target module, by count or with variables."""
        if not self.flow_control:
            raise ValueError(INVALID_SETUP)
        return self.flow_control.run_loop(target, *args)

    @keyword("Condition")
    def condition(self, *args: str) -> Any:
        """Evaluate conditions and execute corresponding targets."""
        if not self.flow_control:
            raise ValueError(INVALID_SETUP)
        return self.flow_control.condition(*args)

    @keyword("Evaluate")
    def evaluate(self, param1: str, param2: str) -> Any:
        """Evaluate an expression and store the result in session elements."""
        if not self.flow_control:
            raise ValueError(INVALID_SETUP)
        return self.flow_control.evaluate(param1, param2)

    @keyword("Date Evaluate")
    def date_evaluate(
        self, param1: str, param2: str, param3: str, param4: str = "%d %B"
    ) -> str:
        """Evaluate a date expression and store the result in session elements."""
        if not self.flow_control:
            raise ValueError(INVALID_SETUP)
        return self.flow_control.date_evaluate(param1, param2, param3, param4)

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
                self.flow_control = None
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
