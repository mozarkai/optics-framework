import os
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Optional, Union, Any
import logging
import pandas as pd
import yaml
import json
import shutil

TestCaseKey = "Test Cases"

# Type aliases for clarity
TestCases = Dict[str, List[str]]
Modules = Dict[str, List[Tuple[str, List[str]]]]
Elements = Dict[str, str]
Config = Dict


class DataReader(ABC):
    """Abstract base class for reading test data."""

    @abstractmethod
    def read_test_cases(self, source: str) -> TestCases:
        pass

    @abstractmethod
    def read_modules(self, source: str) -> Modules:
        pass

    @abstractmethod
    def read_elements(self, source: str) -> Elements:
        pass

    @abstractmethod
    def read_config(self, source: str) -> Config:
        pass


class CSVDataReader(DataReader):
    """Reader for CSV-based test data."""

    def read_test_cases(self, source: str) -> TestCases:
        df = pd.read_csv(source)
        test_cases = {}
        for test_case in df["test_case"].unique():
            test_cases[test_case.strip()] = (
                df[df["test_case"] == test_case]["test_step"].str.strip().tolist()
            )
        return test_cases

    def read_modules(self, source: str) -> Modules:
        df = pd.read_csv(source, dtype=str)
        modules = {}
        for module_name in df["module_name"].unique():
            module_df = df[df["module_name"] == module_name]
            steps = []
            for _, row in module_df.iterrows():
                # Skip rows where module_step is NaN or empty
                if pd.isna(row["module_step"]) or not str(row["module_step"]).strip():
                    continue

                step = (
                    str(row["module_step"]).strip(),
                    [
                        self._format_param_value(row[f"param_{i + 1}"])
                        for i in range(len(row) - 2)
                        if pd.notna(row[f"param_{i + 1}"])
                    ],
                )
                steps.append(step)
            modules[str(module_name).strip()] = steps
        return modules

    def _format_param_value(self, value) -> str:
        """Format parameter value, preserving the original format from CSV."""
        return str(value).strip()

    def read_elements(self, source: str) -> Elements:
        df = pd.read_csv(source)
        return dict(zip(df["Element_Name"].str.strip(), df["Element_ID"].str.strip()))

    def read_config(self, source: str) -> Config:
        with open(source, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}


class YAMLDataReader(DataReader):
    """Reader for YAML-based test data."""

    def read_test_cases(self, source: str) -> TestCases:
        with open(source, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        test_cases = {}
        test_cases_list = data.get(TestCaseKey, [])
        if isinstance(test_cases_list, list):
            for item in test_cases_list:
                if isinstance(item, dict):
                    test_cases.update(item)
        elif isinstance(test_cases_list, dict):
            test_cases = test_cases_list
        return test_cases

    def _parse_step(self, step: str, keyword_registry: set) -> Tuple[str, List[str]]:
        parts = step.split(maxsplit=1)
        keyword = parts[0] if parts else ""
        params = []
        if len(parts) > 1:
            # Sort keywords by length in descending order to match longer keywords first
            for reg_keyword in sorted(keyword_registry, key=len, reverse=True):
                if step.startswith(reg_keyword):
                    keyword = reg_keyword
                    params = (
                        step[len(reg_keyword) :].strip().split()
                        if step[len(reg_keyword) :].strip()
                        else []
                    )
                    break
            else:
                params = parts[1].split() if parts[1] else []
        return keyword, params

    def read_modules(self, source: str) -> Modules:
        with open(source, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        modules = {}
        keyword_registry = {
            "Launch App",
            "Launch Other App",
            "Start Appium Session",
            "Close and Terminate App",
            "Get App Version",
            "Press Element",
            "Press By Percentage",
            "Press By Coordinates",
            "Press Element With Index",
            "Detect and Press",
            "Swipe",
            "Swipe Until Element Appears",
            "Swipe From Element",
            "Scroll",
            "Scroll Until Element Appears",
            "Scroll From Element",
            "Enter Text",
            "Enter Text Direct",
            "Enter Text Using Keyboard",
            "Enter Number",
            "Press Keycode",
            "Clear Element Text",
            "Get Text",
            "Sleep",
            "Validate Element",
            "Assert Presence",
            "Validate Screen",
            "Get Interactive Elements",
            "Capture Screenshot",
            "Capture Page Source",
            "Quit",
        }
        modules_list = data.get("Modules", [])
        if isinstance(modules_list, list):
            for item in modules_list:
                if isinstance(item, dict):
                    for module_name, steps in item.items():
                        modules[module_name] = [
                            self._parse_step(step, keyword_registry) for step in steps
                        ]
        elif isinstance(modules_list, dict):
            for module_name, steps in modules_list.items():
                modules[module_name] = [
                    self._parse_step(step, keyword_registry) for step in steps
                ]
        return modules

    def read_elements(self, source: str) -> Elements:
        with open(source, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("Elements", {})

    def read_config(self, source: str) -> Config:
        with open(source, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}


class TestFrameworkGenerator(ABC):
    """Abstract base class for generating test framework code."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.keyword_registry = {
            "Launch App": "launch_app",
            "Launch Other App": "launch_other_app",
            "Start Appium Session": "start_appium_session",
            "Close and Terminate App": "close_and_terminate_app",
            "Get App Version": "get_app_version",
            "Press Element": "press_element",
            "Press By Percentage": "press_by_percentage",
            "Press By Coordinates": "press_by_coordinates",
            "Press Element With Index": "press_element_with_index",
            "Detect and Press": "detect_and_press",
            "Swipe": "swipe",
            "Swipe Until Element Appears": "swipe_until_element_appears",
            "Swipe From Element": "swipe_from_element",
            "Scroll": "scroll",
            "Scroll Until Element Appears": "scroll_until_element_appears",
            "Scroll From Element": "scroll_from_element",
            "Enter Text": "enter_text",
            "Enter Text Direct": "enter_text_direct",
            "Enter Text Using Keyboard": "enter_text_using_keyboard",
            "Enter Number": "enter_number",
            "Press Keycode": "press_keycode",
            "Clear Element Text": "clear_element_text",
            "Get Text": "get_text",
            "Sleep": "sleep",
            "Validate Element": "validate_element",
            "Assert Presence": "assert_presence",
            "Validate Screen": "validate_screen",
            "Get Interactive Elements": "get_interactive_elements",
            "Capture Screenshot": "capture_screenshot",
            "Capture Page Source": "capture_pagesource",
            "Quit": "quit",
        }

    @abstractmethod
    def generate(
        self,
        test_cases: TestCases,
        modules: Modules,
        elements: Elements,
        config: Config,
    ) -> str:
        pass

    def _resolve_params(self, params: List[str], elements: Elements, framework: str) -> List[str]:
        resolved = []
        for param in params:
            if param.startswith("${") and param.endswith("}"):
                var_name = param[2:-1]
                if var_name not in elements:
                    raise ValueError(f"Element '{var_name}' not found in elements.")
                resolved.append(f"ELEMENTS['{var_name}']" if framework == "pytest" else f"${{ELEMENTS.{var_name}}}")
            elif "=" in param and not param.startswith(("'", '"')):
                resolved.append(param)
            elif framework == "pytest":
                resolved.append(f"'{param}'")
            else:
                resolved.append(param)
        return resolved


class PytestGenerator(TestFrameworkGenerator):
    """Generator for pytest-compatible test code."""

    def generate(
        self,
        test_cases: TestCases,
        modules: Modules,
        elements: Elements,
        config: Config,
    ) -> str:
        code_parts = [
            self._generate_header(),
            self._generate_config(config),
            self._generate_elements(elements),
            self._generate_setup(),
            "# Module functions\n",
        ]
        for module_name, steps in modules.items():
            code_parts.append(
                self._generate_module_function(module_name, steps, elements)
            )
        code_parts.append("# Test functions\n")
        for test_case_name, module_names in test_cases.items():
            code_parts.append(
                self._generate_test_function(test_case_name, module_names)
            )
        return "\n".join(code_parts)

    def _generate_header(self) -> str:
        return "\n".join(
            [
                "# Auto-generated by generate.py. Do not edit manually.",
                "import os",
                "import pytest",
                "from optics_framework.optics import Optics",
                "from optics_framework.common.utils import load_config",
                "",
            ]
        )

    def _generate_config(self, config: Config) -> str:
        return "\n".join(
            [
                "# Get project path and setup execution output path",
                "PROJECT_PATH = os.path.dirname(os.path.abspath(__file__))",
                "EXECUTION_OUTPUT_PATH = os.environ.get('EXEC_OUTPUT', os.path.join(PROJECT_PATH, 'execution_outputs'))",
                "os.makedirs(EXECUTION_OUTPUT_PATH, exist_ok=True)",
                "env_config = os.environ.get('TEST_SESSION_ENV_VARIABLES')",
                "print(f'test env_config: {env_config}')",

                "",
                "CONFIG = {",
                f"    'driver_config': {config.get('driver_sources', [])},"
                if config.get("driver_sources")
                else "    'driver_config': [],",
                f"    'element_source_config': {config.get('elements_sources', [])},"
                if config.get("elements_sources")
                else "    'element_source_config': [],",
                f"    'text_config': {config.get('text_detection', [])},"
                if config.get("text_detection")
                else "    'text_config': [],",
                f"    'image_config': {config.get('image_detection', [])},"
                if config.get("image_detection")
                else "    'image_config': [],",
                "    'execution_output_path': EXECUTION_OUTPUT_PATH,",
                "    'project_path': PROJECT_PATH,",
                "    'event_attributes_json': os.environ.get('MOZARK_ATTRIBUTES_JSON'),",
                "}\n",
                "# Override with environment values if available",
                "CONFIG = load_config(CONFIG)",
                "print(f'config: {CONFIG}')",
                "",
            ]
        )

    def _generate_elements(self, elements: Elements) -> str:
        lines = ["ELEMENTS = {"]
        for name, value in elements.items():
            lines.append(f"    '{name}': '{value}',")
        lines.append("}\n")
        return "\n".join(lines)

    def _generate_setup(self) -> str:
        return "\n".join(
            [
                "@pytest.fixture(scope='module')",
                "def optics():",
                "    optics = Optics()",
                "    optics.setup(config=CONFIG)",
                "    print(f'Optics setup complete. Config: {optics.config}')",
                "    yield optics",
                "    optics.quit()\n",
            ]
        )

    def _generate_module_function(
        self, module_name: str, steps: List[Tuple[str, List[str]]], elements: Elements
    ) -> str:
        func_name = "_".join(module_name.lower().split())
        lines = [f"def {func_name}(optics: Optics) -> None:"]
        for keyword, params in steps:
            method_name = self.keyword_registry.get(
                keyword, "_".join(keyword.lower().split())
            )
            resolved_params = self._resolve_params(params, elements, "pytest")
            param_str = ", ".join(resolved_params)
            lines.append(f"    optics.{method_name}({param_str})")
        return "\n".join(lines) + "\n"

    def _generate_test_function(
        self, test_case_name: str, module_names: List[str]
    ) -> str:
        func_name = f"test_{'_'.join(test_case_name.lower().split())}"
        lines = [f"def {func_name}(optics):"]
        for module_name in module_names:
            module_func_name = "_".join(module_name.lower().split())
            lines.append(f"    {module_func_name}(optics)")
        return "\n".join(lines) + "\n"


class RobotGenerator(TestFrameworkGenerator):
    """Generator for Robot Framework-compatible test code."""

    def generate(
        self,
        test_cases: TestCases,
        modules: Modules,
        elements: Elements,
        config: Config,
    ) -> str:
        code_parts = [
            self._generate_header(),
            self._generate_variables(elements, config),
            self._generate_test_cases(test_cases),
            self._generate_keywords(modules, elements),
        ]
        return "\n".join(code_parts)

    def _generate_header(self) -> str:
        return "\n".join(
            [
                "*** Settings ***",
                "Library    optics_framework.optics.Optics",
                "Library    Collections",
                "Library    optics_framework.common.utils",
                "",
            ]
        )

    def _transform_config_structure(self, config: Config) -> Dict[str, Any]:
        """
        Transform the config object to match the expected Optics setup structure.

        Args:
            config: The original config object

        Returns:
            Dict with the structure expected by the new Optics setup method
        """
        transformed = {
            "driver_config": config.get('driver_sources', []),
            "element_source_config": config.get('elements_sources', []),
            "project_path": "${EXECDIR}"
        }

        # Add optional configurations only if they exist and are not empty
        if config.get('image_detection'):
            transformed["image_config"] = config.get('image_detection', [])

        if config.get('text_detection'):
            transformed["text_config"] = config.get('text_detection', [])

        if config.get('execution_output_path'):
            transformed["execution_output_path"] = config.get('execution_output_path')

        return transformed

    def _escape_json_for_robot(self, json_str: str) -> str:
        """
        Escape JSON string for Robot Framework variable assignment.

        Args:
            json_str: JSON string to escape

        Returns:
            Escaped string suitable for Robot Framework
        """
        # Replace backslashes first, then quotes
        escaped = json_str.replace('\\', '\\\\')
        escaped = escaped.replace('"', '\\"')
        escaped = escaped.replace('\n', '\\n')
        escaped = escaped.replace('\r', '\\r')
        escaped = escaped.replace('\t', '\\t')
        return escaped

    def _generate_variables(self, elements: Elements, config: Config) -> str:
        lines = ["*** Variables ***"]

        # Generate element dictionary
        lines.extend(["# Element dictionary", "&{ELEMENTS}="])
        for name, value in elements.items():
            lines.append(f"...    {name}={value}")
        lines.append("")

        # Transform config to the new structure
        transformed_config = self._transform_config_structure(config)

        # Generate configuration as JSON string
        config_json = json.dumps(transformed_config, separators=(',', ':'))  # Compact format
        escaped_config = self._escape_json_for_robot(config_json)

        lines.extend([
            "# Optics configuration as JSON string",
            f"${{OPTICS_CONFIG_JSON}}=    {escaped_config}",
            "",
        ])

        return "\n".join(lines)

    def _generate_test_cases(self, test_cases: TestCases) -> str:
        lines = ["*** Test Cases ***"]
        for test_case_name in test_cases:
            lines.append(test_case_name)
            lines.append("    [Setup]    Setup Optics")
            for module_name in test_cases[test_case_name]:
                lines.append(f"    {module_name}")
            lines.append("    [Teardown]    Quit Optics")
            lines.append("")
        return "\n".join(lines)

    def _generate_keywords(self, modules: Modules, elements: Elements) -> str:
        lines = ["*** Keywords ***"]

        # Setup keyword using JSON configuration
        lines.extend([
            "Setup Optics",
            "    # Parse JSON configuration and setup Optics",
            "    ${config_dict}=    Evaluate    json.loads(r'''${OPTICS_CONFIG_JSON}''')    json",
            "    Setup    config=${config_dict}",
            "",
        ])

        # Quit keyword
        lines.extend([
            "Quit Optics",
            "    Quit",
            "",
        ])

        # Generate module keywords
        for module_name, steps in modules.items():
            lines.append(module_name)
            for keyword, params in steps:
                resolved_params = self._resolve_params(params, elements, "robot")
                param_str = "    ".join(resolved_params)
                lines.append(f"    {keyword}    {param_str}")
            lines.append("")

        return "\n".join(lines)


class FileWriter:
    """Handles writing generated code and requirements to files."""

    def write(
        self, folder_path: str, filename: str, content: str, framework: str
    ) -> None:
        # Create generated/Tests/ directory
        tests_folder = os.path.join(folder_path, "Tests")
        os.makedirs(tests_folder, exist_ok=True)
        output_file = os.path.join(tests_folder, filename)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(content)
        logging.info(f"Generated test file: {output_file}")

        # Create requirements.txt in generated/ directory
        requirements = [
            "optics-framework",  # core framework for execution
            "Appium-Python-Client",  # appium driver
            "pytest" if framework == "pytest" else "robotframework",
            "easyocr",  # default ocr model
            "pyserial",  # to communicate with BLE devices
            "pytest-tagging"  # for tagging tests in pytest
        ]
        requirements_file = os.path.join(folder_path, "requirements.txt")
        with open(requirements_file, "w", encoding="utf-8") as f:
            f.write("\n".join(requirements) + "\n")
        logging.info(f"Generated requirements file: {requirements_file}")

    def copy_input_templates(self, source_folder: str, generated_folder: str) -> None:
        """
        Copy input_templates folder to generated/Tests/ if it exists.

        Args:
            source_folder: Path to the source folder that may contain input_templates
            generated_folder: Path to the generated folder where Tests/ is located
        """
        input_templates_path = os.path.join(source_folder, "input_templates")

        if os.path.exists(input_templates_path) and os.path.isdir(input_templates_path):
            tests_folder = os.path.join(generated_folder, "Tests")
            destination_path = os.path.join(tests_folder, "input_templates")

            try:
                # Remove destination if it already exists
                if os.path.exists(destination_path):
                    shutil.rmtree(destination_path)

                # Copy the entire input_templates folder
                shutil.copytree(input_templates_path, destination_path)
                logging.info(f"Copied input_templates folder to: {destination_path}")

            except Exception as e:
                logging.error(f"Failed to copy input_templates folder: {e}")
        else:
            logging.info("No input_templates folder found - skipping copy")


def detect_file_type(file_path: str) -> Optional[Tuple[str, str]]:
    """Detect file type and content type (test_cases, modules, elements, config)."""
    if not os.path.exists(file_path):
        return None

    extension = os.path.splitext(file_path)[1].lower()
    if extension == ".csv":
        return _detect_csv_type(file_path)
    if extension in [".yaml", ".yml"]:
        return _detect_yaml_type(file_path)
    return None


def _detect_csv_type(file_path: str) -> Optional[Tuple[str, str]]:
    try:
        df = pd.read_csv(file_path, nrows=1)
        headers = [h.strip().lower() for h in df.columns]
        if "test_case" in headers and "test_step" in headers:
            return "csv", "test_cases"
        if "module_name" in headers and "module_step" in headers:
            return "csv", "modules"
        if "element_name" in headers and "element_id" in headers:
            return "csv", "elements"
    except Exception as e:
        logging.error(f"Error reading CSV {file_path}: {e}")
    return None


def _detect_yaml_type(file_path: str) -> Optional[Tuple[str, str]]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if os.path.basename(file_path) == "config.yaml":
            return "yaml", "config"
        if TestCaseKey in data:
            return "yaml", "test_cases"
        if "Modules" in data:
            return "yaml", "modules"
        if "Elements" in data:
            return "yaml", "elements"
    except Exception as e:
        logging.error(f"Error reading YAML {file_path}: {e}")
    return None


def _assign_yaml_files(yaml_files, files):
    """Assign YAML files to test_cases, modules, and elements if not already assigned."""
    for yaml_file in yaml_files:
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for content_type in ["test_cases", "modules", "elements"]:
            key = (
                "Test Cases"
                if content_type == "test_cases"
                else content_type.capitalize()
            )
            if key in data and not files[content_type]:
                files[content_type] = yaml_file

def find_all_files(folder_path: str) -> dict[str, list[str]]:
    """Find all CSV and YAML files for each content type, supporting mixed formats with priority rules."""
    files: dict[str, list[str]] = {
        "test_cases": [],
        "modules": [],
        "elements": [],
        "config": [],
    }

    # First pass: collect all files by type
    all_files_by_type: dict[str, dict[str, list[str]]] = {
        "test_cases": {"csv": [], "yaml": []},
        "modules": {"csv": [], "yaml": []},
        "elements": {"csv": [], "yaml": []},
        "config": {"csv": [], "yaml": []},
    }

    for file in os.listdir(folder_path):
        file_path = os.path.join(folder_path, file)
        result = detect_file_type(file_path)
        if not result:
            continue
        file_format, content_type = result

        if content_type in all_files_by_type:
            all_files_by_type[content_type][file_format].append(file_path)

    # Apply merging rules:
    # - test_cases: Merge all files, raise error on conflicts
    # - modules: Merge all files, raise error on conflicts
    # - elements: Merge all files, raise error on conflicts
    # - config: Always YAML only

    # Test cases: Merge all with conflict detection
    files["test_cases"] = all_files_by_type["test_cases"]["csv"] + all_files_by_type["test_cases"]["yaml"]

    # Modules: Merge both CSV and YAML with conflict detection
    files["modules"] = all_files_by_type["modules"]["csv"] + all_files_by_type["modules"]["yaml"]

    # Elements: Merge both CSV and YAML with conflict detection
    files["elements"] = all_files_by_type["elements"]["csv"] + all_files_by_type["elements"]["yaml"]

    # Config: YAML only
    files["config"] = all_files_by_type["config"]["yaml"]

    return files


def find_files(
    folder_path: str,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Find test cases, modules, elements, and config files, detecting their format."""
    files: dict[str, Union[str, None]] = {
        "test_cases": None,
        "modules": None,
        "elements": None,
        "config": None,
    }
    yaml_files = []

    for file in os.listdir(folder_path):
        file_path = os.path.join(folder_path, file)
        result = detect_file_type(file_path)
        if not result:
            continue
        file_format, content_type = result
        if file_format == "csv":
            files[content_type] = file_path
        elif file_format == "yaml" and content_type != "config":
            yaml_files.append(file_path)
        else:
            files["config"] = file_path

    _assign_yaml_files(yaml_files, files)

    return files["test_cases"], files["modules"], files["elements"], files["config"]


def read_mixed_data(files: list[str], data_type: str) -> dict:
    """Read and merge data from multiple CSV and YAML files with conflict detection."""
    merged_data = {}
    conflict_sources = {}  # Track which file each key came from

    for file_path in files:
        if file_path.endswith(".csv"):
            reader = CSVDataReader()
        else:
            reader = YAMLDataReader()

        if data_type == "test_cases":
            data = reader.read_test_cases(file_path)
        elif data_type == "modules":
            data = reader.read_modules(file_path)
        elif data_type == "elements":
            data = reader.read_elements(file_path)
        else:
            continue

        # Check for conflicts and merge the data
        for key, value in data.items():
            if key in merged_data:
                # Conflict detected - raise descriptive error
                existing_source = conflict_sources[key]
                current_source = os.path.basename(file_path)
                raise ValueError(
                    f"Naming conflict detected in {data_type}: '{key}' is defined in both "
                    f"'{existing_source}' and '{current_source}'. Please use unique names "
                    f"across all {data_type} files to avoid conflicts."
                )

            merged_data[key] = value
            conflict_sources[key] = os.path.basename(file_path)

    return merged_data


def generate_test_file(
    folder_path: str, framework: str = "pytest", output_filename: str | None = None
) -> None:
    """
    Generate a test file from mixed CSV and YAML files in the specified folder.

    Args:
        folder_path (str): Path to the folder containing input files and config.yaml.
        framework (str): Target framework ('pytest' or 'robot').
        output_filename (str): Name of the output file (optional).
    """
    logging.basicConfig(level=logging.INFO)

    # Find all files for mixed CSV/YAML support
    all_files = find_all_files(folder_path)

    # Check for required files
    if not all_files["config"]:
        logging.error("Error: Missing config.yaml")
        return
    if not all_files["test_cases"]:
        logging.error("Error: Missing test cases file")
        return
    if not all_files["modules"]:
        logging.error("Error: Missing modules file")
        return
    if not all_files["elements"]:
        logging.error("Error: Missing elements file")
        return

    if framework == "pytest":
        generator = PytestGenerator()
        default_filename = f"test_{os.path.basename(folder_path)}.py"
    elif framework == "robot":
        generator = RobotGenerator()
        default_filename = f"test_{os.path.basename(folder_path)}.robot"
    else:
        logging.error(f"Unsupported framework: {framework}")
        return

    # Read and merge data from all files
    test_cases = read_mixed_data(all_files["test_cases"], "test_cases")
    modules = read_mixed_data(all_files["modules"], "modules")
    elements = read_mixed_data(all_files["elements"], "elements")

    # Config is always single YAML file
    config_reader = YAMLDataReader()
    config = config_reader.read_config(all_files["config"][0])

    output_filename = output_filename or default_filename
    generated_folder = os.path.join(folder_path, "generated")
    os.makedirs(generated_folder, exist_ok=True)
    code = generator.generate(test_cases, modules, elements, config)
    FileWriter().write(generated_folder, output_filename, code, framework)

    FileWriter().copy_input_templates(folder_path, generated_folder)
