import os
import sys
import asyncio
from typing import Optional, Tuple, List, Dict, Set
import yaml
from pydantic import BaseModel, field_validator
from optics_framework.common.logging_config import internal_logger, reconfigure_logging
from optics_framework.common.Junit_eventhandler import setup_junit
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.runner.data_reader import (
    CSVDataReader,
    YAMLDataReader,
    merge_dicts,
)
from optics_framework.common.session_manager import SessionManager
from optics_framework.common.execution import ExecutionEngine, ExecutionParams
from optics_framework.common.models import (
    TestCaseNode,
    ModuleNode,
    KeywordNode,
    ElementData,
)


def find_files(folder_path: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Search for CSV and YAML files in a folder and categorize them by content.
    Exits the program if required files (test cases and modules) are missing.

    :param folder_path: Path to the project folder.
    :return: Tuple of lists of paths to test case files, module files, and element files.
    """
    test_case_files = []
    module_files = []
    element_files = []

    for file in os.listdir(folder_path):
        if file.endswith((".csv", ".yml", ".yaml")):
            file_path = os.path.join(folder_path, file)
            content_type = identify_file_content(file_path)
            if "test_cases" in content_type:
                test_case_files.append(file_path)
            if "modules" in content_type:
                module_files.append(file_path)
            if "elements" in content_type:
                element_files.append(file_path)

    validate_required_files(test_case_files, module_files, folder_path)
    return test_case_files, module_files, element_files


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
    :return: Set of content types ('test_cases', 'modules', 'elements').
    """
    content_types = set()
    if "Test Cases" in data:
        content_types.add("test_cases")
    if "Modules" in data:
        content_types.add("modules")
    if "Elements" in data:
        content_types.add("elements")
    return content_types


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
    test_cases_dict: Dict,
    include: List[str] | None = None,
    exclude: List[str] | None = None,
) -> Dict:
    """
    Filter a dictionary of test cases based on include or exclude list.
    Always include setup or teardown test cases.

    :param test_cases_dict: Dictionary of test case names and their steps.
    :param include: List of test case names to include (case-insensitive).
    :param exclude: List of test case names to exclude (case-insensitive).
    :return: Filtered dictionary with test case names as keys.
    """
    if include and exclude:
        raise ValueError("Provide either include or exclude list, not both.")

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
    test_cases_data: Dict,
) -> Tuple[
    Optional[Tuple[str, List]],
    Optional[Tuple[str, List]],
    Optional[Tuple[str, List]],
    Optional[Tuple[str, List]],
    Dict[str, List],
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
    execution_dict = {}

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


def create_test_case_nodes(execution_dict: Dict) -> TestCaseNode:
    """
    Create a linked list of TestCaseNode objects from the execution dictionary.

    :param execution_dict: Ordered dictionary of test case names and their modules.
    :return: Head of the TestCaseNode linked list.
    """
    head = None
    prev_tc = None

    for tc_name in execution_dict:
        tc_node = TestCaseNode(name=tc_name)
        if not head:
            head = tc_node
        if prev_tc:
            prev_tc.next = tc_node
        prev_tc = tc_node

    if not head:
        raise ValueError("No test cases found to build linked list")

    return head


def populate_module_nodes(
    tc_node: TestCaseNode, modules: List, modules_data: Dict
) -> None:
    """
    Populate a TestCaseNode with its ModuleNodes and their KeywordNodes.

    :param tc_node: TestCaseNode to populate.
    :param modules: List of module names for the test case.
    :param modules_data: Dictionary mapping module names to keyword tuples.
    """
    module_head = None
    module_prev = None

    for module_name in modules:
        module_node = ModuleNode(name=module_name)
        if not module_head:
            module_head = module_node
        if module_prev:
            module_prev.next = module_node
        module_prev = module_node

        keyword_head = None
        keyword_prev = None

        for keyword, params in modules_data.get(module_name, []):
            keyword_node = KeywordNode(name=keyword, params=params)
            if not keyword_head:
                keyword_head = keyword_node
            if keyword_prev:
                keyword_prev.next = keyword_node
            keyword_prev = keyword_node

        module_node.keywords_head = keyword_head

    tc_node.modules_head = module_head


def build_linked_list(test_cases_data: Dict, modules_data: Dict) -> TestCaseNode:
    """
    Build a nested linked list structure representing the test execution flow.

    :param test_cases_data: Dictionary mapping test case names to a list of module names.
    :param modules_data: Dictionary mapping module names to a list of (keyword, params) tuples.
    :return: Head of the linked list of TestCaseNode objects representing the full execution flow.
    """
    # Get the ordered execution dict
    execution_dict = get_execution_queue(test_cases_data)

    # Create TestCaseNode linked list
    head = create_test_case_nodes(execution_dict)

    # Populate modules and keywords for each test case
    current = head
    while current:
        populate_module_nodes(current, execution_dict[current.name], modules_data)
        current = current.next

    return head


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
            raise ValueError(f"Invalid project folder: {abs_path}")
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

        # Find all relevant files
        test_case_files, module_files, element_files = find_files(self.folder_path)

        # Initialize data readers
        csv_reader = CSVDataReader()
        yaml_reader = YAMLDataReader()

        # Read and merge test cases
        self.test_cases_data = {}
        for file_path in test_case_files:
            reader = csv_reader if file_path.endswith(".csv") else yaml_reader
            test_cases = reader.read_test_cases(file_path)
            self.test_cases_data = merge_dicts(
                self.test_cases_data, test_cases, "test_cases"
            )

        # Read and merge modules
        self.modules_data = {}
        for file_path in module_files:
            reader = csv_reader if file_path.endswith(".csv") else yaml_reader
            modules = reader.read_modules(file_path)
            self.modules_data = merge_dicts(self.modules_data, modules, "modules")

        # Read and merge elements
        self.elements_data = {}
        for file_path in element_files:
            reader = csv_reader if file_path.endswith(".csv") else yaml_reader
            elements = reader.read_elements(file_path)
            self.elements_data = merge_dicts(self.elements_data, elements, "elements")

        if not self.test_cases_data:
            internal_logger.debug(f"No test cases found in {test_case_files}")

        # Load and validate configuration
        self.config_handler = ConfigHandler.get_instance()
        self.config_handler.set_project(self.folder_path)
        self.config_handler.load()
        self.config = self.config_handler.config
        self.config.project_path = self.folder_path
        internal_logger.debug(f"Loaded configuration: {self.config}")
        reconfigure_logging()
        setup_junit()  # Setup JUnit event handler if configured

        # Validate required configs
        required_configs = ["driver_sources", "elements_sources"]
        missing_configs = [
            key for key in required_configs if not self.config_handler.get(key)
        ]
        if missing_configs:
            internal_logger.error(
                f"Missing required configuration keys: {', '.join(missing_configs)}"
            )
            raise ValueError(
                f"Configuration missing required keys: {', '.join(missing_configs)}"
            )

        # Setup session
        self.manager = SessionManager()
        self.session_id = self.manager.create_session(self.config)
        self.engine = ExecutionEngine(self.manager)

        # Filter test cases
        included, excluded = (
            self.config_handler.get("include"),
            self.config_handler.get("exclude"),
        )
        self.filtered_test_cases = filter_test_cases(
            self.test_cases_data, included, excluded
        )
        self.execution_queue = build_linked_list(
            self.filtered_test_cases, self.modules_data
        )

    async def run(self, mode: str):
        """Run the specified mode using ExecutionEngine."""
        try:
            params = ExecutionParams(
                session_id=self.session_id,
                mode=mode,
                test_cases=self.execution_queue,
                modules=self.modules_data,
                elements=ElementData(elements=self.elements_data),
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
