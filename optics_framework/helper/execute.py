import os
import sys
import asyncio
from typing import Optional, Tuple, List
import yaml
from pydantic import BaseModel, field_validator
from optics_framework.common.logging_config import internal_logger, reconfigure_logging
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.runner.data_reader import CSVDataReader, YAMLDataReader, merge_dicts
from optics_framework.common.session_manager import SessionManager
from optics_framework.common.execution import ExecutionEngine, ExecutionParams
from optics_framework.common.models import TestCaseNode, ModuleNode, KeywordNode, ElementData


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


def identify_file_content(file_path: str) -> set:
    """
    Identify the content type of a file based on its headers (CSV) or keys (YAML).

    :param file_path: Path to the file.
    :return: Set of content types ('test_cases', 'modules', 'elements').
    """
    content_types = set()
    try:
        if file_path.endswith(".csv"):
            headers = read_csv_headers(file_path)
            if headers:
                if {"test_case", "test_step"}.issubset(headers):
                    content_types.add("test_cases")
                if {"module_name", "module_step"}.issubset(headers):
                    content_types.add("modules")
                if {"element_name", "element_id"}.issubset(headers):
                    content_types.add("elements")
        else:  # YAML file
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                if "Test Cases" in data:
                    content_types.add("test_cases")
                if "Modules" in data:
                    content_types.add("modules")
                if "Elements" in data:
                    content_types.add("elements")
    except Exception as e:
        internal_logger.exception(f"Error reading {file_path}: {e}")
    return content_types


def read_csv_headers(file_path: str) -> Optional[set]:
    """
    Read and return the headers of a CSV file as a set.

    :param file_path: Path to the CSV file.
    :return: Set of header names or None if reading fails.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            header = f.readline().strip().split(',')
            return {h.strip().lower() for h in header}
    except (OSError, IOError) as e:
        internal_logger.exception(f"Error reading {file_path}: {e}")
        return None


def validate_required_files(test_case_files: List[str], module_files: List[str], folder_path: str) -> None:
    """
    Validate that required files (test cases and modules) are present; exit if missing.

    :param test_case_files: List of test case file paths.
    :param module_files: List of module file paths.
    :param folder_path: Path to the project folder.
    """
    if not test_case_files or not module_files:
        missing = [f for f, p in [
            ("test_cases", test_case_files), ("modules", module_files)] if not p]
        error_msg = f"Missing required files in {folder_path}: {', '.join(missing)}"
        internal_logger.error(error_msg)
        print(f"Error: {error_msg}", file=sys.stderr)
        sys.exit(1)


def filter_test_cases(test_cases_dict: dict, include: list = None, exclude: list = None) -> dict:
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

    include_set = set(tc.strip().lower()
                      for tc in include) if include else set()
    exclude_set = set(tc.strip().lower()
                      for tc in exclude) if exclude else set()

    filtered = {}
    for name, steps in test_cases_dict.items():
        lname = name.lower()
        if "setup" in lname or "teardown" in lname:
            filtered[name] = steps
            continue
        if include_set:
            if lname in include_set:
                filtered[name] = steps
        elif exclude_set:
            if lname not in exclude_set:
                filtered[name] = steps
        else:
            filtered[name] = steps
    return filtered


def build_linked_list(test_cases_data: dict, modules_data: dict) -> TestCaseNode:
    """Build a nested singly linked list from test cases and modules."""
    head = None
    prev = None
    for tc_name, modules in test_cases_data.items():
        tc_node = TestCaseNode(name=tc_name)
        if not head:
            head = tc_node
        if prev:
            prev.next = tc_node
        prev = tc_node

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
    if not head:
        raise ValueError("No test cases found to build linked list")
    return head


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
    """Base class for running test cases from CSV and YAML files using ExecutionEngine."""

    def __init__(self, args: RunnerArgs):
        self.folder_path = args.folder_path
        self.test_name = args.test_name
        self.runner = args.runner
        internal_logger.debug(f"Using runner: {self.runner}")

        # Find all relevant files
        test_case_files, module_files, element_files = find_files(
            self.folder_path)

        # Initialize data readers
        csv_reader = CSVDataReader()
        yaml_reader = YAMLDataReader()

        # Read and merge test cases
        self.test_cases_data = {}
        for file_path in test_case_files:
            reader = csv_reader if file_path.endswith(".csv") else yaml_reader
            test_cases = reader.read_test_cases(file_path)
            self.test_cases_data = merge_dicts(
                self.test_cases_data, test_cases, "test_cases")

        # Read and merge modules
        self.modules_data = {}
        for file_path in module_files:
            reader = csv_reader if file_path.endswith(".csv") else yaml_reader
            modules = reader.read_modules(file_path)
            self.modules_data = merge_dicts(
                self.modules_data, modules, "modules")

        # Read and merge elements
        self.elements_data = {}
        for file_path in element_files:
            reader = csv_reader if file_path.endswith(".csv") else yaml_reader
            elements = reader.read_elements(file_path)
            self.elements_data = merge_dicts(
                self.elements_data, elements, "elements")

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

        # Validate required configs
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

        # Filter test cases
        included, excluded = self.config_handler.get(
            'include'), self.config_handler.get('exclude')
        self.filtered_test_cases = filter_test_cases(
            self.test_cases_data, included, excluded)
        self.execution_queue = build_linked_list(
            self.filtered_test_cases, self.modules_data)

    async def run(self, mode: str):
        """Run the specified mode using ExecutionEngine."""
        try:
            params = ExecutionParams(
                session_id=self.session_id,
                mode=mode,
                test_case=self.test_name if self.test_name else None,
                test_cases=self.execution_queue,
                modules=self.modules_data,
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
