import os
import asyncio
from typing import Optional, Tuple
from pydantic import BaseModel, field_validator
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.runner.csv_reader import CSVDataReader
from optics_framework.common.session_manager import SessionManager
from optics_framework.common.execution import ExecutionEngine, ExecutionParams, TestCaseData, ModuleData, ElementData


def find_csv_files(folder_path: str) -> Tuple[str, str, Optional[str]]:
    """
    Search for CSV files in a folder and categorize them by reading their headers.

    :param folder_path: Path to the project folder.
    :return: Tuple of paths to test_cases (required), modules (required), and elements (optional) CSV files.
    """
    test_cases, modules, elements = None, None, None
    for file in os.listdir(folder_path):
        if file.endswith(".csv"):
            file_path = os.path.join(folder_path, file)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    header = f.readline().strip().split(',')
                    headers = {h.strip().lower() for h in header}
            except (OSError, IOError) as e:
                internal_logger.exception(f"Error reading {file_path}: {e}")
                continue

            if "test_case" in headers and "test_step" in headers:
                test_cases = file_path
                internal_logger.debug(f"Found test cases file: {file_path}")
            elif "module_name" in headers and "module_step" in headers:
                modules = file_path
                internal_logger.debug(f"Found modules file: {file_path}")
            elif "element_name" in headers and "element_id" in headers:
                elements = file_path
                internal_logger.debug(f"Found elements file: {file_path}")

    if not test_cases or not modules:
        missing = [f for f, p in [
            ("test_cases", test_cases), ("modules", modules)] if not p]
        internal_logger.error(
            f"Missing required CSV files in {folder_path}: {', '.join(missing)}")
        raise ValueError(f"Required CSV files missing: {', '.join(missing)}")
    return test_cases, modules, elements


class RunnerArgs(BaseModel):
    """Arguments for BaseRunner initialization."""
    folder_path: str
    test_name: str = ""

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


class BaseRunner:
    """Base class for running test cases from CSV files using ExecutionEngine."""

    def __init__(self, args: RunnerArgs):
        self.folder_path = args.folder_path
        self.test_name = args.test_name

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

    async def run(self, mode: str):
        """Run the specified mode using ExecutionEngine."""
        try:
            params = ExecutionParams(
                session_id=self.session_id,
                mode=mode,
                test_case=self.test_name if self.test_name else None,
                event_queue=None,  # Local mode uses TreeResultPrinter
                test_cases=TestCaseData(test_cases=self.test_cases_data),
                modules=ModuleData(modules=self.modules_data),
                elements=ElementData(elements=self.elements_data),
                runner_type="test_runner"  # Default; could be configurable
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


def execute_main(folder_path: str, test_name: str = ""):
    """Entry point for execute command."""
    args = RunnerArgs(folder_path=folder_path, test_name=test_name)
    runner = ExecuteRunner(args)
    asyncio.run(runner.execute())


def dryrun_main(folder_path: str, test_name: str = ""):
    """Entry point for dry run command."""
    args = RunnerArgs(folder_path=folder_path, test_name=test_name)
    runner = DryRunRunner(args)
    asyncio.run(runner.execute())


if __name__ == "__main__":
    folder_path = "/Users/dhruvmenon/Documents/optics-framework-1/optics_framework/samples/contact/"
    execute_main(folder_path, "")
