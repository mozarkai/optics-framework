import os
import shutil
import socket
import subprocess  # nosec B404
import sys
import asyncio
from typing import NoReturn, Optional, Tuple, List, Dict, Set, Any
from urllib.parse import urlparse
import yaml
from pydantic import BaseModel, field_validator
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.common.logging_config import internal_logger, initialize_handlers
from optics_framework.common.runner.data_reader import (
    CSVDataReader,
    YAMLDataReader,
    merge_dicts,
)
from optics_framework.common.session_manager import SessionManager
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.execution import ExecutionEngine, ExecutionParams
from optics_framework.common.models import (
    TestCaseNode,
    ModuleNode,
    KeywordNode,
    ElementData,
    ApiData,
    TestSuite,
    ModuleData,
    TemplateData,
    ErrorDefinitions,
)


def discover_templates(project_path: str) -> TemplateData:
    """
    Discover all image templates in the project directory.

    :param project_path: The path to the project directory.
    :type project_path: str

    :return: TemplateData containing image name to path mappings.
    :rtype: TemplateData
    """
    template_data = TemplateData()
    project_dir = Path(project_path)

    # Common image extensions
    image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif'}

    # Recursively find all image files
    for image_file in project_dir.rglob('*'):
        if image_file.is_file() and image_file.suffix.lower() in image_extensions:
            template_data.add_template(image_file.name, str(image_file))
    return template_data


def find_files(folder_path: str) -> tuple[list[Any], list[Any], list[Any], list[Any], list[Any], Config | None]:
    """
    Recursively search for CSV and YAML files under `folder_path` and categorize them by content.

    Returns lists of discovered test case, module, element, api and error_definitions files
    and an optional Config object (if a suitable YAML config is found).
    """
    file_collections = _initialize_file_collections()
    config_obj: Config | None = None

    # Walk the directory tree so files in subfolders are discovered
    for root, _dirs, files in os.walk(folder_path):
        for fname in files:
            file_path = os.path.join(root, fname)
            lname = fname.lower()

            # Only consider common file extensions
            if lname.endswith((".yml", ".yaml")):
                config_obj = _process_yaml_file(file_path, file_collections, config_obj)
            elif lname.endswith(".csv"):
                _process_csv_file(file_path, file_collections)

    validate_required_files(file_collections["test_case"], file_collections["module"], folder_path)
    return (
        file_collections["test_case"],
        file_collections["module"],
        file_collections["element"],
        file_collections["api"],
        file_collections["error_definitions"],
        config_obj,
    )


def _initialize_file_collections():
    """Initialize collections for different file types."""
    return {
        'test_case': [],
        'module': [],
        'element': [],
        'api': [],
        'error_definitions': [],
    }


def _process_yaml_file(file_path: str, file_collections: dict, current_config: Config | None) -> Config | None:
    """Process a YAML file for config detection and content categorization."""
    config_obj = _try_load_config_from_yaml(file_path, current_config)
    _categorize_file_by_content(file_path, file_collections)
    return config_obj


def _try_load_config_from_yaml(file_path: str, current_config: Config | None) -> Config | None:
    """Attempt to load configuration from YAML file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f) or {}

        if _is_config_file(yaml_data):
            yaml_data = _normalize_element_sources_key(yaml_data)
            return Config(**yaml_data)

        return current_config
    except Exception as e:
        internal_logger.error(f"Failed to load config from {file_path}: {e}")
        return current_config


def _is_config_file(yaml_data: dict) -> bool:
    """Check if YAML data represents a configuration file."""
    return (isinstance(yaml_data, dict) and
            "driver_sources" in yaml_data and
            ("element_sources" in yaml_data or "elements_sources" in yaml_data))


def _normalize_element_sources_key(yaml_data: dict) -> dict:
    """Normalize element_sources key to elements_sources."""
    if "element_sources" in yaml_data and "elements_sources" not in yaml_data:
        yaml_data["elements_sources"] = yaml_data.pop("element_sources")
    return yaml_data


def _process_csv_file(file_path: str, file_collections: dict):
    """Process a CSV file and categorize by content."""
    _categorize_file_by_content(file_path, file_collections)


def _categorize_file_by_content(file_path: str, file_collections: dict):
    """Categorize a file based on its content type."""
    content_type = identify_file_content(file_path)

    if "test_cases" in content_type:
        file_collections['test_case'].append(file_path)
    if "modules" in content_type:
        file_collections['module'].append(file_path)
    if "elements" in content_type:
        file_collections['element'].append(file_path)
    if "api" in content_type:
        file_collections['api'].append(file_path)
    if "error_definitions" in content_type:
        file_collections['error_definitions'].append(file_path)


def _identify_csv_content(headers: Optional[Set[str]]) -> Set[str]:
    """
    Identify content types based on CSV headers.

    :param headers: Set of CSV header names.
    :return: Set of content types ('test_cases', 'modules', 'elements').
    """
    content_types = set()
    if headers:
        if {"test_case", "test_step"}.issubset(headers):
            content_types.add("test_cases")
        if {"module_name", "module_step"}.issubset(headers):
            content_types.add("modules")
        if {"element_name", "element_id"}.issubset(headers):
            content_types.add("elements")
        if {"error_code", "match_string"}.issubset(headers):
            content_types.add("error_definitions")
    return content_types


def _identify_yaml_content(data: Dict) -> Set[str]:
    """
    Identify content types based on YAML keys.

    :param data: Dictionary loaded from YAML file.
    :return: Set of content types ('test_cases', 'modules', 'elements', 'api').
    """
    content_types = set()
    keys = _normalize_yaml_keys(data)

    if any(k in keys for k in ("test cases", "test_cases", "test-cases", "testcases")):
        content_types.add("test_cases")
    if "modules" in keys:
        content_types.add("modules")
    if "elements" in keys:
        content_types.add("elements")
    if any(k in keys for k in ("api", "apis")):
        content_types.add("api")

    return content_types


def _normalize_yaml_keys(data: Dict) -> Set[str]:
    """
    Return a set of normalized (lowercased and stripped) keys from a YAML mapping.

    This centralizes the normalization logic so other functions can reuse it and
    improves readability.
    """
    if not isinstance(data, dict):
        return set()
    return {str(k).strip().lower() for k in data.keys()}


def identify_file_content(file_path: str) -> Set[str]:
    """
    Identify the content type of a file based on its headers (CSV) or keys (YAML).

    :param file_path: Path to the file.
    :return: Set of content types ('test_cases', 'modules', 'elements').
    """
    try:
        if file_path.endswith(".csv"):
            headers = read_csv_headers(file_path)
            return _identify_csv_content(headers)
        else:  # YAML file
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return _identify_yaml_content(data)
    except Exception as e:
        internal_logger.exception(f"Error reading {file_path}: {e}")
        return set()


def read_csv_headers(file_path: str) -> Optional[Set[str]]:
    """
    Read and return the headers of a CSV file as a set.

    :param file_path: Path to the CSV file.
    :return: Set of header names or None if reading fails.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            header = f.readline().strip().split(",")
            return {h.strip().lower() for h in header}
    except (OSError, IOError) as e:
        internal_logger.exception(f"Error reading {file_path}: {e}")
        return None


def validate_required_files(
    test_case_files: List[str], module_files: List[str], folder_path: str
) -> None:
    """
    Validate that required files (test cases and modules) are present; exit if missing.

    :param test_case_files: List of test case file paths.
    :param module_files: List of module file paths.
    :param folder_path: Path to the project folder.
    """
    if not test_case_files or not module_files:
        missing = [
            f
            for f, p in [("test_cases", test_case_files), ("modules", module_files)]
            if not p
        ]
        error_msg = (
            f"Missing required files in {folder_path}: {', '.join(missing)}. "
            "Run `optics init <name>` to scaffold a complete project."
        )
        internal_logger.error(error_msg)
        print(f"Error: {error_msg}", file=sys.stderr)
        sys.exit(1)


def _should_include_test_case(
    name: str, include_set: Set[str], exclude_set: Set[str]
) -> bool:
    """
    Determine if a test case should be included based on include/exclude sets.

    :param name: Test case name (lowercase).
    :param include_set: Set of test case names to include.
    :param exclude_set: Set of test case names to exclude.
    :return: True if the test case should be included, False otherwise.
    """
    if include_set:
        return name in include_set
    if exclude_set:
        return name not in exclude_set
    return True


def filter_test_cases(
    test_cases_dict: Dict[str, Any],
    include: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Filter a dictionary of test cases based on include or exclude list.
    Always include setup or teardown test cases.

    :param test_cases_dict: Dictionary of test case names and their steps.
    :param include: List of test case names to include (case-insensitive).
    :param exclude: List of test case names to exclude (case-insensitive).
    :return: Filtered dictionary with test case names as keys.
    """
    if include and exclude:
        raise OpticsError(Code.E0403, message="Provide either include or exclude list, not both.")

    include_set = {tc.strip().lower() for tc in include} if include else set()
    exclude_set = {tc.strip().lower() for tc in exclude} if exclude else set()
    filtered = {}

    for name, steps in test_cases_dict.items():
        lname = name.lower()
        if (
            "setup" in lname
            or "teardown" in lname
            or _should_include_test_case(lname, include_set, exclude_set)
        ):
            filtered[name] = steps

    return filtered


def categorize_test_cases(
    test_cases_data: Dict[str, Any],
) -> Tuple[
    Optional[Tuple[str, Any]],
    Optional[Tuple[str, Any]],
    Optional[Tuple[str, Any]],
    Optional[Tuple[str, Any]],
    Dict[str, Any],
]:
    """
    Categorize test cases into suite setup, suite teardown, setup, teardown, and regular test cases.

    :param test_cases_data: Dictionary of test case names and their steps.
    :return: Tuple containing suite setup, suite teardown, setup, teardown, and regular test cases.
    """
    suite_setup = None
    suite_teardown = None
    setup = None
    teardown = None
    regular_test_cases = {}

    for name, steps in test_cases_data.items():
        lname = name.lower()
        if "suite" in lname and "setup" in lname:
            suite_setup = (name, steps)
        elif "suite" in lname and "teardown" in lname:
            suite_teardown = (name, steps)
        elif "setup" in lname and "suite" not in lname and not setup:
            setup = (name, steps)
        elif "teardown" in lname and "suite" not in lname and not teardown:
            teardown = (name, steps)
        else:
            regular_test_cases[name] = steps

    return suite_setup, suite_teardown, setup, teardown, regular_test_cases


def get_execution_queue(test_cases_data: Dict) -> Dict:
    """
    Build and return the execution queue including suite-level and per-test setup/teardown.

    :param test_cases_data: Dictionary of all test case names and their steps.
    :return: Ordered dictionary of test execution plan.
    """
    execution_dict: Dict[str, Any] = {}

    # Categorize test cases
    suite_setup, suite_teardown, setup, teardown, regular_test_cases = (
        categorize_test_cases(test_cases_data)
    )

    # Add suite setup if present
    if suite_setup:
        execution_dict[suite_setup[0]] = suite_setup[1]

    for name, steps in regular_test_cases.items():
        if setup:
            execution_dict[setup[0]] = setup[1]
        execution_dict[name] = steps
        if teardown:
            execution_dict[teardown[0]] = teardown[1]

    if suite_teardown:
        execution_dict[suite_teardown[0]] = suite_teardown[1]

    return execution_dict


def create_test_case_nodes(execution_dict: Dict) -> Optional[TestCaseNode]:
    """
    Create a linked list of TestCaseNode objects from the execution dictionary.

    :param execution_dict: Ordered dictionary of test case names and their modules.
    :return: Head of the TestCaseNode linked list, or None if empty.
    """
    test_suite = TestSuite()
    for tc_name in execution_dict:
        tc_node = TestCaseNode(name=tc_name)
        test_suite.add_test_case(tc_node)
    return test_suite.test_cases_head


def populate_module_nodes(
    tc_node: TestCaseNode, modules: List[Any], modules_data: ModuleData
) -> None:
    """
    Populate a TestCaseNode with its ModuleNodes and their KeywordNodes.

    :param tc_node: TestCaseNode to populate.
    :param modules: List of module names for the test case.
    :param modules_data: ModuleData object containing module definitions.
    """
    for module_name in modules:
        module_node = ModuleNode(name=module_name)
        tc_node.add_module(module_node)

        # Get the module definition (list of keywords) from ModuleData
        module_definition = modules_data.get_module_definition(module_name)

        if module_definition:  # Check if the module definition exists
            # Iterate through the keyword definitions in the list
            for keyword_name, keyword_params in module_definition:
                # Create a new KeywordNode for the current test case's module
                keyword_node = KeywordNode(name=keyword_name, params=keyword_params)
                module_node.add_keyword(keyword_node)


def load_api_data(file_path: str) -> ApiData:
    """Loads API data from a YAML file and validates it."""
    if not os.path.exists(file_path):
        raise OpticsError(Code.E0501, message=f"API specification file not found: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
            return ApiData(**data)
        except yaml.YAMLError as e:
            raise OpticsError(Code.E0503, message=f"Error parsing YAML file: {e}", details={"exception": str(e)})
        except Exception as e:
            raise OpticsError(Code.E0503, message=f"Invalid API data structure: {e}", details={"exception": str(e)})


def build_linked_list(
    test_cases_data: Dict[str, Any], modules_data: ModuleData
) -> TestCaseNode:
    """
    Build a nested linked list structure representing the test execution flow.

    :param test_cases_data: Dictionary mapping test case names to a list of module names.
    :param modules_data: Dictionary mapping module names to a list of (keyword, params) tuples.
    :return: Head of the linked list of TestCaseNode objects representing the full execution flow.
    """
    try:
        # Get the ordered execution dict
        execution_dict = get_execution_queue(test_cases_data)

        # Create TestCaseNode linked list
        head = create_test_case_nodes(execution_dict)
        if head is None:
            raise OpticsError(Code.E0702, message="No test cases found to build execution linked list.")

        # Populate modules and keywords for each test case
        current = head
        while current:
            populate_module_nodes(current, execution_dict[current.name], modules_data)
            current = current.next
        return head
    except Exception as e:
        internal_logger.error(f"Error building linked list: {e}")
        raise OpticsError(Code.E0701, message=f"Failed to build linked list: {e}", details={"exception": str(e)})

_PREFLIGHT_SKIP_ENV = "OPTICS_SKIP_PREFLIGHT"
_PREFLIGHT_SOCKET_TIMEOUT_S = 2.0
_PREFLIGHT_ADB_TIMEOUT_S = 5.0
_APPIUM_DEFAULT_URL = "http://127.0.0.1:4723"
_SELENIUM_DEFAULT_URL = "http://127.0.0.1:4444/wd/hub"
_SCHEME_DEFAULT_PORTS = {"http": 80, "https": 443}


def _probe_tcp(url: str | None, default_port: int,
               timeout: float = _PREFLIGHT_SOCKET_TIMEOUT_S) -> bool:
    """
    Check whether something is listening at ``url`` with a short TCP probe.

    Deliberately a plain socket attempt (no HTTP round-trip, no retries): it
    only answers "is a server reachable there?" within ``timeout`` seconds.
    Missing or unparseable URLs count as unreachable so callers can report the
    real fix instead of an opaque driver error minutes into the run.

    ``urlparse`` never fills in a scheme's implied port, so a remote hub given
    as ``https://hub.example.com/wd/hub`` would otherwise be probed on the
    local-server default and reported as down.

    :param url: Server URL from config.yaml (e.g. ``http://127.0.0.1:4723``).
    :param default_port: Port used when the URL carries neither an explicit
                         port nor a scheme whose port is implied.
    :param timeout: Seconds to wait for the connection.
    :return: True when the TCP connection succeeds.
    """
    if not url or not url.strip():
        return False
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = (parsed.port
                or _SCHEME_DEFAULT_PORTS.get(parsed.scheme)
                or default_port)
    except ValueError:
        return False
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _adb_device_count() -> int | None:
    """
    Count attached Android devices/emulators via ``adb devices``.

    Counts only entries in the ``device`` state (offline/unauthorized entries
    cannot be driven) and only after the "List of devices attached" header, so
    daemon banners or error text are never mistaken for devices.

    :return: Device count, or None when adb is missing or cannot be executed —
             letting callers tell "no tooling installed" apart from "no device".
    """
    adb = shutil.which("adb")
    if adb is None:
        return None
    try:
        result = subprocess.run(  # nosec B603
            [adb, "devices"], capture_output=True, text=True,
            timeout=_PREFLIGHT_ADB_TIMEOUT_S, check=False, shell=False)
    except (OSError, subprocess.SubprocessError):
        return None
    in_list = False
    count = 0
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("List of devices attached"):
            in_list = True
            continue
        parts = stripped.split() if in_list and stripped else []
        if len(parts) >= 2 and parts[1] == "device":
            count += 1
    return count if in_list else 0


def _abort_preflight(lines: List[str]) -> NoReturn:
    """Print a friendly rich block explaining the failure and exit non-zero."""
    console = Console(file=sys.stderr)
    console.print(Panel("\n".join(lines), title="Cannot start",
                        border_style="red"))
    sys.exit(1)


def _preflight_or_exit(config: Config | None, folder_path: str = "<folder>") -> None:
    """
    Beginner-facing environment gate for ``optics execute``, run before any
    test executes so failures surface as guidance instead of a fake
    "Launch App | PASS".

    Checks the first enabled driver from the project's config:

    - playwright: self-contained, skipped silently.
    - selenium: probes the configured WebDriver URL (~2s).
    - appium: probes the configured Appium server URL (~2s); when capabilities
      say Android, additionally requires adb on PATH and one attached device.
      iOS projects get the server probe only.

    On failure prints exactly what is wrong and how to fix it, then exits 1
    without executing tests (dry_run never invokes this gate). Callers pass
    ``None`` when no Config is available (e.g. test doubles) and the gate
    quietly steps aside; a no-driver-enabled config is likewise left to the
    existing gate in ``BaseRunner._setup_session``.

    Set the environment variable ``OPTICS_SKIP_PREFLIGHT`` to ``1`` or
    ``true`` to skip the entire preflight (e.g. for CI images where servers
    are known-good or intentionally absent).

    :param config: The project's resolved configuration.
    :param folder_path: Project folder, used in the re-run hint.
    """
    if os.environ.get(_PREFLIGHT_SKIP_ENV, "").strip().lower() in {"1", "true"}:
        return
    enabled = next(
        ((name, details)
         for source in (config.driver_sources if config else [])
         for name, details in source.items()
         if details.enabled),
        None,
    )
    if enabled is None or enabled[0] == "playwright":
        return
    name, details = enabled
    if name == "appium":
        _preflight_appium(details, folder_path)
    elif name == "selenium":
        _preflight_selenium(details, folder_path)


def _preflight_appium(details: DependencyConfig, folder_path: str) -> None:
    """Probe the Appium server and, for Android, adb + an attached device."""
    url = details.url or _APPIUM_DEFAULT_URL
    if not _probe_tcp(url, default_port=4723):
        _abort_preflight([
            f"No Appium server reachable at {url}.",
            "",
            "Start one in another terminal:  appium",
            f"Then re-run:  optics execute {folder_path}",
        ])
    platform = str(details.capabilities.get("platformName", "")).lower()
    if platform != "android":
        return
    device_count = _adb_device_count()
    if device_count is None:
        _abort_preflight([
            "Android run requested, but the adb tool was not found.",
            "",
            "Install Android platform-tools (any option that puts adb on PATH),",
            "then check:  adb devices",
            f"Then re-run:  optics execute {folder_path}",
        ])
    if device_count == 0:
        _abort_preflight([
            "No Android device/emulator attached (adb reports none).",
            "",
            "Connect a device (USB debugging on) or start an emulator,",
            "then check:  adb devices",
            f"Then re-run:  optics execute {folder_path}",
        ])


def _preflight_selenium(details: DependencyConfig, folder_path: str) -> None:
    """Probe the configured Selenium/WebDriver URL."""
    url = details.url or _SELENIUM_DEFAULT_URL
    if not _probe_tcp(url, default_port=4444):
        _abort_preflight([
            f"No Selenium/WebDriver server reachable at {url}.",
            "",
            "Start one, e.g.:  docker run -d -p 4444:4444 selenium/standalone",
            f"Then re-run:  optics execute {folder_path}",
        ])


class RunnerArgs(BaseModel):
    """Arguments for BaseRunner initialization."""

    folder_path: str
    runner: str = "test_runner"
    use_printer: bool = True

    @field_validator("folder_path")
    @classmethod
    def folder_path_must_exist(cls, v: str) -> str:
        """Ensure folder_path is an existing directory."""
        abs_path = os.path.abspath(v)
        if not os.path.isdir(abs_path):
            raise OpticsError(Code.E0501, message=f"Invalid project folder: {abs_path}")
        return abs_path

    @field_validator("runner")
    @classmethod
    def strip_runner(cls, v: str) -> str:
        """Strip whitespace from runner."""
        return v.strip()


class BaseRunner:
    """Base class for running test cases from CSV and YAML files using ExecutionEngine."""

    def __init__(self, args: RunnerArgs):
        self.folder_path = args.folder_path
        self.runner = args.runner
        self.use_printer = args.use_printer
        internal_logger.debug(f"Using runner: {self.runner}")

        (
            test_case_files,
            module_files,
            element_files,
            api_files,
            error_definition_files,
            config_obj,
        ) = find_files(self.folder_path)

        self._init_data_readers()
        self._load_test_cases(test_case_files)
        self._load_modules(module_files)
        self._load_elements(element_files)
        self._load_api_data(api_files)
        self._load_error_definitions(error_definition_files)

        if not self.test_cases_data:
            internal_logger.debug(f"No test cases found in {test_case_files}")

        # Set self.config from config.yaml (or default if missing)
        if config_obj is not None:
            self.config = config_obj
            self.config.project_path = self.folder_path
        else:
            self.config = Config()

        # Load templates after config is set
        self._load_templates()

        # Ensure logging is configured before any test execution
        initialize_handlers(self.config)

        self._filter_and_build_execution_queue()
        self._setup_session()

    def _init_data_readers(self):
        self.csv_reader = CSVDataReader()
        self.yaml_reader = YAMLDataReader()

    def _load_test_cases(self, test_case_files):
        self.test_cases_data: Dict[str, Any] = {}
        for file_path in test_case_files:
            reader = self.csv_reader if file_path.endswith(".csv") else self.yaml_reader
            test_cases = reader.read_test_cases(file_path)
            self.test_cases_data = merge_dicts(
                self.test_cases_data, test_cases, "test_cases"
            )

    def _load_modules(self, module_files):
        self.modules_data: ModuleData = ModuleData()
        for file_path in module_files:
            reader = self.csv_reader if file_path.endswith(".csv") else self.yaml_reader
            modules = reader.read_modules(file_path)
            for name, definition in modules.items():
                if self.modules_data.get_module_definition(name):
                    internal_logger.warning(
                        f"Duplicate modules key '{name}' found. Overwriting."
                    )
                self.modules_data.add_module_definition(name, definition)

    def _load_elements(self, element_files):
        """
        Load element data from the provided element files (CSV or YAML).

        :param element_files: List of element file paths.
        :type element_files: list
        :return: Populates self.elements_data with Dict[str, List[str]] for fallback support.
        """
        self.elements_data: ElementData = ElementData()
        for file_path in element_files:
            reader = self.csv_reader if file_path.endswith(".csv") else self.yaml_reader
            elements = reader.read_elements(file_path)
            for name, values in elements.items():
                self._add_or_merge_element(name, values)

    def _add_or_merge_element(self, name, values):
        """Helper to add or merge element values into self.elements_data."""
        # values is a list of element IDs (for fallback)
        if not isinstance(values, list):
            values = [values]
        if self.elements_data.get_element(name):
            # Merge lists, avoid duplicates
            existing = self.elements_data.get_element(name) or []
            for v in values:
                if v not in existing:
                    self.elements_data.elements[name].append(v)
        else:
            self.elements_data.elements[name] = list(values)

    def _load_api_data(self, api_files):
        self.api_data: ApiData = ApiData()
        for file_path in api_files:
            reader = self.yaml_reader  # API files are expected to be YAML
            self.api_data = reader.read_api_data(file_path, existing_api_data=self.api_data)

    def _load_error_definitions(self, error_definition_files):
        self.error_definitions_data: ErrorDefinitions = ErrorDefinitions()
        for file_path in error_definition_files:
            raw = self.csv_reader.read_error_definitions(file_path)
            for code, meta in raw.items():
                self.error_definitions_data.add_error(code, **meta)

    def _load_templates(self):
        """Load template data by discovering image files in the project directory."""
        self.templates_data: TemplateData = TemplateData()
        if hasattr(self.config, 'project_path') and self.config.project_path:
            self.templates_data = discover_templates(self.config.project_path)



    def _filter_and_build_execution_queue(self):
        if not self.test_cases_data:
            self._exit_no_test_cases()
        included = self.config.get("include")
        excluded = self.config.get("exclude")
        self.filtered_test_cases: Dict[str, Any] = filter_test_cases(
            self.test_cases_data, included, excluded
        )
        self.execution_queue: TestCaseNode = build_linked_list(
            self.filtered_test_cases, self.modules_data
        )

    def _exit_no_test_cases(self) -> None:
        """Exit with actionable guidance when the project has no test cases."""
        message = (
            f"No test cases to run in {self.folder_path}.\n"
            "Your test_cases file has no rows yet — a freshly created project "
            "starts empty.\n"
            "Add a test case (a row in test_cases/test_cases.csv naming a module) "
            "and define that module in modules/modules.csv, or start from a "
            "ready-made example:\n"
            "    optics init <name> --template <sample>   (see `optics init --help`)\n"
            f"Then re-run:  optics dry_run {self.folder_path}"
        )
        internal_logger.error("No test cases found in %s", self.folder_path)
        print(message, file=sys.stderr)
        sys.exit(1)

    def _setup_session(self):
        if not self._has_enabled_driver():
            config_path = os.path.join(self.folder_path, "config.yaml")
            print(
                f"No driver enabled in {config_path}. "
                f"Run `optics configure {self.folder_path}`, then re-run."
            )
            sys.exit(1)
        self.manager: SessionManager = SessionManager()
        self.session_id: str = self.manager.create_session(
            self.config,
            self.execution_queue,
            self.modules_data,
            self.elements_data,
            self.api_data,
            self.templates_data,
            self.error_definitions_data,
        )
        self.engine: ExecutionEngine = ExecutionEngine(self.manager)

    def _has_enabled_driver(self) -> bool:
        return any(
            details.enabled
            for source in self.config.driver_sources
            for details in source.values()
        )

    async def run(self, mode: str):
        """Run the specified mode using ExecutionEngine."""
        try:
            params = ExecutionParams(
                session_id=self.session_id,
                mode=mode,
                runner_type=self.runner,
                use_printer=self.use_printer,
            )
            internal_logger.debug(
                f"Executing with runner_type: {self.runner}, use_printer: {self.use_printer}"
            )
            return await self.engine.execute(params)
        except Exception as e:
            internal_logger.error(f"{mode.capitalize()} failed: {e}")
            raise
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up session resources."""
        try:
            self.manager.terminate_session(self.session_id)
        except Exception as e:
            internal_logger.error(f"Failed to terminate session {self.session_id}: {e}")


class ExecuteRunner(BaseRunner):
    async def execute(self):
        """Execute test cases."""
        return await self.run("batch")


class DryRunRunner(BaseRunner):
    async def execute(self):
        """Perform dry run of test cases."""
        return await self.run("dry_run")


def _has_failed_results(results: Any) -> bool:
    return isinstance(results, dict) and any(
        getattr(tc, "status", None) != "PASS" for tc in results.values()
    )


def execute_main(
    folder_path: str, runner: str = "test_runner", use_printer: bool = True
):
    """Entry point for execute command."""
    args = RunnerArgs(folder_path=folder_path, runner=runner, use_printer=use_printer)
    runner_instance = ExecuteRunner(args)
    _preflight_or_exit(getattr(runner_instance, "config", None), folder_path)
    results = asyncio.run(runner_instance.execute())
    if _has_failed_results(results):
        sys.exit(1)


def dryrun_main(
    folder_path: str, runner: str = "test_runner", use_printer: bool = True
):
    """Entry point for dry run command."""
    args = RunnerArgs(folder_path=folder_path, runner=runner, use_printer=use_printer)
    runner_instance = DryRunRunner(args)
    results = asyncio.run(runner_instance.execute())
    if _has_failed_results(results):
        sys.exit(1)
