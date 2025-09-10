import os
import sys
import asyncio
from typing import Optional, Tuple, List, Dict, Set, Any
import yaml
from pydantic import BaseModel, field_validator
from pathlib import Path
from optics_framework.common.config_handler import Config
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


def find_files(folder_path: str) -> tuple[list[Any], list[Any], list[Any], list[Any], Config | None]:
    """
    Recursively search for CSV and YAML files under `folder_path` and categorize them by content.

    Returns lists of discovered test case, module, element and api files and an optional
    Config object (if a suitable YAML config is found).
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
        config_obj,
    )


def _initialize_file_collections():
    """Initialize collections for different file types."""
    return {
        'test_case': [],
        'module': [],
        'element': [],
        'api': []
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
        error_msg = f"Missing required files in {folder_path}: {', '.join(missing)}"
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
            config_obj,
        ) = find_files(self.folder_path)

        self._init_data_readers()
        self._load_test_cases(test_case_files)
        self._load_modules(module_files)
        self._load_elements(element_files)
        self._load_api_data(api_files)

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

    def _load_templates(self):
        """Load template data by discovering image files in the project directory."""
        self.templates_data: TemplateData = TemplateData()
        if hasattr(self.config, 'project_path') and self.config.project_path:
            self.templates_data = discover_templates(self.config.project_path)



    def _filter_and_build_execution_queue(self):
        included = self.config.get("include")
        excluded = self.config.get("exclude")
        self.filtered_test_cases: Dict[str, Any] = filter_test_cases(
            self.test_cases_data, included, excluded
        )
        self.execution_queue: TestCaseNode = build_linked_list(
            self.filtered_test_cases, self.modules_data
        )

    def _setup_session(self):
        self.manager: SessionManager = SessionManager()
        self.session_id: str = self.manager.create_session(
            self.config,
            self.execution_queue,
            self.modules_data,
            self.elements_data,
            self.api_data,
            self.templates_data,
        )
        self.engine: ExecutionEngine = ExecutionEngine(self.manager)
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
            await self.engine.execute(params)
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
        await self.run("batch")


class DryRunRunner(BaseRunner):
    async def execute(self):
        """Perform dry run of test cases."""
        await self.run("dry_run")


def execute_main(
    folder_path: str, runner: str = "test_runner", use_printer: bool = True
):
    """Entry point for execute command."""
    args = RunnerArgs(folder_path=folder_path, runner=runner, use_printer=use_printer)
    runner_instance = ExecuteRunner(args)
    asyncio.run(runner_instance.execute())


def dryrun_main(
    folder_path: str, runner: str = "test_runner", use_printer: bool = True
):
    """Entry point for dry run command."""
    args = RunnerArgs(folder_path=folder_path, runner=runner, use_printer=use_printer)
    runner_instance = DryRunRunner(args)
    asyncio.run(runner_instance.execute())
