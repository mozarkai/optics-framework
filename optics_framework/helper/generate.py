import os
from abc import ABC, abstractmethod
from typing import Dict, List, Literal, Tuple, Optional, Union
import logging
import pandas as pd
import yaml

TestCaseKey = Literal["Test Cases"]

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
        df = pd.read_csv(source)
        modules = {}
        for module_name in df["module_name"].unique():
            module_df = df[df["module_name"] == module_name]
            steps = [
                (
                    row["module_step"].strip(),
                    [
                        str(row[f"param_{i + 1}"]).strip()
                        for i in range(len(row) - 2)
                        if pd.notna(row[f"param_{i + 1}"])
                    ],
                )
                for _, row in module_df.iterrows()
            ]
            modules[module_name.strip()] = steps
        return modules

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
            for reg_keyword in keyword_registry:
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
        output_path: str,
    ) -> str:
        pass

    def _resolve_params(
        self, params: List[str], elements: Elements, framework: str
    ) -> List[str]:
        resolved = []
        for param in params:
            if param.startswith("${") and param.endswith("}"):
                var_name = param[2:-1]
                if var_name not in elements:
                    raise ValueError(f"Element '{var_name}' not found in elements.")
                if framework == "pytest":
                    resolved.append(f"ELEMENTS['{var_name}']")
                else:  # robot
                    resolved.append(f"${{ELEMENTS.{var_name}}}")
            else:
                resolved.append(f"'{param}'" if framework == "pytest" else param)
        return resolved


class PytestGenerator(TestFrameworkGenerator):
    """Generator for pytest-compatible test code."""

    def generate(
        self,
        test_cases: TestCases,
        modules: Modules,
        elements: Elements,
        config: Config,
        output_path: str,
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
                "import pytest",
                "from optics_framework.optics import Optics",
                "from optics_framework.common.utils import load_config",
                "",
            ]
        )

    def _generate_config(self, config: Config) -> str:
        return "\n".join(
            [
                "CONFIG = {",
                f"    'driver_config': {config.get('driver_sources', [])},"
                if config.get("driver_sources")
                else "    'driver_config': [],",
                f"    'element_source_config': {config.get('elements_sources', [])},"
                if config.get("elements_sources")
                else "    'element_source_config': [],",
                f"    'text_detection': {config.get('text_detection', [])},"
                if config.get("text_detection")
                else "    'text_detection': [],",
                f"    'image_detection': {config.get('image_detection', [])},"
                if config.get("image_detection")
                else "    'image_detection': [],",
                f"    'execution_output_path': {config.get('execution_output_path')},"
                if config.get("execution_output_path")
                else "    'execution_output_path': None,",
                "}\n",
                "# Override with environment values if available",
                "CONFIG = load_config(CONFIG)",
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
                "    optics.setup(",
                "        driver_config=CONFIG['driver_config'],",
                "        element_source_config=CONFIG['element_source_config'],",
                "        image_config=CONFIG['image_detection'],",
                "        text_config=CONFIG['text_detection'],",
                "        execution_output_path=CONFIG.get('execution_output_path', None),",
                "    )",
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
        output_path: str,
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

    def _generate_variables(self, elements: Elements, config: Config) -> str:
        lines = ["*** Variables ***", "# Element dictionary", "&{ELEMENTS}="]
        for name, value in elements.items():
            lines.append(f"...    {name}={value}")
        lines.append("")
        lines.extend(
            [
                "# Driver configuration as a single-line list",
                f"${{DRIVER_CONFIG_LIST}}=    {config.get('driver_sources', [])}",
                "",
                "# Element source configuration as a single-line list",
                f"${{ELEMENT_SOURCE_CONFIG_LIST}}=    {config.get('elements_sources', [])}",
                "",
                "# Image detection configuration as a single-line list",
                f"${{IMAGE_DETECTION_CONFIG_LIST}}=    {config.get('image_detection', [])}",
                "",
                "# Text detection configuration as a single-line list",
                f"${{TEXT_DETECTION_CONFIG_LIST}}=    {config.get('text_detection', [])}",
                "",
            ]
        )
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
        lines = ["*** Keywords ***", "Setup Optics"]
        lines.append(
            "    Setup    ${DRIVER_CONFIG_LIST}    ${ELEMENT_SOURCE_CONFIG_LIST}    ${IMAGE_DETECTION_CONFIG_LIST}    ${TEXT_DETECTION_CONFIG_LIST}"
        )
        lines.append("")
        lines.append("Quit Optics")
        lines.append("    Quit")
        lines.append("")
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
            "optics-framework",
            "Appium-Python-Client",
            "pytest" if framework == "pytest" else "robotframework",
        ]
        requirements_file = os.path.join(folder_path, "requirements.txt")
        with open(requirements_file, "w", encoding="utf-8") as f:
            f.write("\n".join(requirements) + "\n")
        logging.info(f"Generated requirements file: {requirements_file}")


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
            if data.get(key) and not files[content_type]:
                files[content_type] = yaml_file

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

    test_cases_file, modules_file, elements_file, config_file = find_files(folder_path)

    if not config_file:
        logging.error("Error: Missing config.yaml")
        return
    if not test_cases_file:
        logging.error("Error: Missing test cases file")
        return
    if not modules_file:
        logging.error("Error: Missing modules file")
        return
    if not elements_file:
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

    # Initialize readers based on file formats
    test_cases_reader = (
        CSVDataReader()
        if test_cases_file and test_cases_file.endswith(".csv")
        else YAMLDataReader()
    )
    modules_reader = (
        CSVDataReader()
        if modules_file and modules_file.endswith(".csv")
        else YAMLDataReader()
    )
    elements_reader = (
        CSVDataReader()
        if elements_file and elements_file.endswith(".csv")
        else YAMLDataReader()
    )
    config_reader = YAMLDataReader()  # Config is always YAML

    # Read data
    test_cases = test_cases_reader.read_test_cases(test_cases_file)
    modules = modules_reader.read_modules(modules_file)
    elements = elements_reader.read_elements(elements_file)
    config = config_reader.read_config(config_file)

    output_filename = output_filename or default_filename
    generated_folder = os.path.join(folder_path, "generated")
    os.makedirs(generated_folder, exist_ok=True)
    code = generator.generate(test_cases, modules, elements, config, generated_folder)
    FileWriter().write(generated_folder, output_filename, code, framework)
