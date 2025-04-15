import os
import sys
import asyncio
from typing import Optional, Tuple
from pydantic import BaseModel, field_validator
from optics_framework.common.logging_config import internal_logger, reconfigure_logging
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.runner.csv_reader import CSVDataReader
from optics_framework.common.session_manager import SessionManager
from optics_framework.common.execution import ExecutionEngine, ExecutionParams, TestCaseData, ModuleData, ElementData

def find_csv_files(folder_path: str) -> Tuple[str, str, Optional[str]]:
    """
    Search for CSV files in a folder and categorize them by reading their headers.
    Exits the program if required files are missing.

    :param folder_path: Path to the project folder.
    :return: Tuple of paths to test_cases (required), modules (required), and elements (optional) CSV files.
    """
    test_cases, modules, elements = scan_folder_for_csvs(folder_path)
    return test_cases, modules, elements


def scan_folder_for_csvs(folder_path: str) -> Tuple[str, str, Optional[str]]:
    """Scans folder for CSV files and categorizes them based on headers."""
    test_cases: str = ""  # Initialize as empty string instead of None
    modules: str = ""     # Initialize as empty string instead of None
    elements: Optional[str] = None

    for file in os.listdir(folder_path):
        if file.endswith(".csv"):
            file_path = os.path.join(folder_path, file)
            headers = read_csv_headers(file_path)
            if headers:
                test_cases, modules, elements = categorize_file(
                    headers, file_path, test_cases, modules, elements)

    validate_required_files(test_cases, modules, folder_path)
    return test_cases, modules, elements


def read_csv_headers(file_path: str) -> Optional[set]:
    """Reads and returns the headers of a CSV file as a set."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            header = f.readline().strip().split(',')
            return {h.strip().lower() for h in header}
    except (OSError, IOError) as e:
        internal_logger.exception(f"Error reading {file_path}: {e}")
        return None


def categorize_file(headers: set, file_path: str, test_cases: str,
                    modules: str, elements: Optional[str]) -> Tuple[str, str, Optional[str]]:
    """Categorizes a CSV file based on its headers."""
    if "test_case" in headers and "test_step" in headers:
        internal_logger.debug(f"Found test cases file: {file_path}")
        return file_path, modules, elements
    if "module_name" in headers and "module_step" in headers:
        internal_logger.debug(f"Found modules file: {file_path}")
        return test_cases, file_path, elements
    if "element_name" in headers and "element_id" in headers:
        internal_logger.debug(f"Found elements file: {file_path}")
        return test_cases, modules, file_path
    return test_cases, modules, elements


def validate_required_files(test_cases: str, modules: str, folder_path: str) -> None:
    """Validates that required CSV files are present; exits if missing."""
    if not test_cases or not modules:
        missing = [f for f, p in [
            ("test_cases", test_cases), ("modules", modules)] if not p]
        error_msg = f"Missing required CSV files in {folder_path}: {', '.join(missing)}"
        internal_logger.error(error_msg)
        print(f"Error: {error_msg}", file=sys.stderr)
        sys.exit(1)

def filter_test_cases(test_cases_dict: dict, include: list = None, exclude: list = None) -> dict:
    """
    Filters a dictionary of test cases based on include or exclude list.
    Always includes any test cases that are setup or teardown.

    :param test_cases_dict: Dictionary of test case names and their steps.
    :param include: List of test case names to include (case-insensitive).
    :param exclude: List of test case names to exclude (case-insensitive).
    :return: Filtered dictionary with test case names as keys.
    """
    if include and exclude:
        raise ValueError("Provide either include or exclude list, not both.")

    include_set = set(tc.strip().lower() for tc in include) if include else None
    exclude_set = set(tc.strip().lower() for tc in exclude) if exclude else None

    filtered = {}

    for name, steps in test_cases_dict.items():
        lname = name.lower()

        # Always include any setup or teardown case
        if "setup" in lname or "teardown" in lname:
            filtered[name] = steps
            continue

        # Include or exclude logic for test cases
        if include_set and lname in include_set:
            filtered[name] = steps
        elif exclude_set and lname not in exclude_set:
            filtered[name] = steps
        elif not include_set and not exclude_set:
            filtered[name] = steps

    return filtered

def get_execution_queue(test_cases_data: dict, test_case_name="", case_setup_teardown: bool = True) -> dict:
    """
    Builds and returns the execution queue as a dictionary:
    - Handles unordered entries in CSV.
    - Supports single or multiple test case names.
    - Includes per-test setup/teardown if toggle is True.
    """

    suite_setup = None
    suite_teardown = None
    setup = None
    teardown = None
    regular_test_cases = {}

    # Normalize test_case_name to list of lowercase strings
    if isinstance(test_case_name, str) and test_case_name.strip():
        test_case_name_list = [test_case_name.strip().lower()]
    elif isinstance(test_case_name, list) and test_case_name:
        test_case_name_list = [tc.strip().lower() for tc in test_case_name]
    else:
        test_case_name_list = None  # Run all test cases

    # Categorize entries
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

    execution_dict = {}

    # Case: specific test case(s) provided
    if test_case_name_list:
        matched_cases = {
            name: steps for name, steps in regular_test_cases.items()
            if name.lower() in test_case_name_list
        }

        if not matched_cases:
            raise ValueError(f"None of the specified test cases found: {test_case_name}")

        if suite_setup:
            execution_dict[suite_setup[0]] = suite_setup[1]

        for name, steps in matched_cases.items():
            if case_setup_teardown and setup:
                execution_dict[setup[0]] = setup[1]
            execution_dict[name] = steps
            if case_setup_teardown and teardown:
                execution_dict[teardown[0]] = teardown[1]

        if suite_teardown:
            execution_dict[suite_teardown[0]] = suite_teardown[1]

    # Case: run all test cases
    else:
        if suite_setup:
            execution_dict[suite_setup[0]] = suite_setup[1]

        for name, steps in regular_test_cases.items():
            if case_setup_teardown and setup:
                execution_dict[setup[0]] = setup[1]
            execution_dict[name] = steps
            if case_setup_teardown and teardown:
                execution_dict[teardown[0]] = teardown[1]

        if suite_teardown:
            execution_dict[suite_teardown[0]] = suite_teardown[1]

    return execution_dict


class RunnerArgs(BaseModel):
    """Arguments for BaseRunner initialization."""
    folder_path: str
    test_name: str = ""
    runner: str = "test_runner"

    @field_validator('folder_path')
    @classmethod
    def folder_path_must_exist(cls, v: str) -> str:
        """Ensure folder_path is an existing directory."""
        abs_path = os.path.abspath(v)
        if not os.path.isdir(abs_path):
            raise ValueError(f"Invalid project folder: {abs_path}")
        return abs_path

    @field_validator('test_name')
    @classmethod
    def strip_test_name(cls, v: str) -> str:
        """Strip whitespace from test_name."""
        return v.strip()

    @field_validator('runner')
    @classmethod
    def strip_runner(cls, v: str) -> str:
        """Strip whitespace from runner."""
        return v.strip()


class BaseRunner:
    """Base class for running test cases from CSV files using ExecutionEngine."""

    def __init__(self, args: RunnerArgs):
        self.folder_path = args.folder_path
        self.test_name = args.test_name
        self.runner = args.runner
        # Added for debugging
        internal_logger.debug(f"Using runner: {self.runner}")

        # Validate CSV files (test_cases and modules required, elements optional)
        test_cases_file, modules_file, elements_file = find_csv_files(
            self.folder_path)

        # Load CSV data
        csv_reader = CSVDataReader()
        self.test_cases_data = csv_reader.read_test_cases(test_cases_file)
        self.modules_data = csv_reader.read_modules(modules_file)
        self.elements_data = csv_reader.read_elements(
            elements_file) if elements_file else {}

        if not self.test_cases_data:
            internal_logger.debug(f"No test cases found in {test_cases_file}")

        # Load and validate configuration using ConfigHandler
        self.config_handler = ConfigHandler.get_instance()
        self.config_handler.set_project(self.folder_path)
        self.config_handler.load()
        self.config = self.config_handler.config

        # Ensure project_path is set in the Config object
        self.config.project_path = self.folder_path
        internal_logger.debug(f"Loaded configuration: {self.config}")
        reconfigure_logging()
        # Check required configs using the get() method
        required_configs = ["driver_sources", "elements_sources"]
        missing_configs = [
            key for key in required_configs if not self.config_handler.get(key)]
        if missing_configs:
            internal_logger.error(
                f"Missing required configuration keys: {', '.join(missing_configs)}")
            raise ValueError(
                f"Configuration missing required keys: {', '.join(missing_configs)}")

        # Setup session
        self.manager = SessionManager()
        self.session_id = self.manager.create_session(self.config)
        self.engine = ExecutionEngine(self.manager)

        # test case selection rules
        included, excluded = self.config_handler.get('include'), self.config_handler.get('exclude')
        self.filtered_test_cases = filter_test_cases(
            self.test_cases_data, included, excluded)

        self.execution_queue = get_execution_queue(
            self.filtered_test_cases, self.test_name, case_setup_teardown=False)

    async def run(self, mode: str):
        """Run the specified mode using ExecutionEngine."""
        try:
            params = ExecutionParams(
                session_id=self.session_id,
                mode=mode,
                test_case=self.test_name if self.test_name else None,
                event_queue=None,  # Local mode uses TreeResultPrinter
                test_cases=TestCaseData(test_cases=self.execution_queue),
                modules=ModuleData(modules=self.modules_data),
                elements=ElementData(elements=self.elements_data),
                runner_type=self.runner
            )
            internal_logger.debug(f"Executing with runner_type: {self.runner}")
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
            internal_logger.error(
                f"Failed to terminate session {self.session_id}: {e}")


class ExecuteRunner(BaseRunner):
    async def execute(self):
        """Execute test cases."""
        await self.run("batch")


class DryRunRunner(BaseRunner):
    async def execute(self):
        """Perform dry run of test cases."""
        await self.run("dry_run")


def execute_main(folder_path: str, test_name: str = "", runner: str = "test_runner"):
    """Entry point for execute command."""
    args = RunnerArgs(folder_path=folder_path,
                      test_name=test_name, runner=runner)
    runner_instance = ExecuteRunner(args)
    asyncio.run(runner_instance.execute())


def dryrun_main(folder_path: str, test_name: str = "", runner: str = "test_runner"):
    """Entry point for dry run command."""
    args = RunnerArgs(folder_path=folder_path,
                      test_name=test_name, runner=runner)
    runner_instance = DryRunRunner(args)
    asyncio.run(runner_instance.execute())
