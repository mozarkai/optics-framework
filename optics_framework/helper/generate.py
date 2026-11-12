import re
import os
import inspect
import keyword as keyword_module
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from collections.abc import Mapping
from typing import Dict, List, Tuple, Optional, Union, Any, Iterator
import logging
import pandas as pd
import json

from optics_framework.common import safe_fs
from optics_framework.common.config_handler import load_project_yaml
from optics_framework.common.runner.data_reader import (
    CSVDataReader as RunnerCSVReader,
    is_elements_header,
    named_locators,
)
from optics_framework.common.utils import unescape_csv_value

_KWARG = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.DOTALL)


@lru_cache(maxsize=None)
def _sdk_parameters(method_name: str) -> Optional[frozenset]:
    """Parameter names of the Optics SDK method a generated step calls, or None if unknown."""
    try:
        from optics_framework.optics import Optics  # deferred: pulls in the vision stack
    except ImportError:
        return None
    method = getattr(Optics, method_name, None)
    if method is None:
        return None
    try:
        return frozenset(inspect.signature(method).parameters) - {"self"}
    except (TypeError, ValueError):
        return None


def _keyword_argument(param: str, accepted: Optional[frozenset]) -> Optional[Tuple[str, str]]:
    """Split ``name=value`` when ``name`` is a parameter of the target method.

    Locators such as ``css=#login`` or ``text=Login`` also look like ``name=value``
    but are the step's element, so they stay positional. Matching quotes around the
    value are dropped: ``index="2"`` passes the string ``2``.
    """
    match = _KWARG.match(param)
    if not match or (accepted is not None and match.group(1) not in accepted):
        return None
    if not match.group(1).isidentifier() or keyword_module.iskeyword(match.group(1)):
        return None
    name, value = match.groups()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return name, value

TestCaseKey = "Test Cases"

_CONFIG_SECTIONS = ("driver_sources", "elements_sources", "text_detection", "image_detection")
_PYTEST_RESERVED = frozenset(
    {"os", "pytest", "Optics", "load_config", "optics", "CONFIG", "ELEMENTS", "PROJECT_PATH", "EXECUTION_OUTPUT_PATH"}
)
_ROBOT_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_ROBOT_CONTROL_WORDS = frozenset(
    {"FOR", "END", "IF", "ELSE", "ELSE IF", "WHILE", "TRY", "EXCEPT", "FINALLY", "BREAK", "CONTINUE", "RETURN", "VAR", "GROUP"}
)


@dataclass(frozen=True)
class _Arg:
    """One step argument: an element name, a named argument or a positional literal."""

    value: str
    name: str | None = None
    element: bool = False
    looks_named: bool = False


class _Identifiers:
    """Maps free-text project names to unique, valid Python identifiers."""

    def __init__(self, reserved: frozenset = frozenset()):
        self._taken = set(reserved)
        self._assigned: dict[tuple[str, str], str] = {}

    def module(self, name: Any) -> str:
        return self._identifier("module", str(name))

    def test(self, name: Any) -> str:
        return self._identifier("test", f"test {name}")

    def _identifier(self, kind: str, text: str) -> str:
        if (kind, text) not in self._assigned:
            base = _identifier_base(text)
            candidate, n = base, 2
            while candidate in self._taken:
                candidate, n = f"{base}_{n}", n + 1
            self._taken.add(candidate)
            self._assigned[(kind, text)] = candidate
        return self._assigned[(kind, text)]


def _identifier_base(text: str) -> str:
    words = "_".join(unicodedata.normalize("NFKC", text).lower().split())
    base = "".join(c if f"_{c}".isidentifier() else "_" for c in words)
    if not base.isidentifier() or keyword_module.iskeyword(base):
        base = f"_{base}"
    return base


def _plain(value: Any) -> Any:
    """Reduce YAML data to JSON types so every value has a literal form."""
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def _python_literal(value: Any) -> str:
    return repr(_plain(value))


def _robot_cell(value: str) -> str:
    """Encode ``value`` as one Robot Framework data cell that reads back as exactly ``value``."""
    if not value:
        return "${EMPTY}"
    cell = "".join(_ROBOT_ESCAPES.get(c, c if c.isprintable() else _robot_codepoint(c)) for c in value)
    cell = re.sub(r"[$@&%]\{", lambda m: "\\" + m.group(), cell)
    cell = re.sub(r"^ | $|(?<= ) ", r"\\ ", cell)
    if cell[0] in "#|[" or cell == "..." or cell in _ROBOT_CONTROL_WORDS:
        cell = "\\" + cell
    return cell


def _robot_codepoint(char: str) -> str:
    code = ord(char)
    if code < 0x100:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}"


logger = logging.getLogger(__name__)

# Type aliases for clarity
TestCases = Dict[str, List[str]]
Modules = Dict[str, List[Tuple[str, List[str]]]]
Elements = Dict[str, Union[str, List[str]]]
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
        param_columns = [column for column in df.columns if str(column).startswith("param_")]
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
                        self._format_param_value(row[column])
                        for column in param_columns
                        if pd.notna(row[column]) and str(row[column]).strip()
                    ],
                )
                steps.append(step)
            modules[str(module_name).strip()] = steps
        return modules

    def _format_param_value(self, value) -> str:
        """Format parameter value, preserving the original format from CSV."""
        return unescape_csv_value(str(value).strip())

    def read_elements(self, source: str) -> Elements:
        """Each element's locators in column order, as a list when there are several: a keyword tries each in turn."""
        return {
            name: ids if len(ids) > 1 else next(iter(ids), "")
            for name, ids in named_locators(RunnerCSVReader().read_file(source)).items()
        }

    def read_config(self, source: str) -> Config:
        return load_project_yaml(source) or {}


class YAMLDataReader(DataReader):
    """Reader for YAML-based test data."""

    def read_test_cases(self, source: str) -> TestCases:
        data = load_project_yaml(source) or {}
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
        data = load_project_yaml(source) or {}
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
            "Go Back",
            "Clear Element Text",
            "Get Text",
            "Sleep",
            "Validate Element",
            "Assert Presence",
            "Assert Visibility",
            "Validate Screen",
            "Assert Text",
            "Get Interactive Elements",
            "Capture Screenshot",
            "Capture Pagesource",
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
        data = load_project_yaml(source) or {}
        return data.get("Elements", {})

    def read_config(self, source: str) -> Config:
        return load_project_yaml(source) or {}


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
            "Go Back": "go_back",
            "Clear Element Text": "clear_element_text",
            "Get Text": "get_text",
            "Sleep": "sleep",
            "Validate Element": "validate_element",
            "Assert Presence": "assert_presence",
            "Assert Visibility": "assert_visibility",
            "Validate Screen": "validate_screen",
            "Assert Text": "assert_text",
            "Get Interactive Elements": "get_interactive_elements",
            "Capture Screenshot": "capture_screenshot",
            "Capture Pagesource": "capture_pagesource",
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

    def _method_name(self, keyword: str) -> str:
        method = self.keyword_registry.get(keyword, "_".join(keyword.lower().split()))
        if not method.isidentifier() or keyword_module.iskeyword(method) or method.startswith("_"):
            raise ValueError(f"'{keyword}' is not a keyword name.")
        return method

    def _arguments(
        self, params: list[str], elements: Mapping[str, Any], method_name: str
    ) -> list[_Arg]:
        accepted = _sdk_parameters(method_name)
        kwargs = [_keyword_argument(param, accepted) for param in params]
        # Named arguments can only follow the positional ones, so only a trailing run
        # counts: in `Enter Text text=Email hello`, `text=Email` is the element.
        first_named = len(params)
        while first_named and kwargs[first_named - 1]:
            first_named -= 1
        return [
            _Arg(kwarg[1], name=kwarg[0]) if i >= first_named and kwarg
            else _positional(param, elements, looks_named=bool(kwarg) and accepted is not None)
            for i, (param, kwarg) in enumerate(zip(params, kwargs))
        ]


def _positional(param: str, elements: Mapping[str, Any], looks_named: bool) -> _Arg:
    if param.startswith("${") and param.endswith("}"):
        var_name = param[2:-1]
        if var_name not in elements:
            raise ValueError(f"Element '{var_name}' not found in elements.")
        return _Arg(var_name, element=True)
    return _Arg(param, looks_named=looks_named)


_PYTEST_HEADER = """# Auto-generated by generate.py. Do not edit manually.
import os
import pytest
from optics_framework.optics import Optics
from optics_framework.common.utils import load_config
"""
_PYTEST_SETUP = """@pytest.fixture(scope='module')
def optics():
    optics = Optics()
    optics.setup(config=CONFIG)
    yield optics
    optics.quit()
"""


class PytestGenerator(TestFrameworkGenerator):
    """Generator for pytest-compatible test code."""

    def generate(
        self,
        test_cases: TestCases,
        modules: Modules,
        elements: Elements,
        config: Config,
    ) -> str:
        names = _Identifiers(_PYTEST_RESERVED)
        code_parts = [
            _PYTEST_HEADER,
            self._generate_config(config),
            self._generate_elements(elements),
            _PYTEST_SETUP,
            "# Module functions\n",
        ]
        for module_name, steps in modules.items():
            code_parts.append(
                self._generate_module_function(names.module(module_name), steps, elements)
            )
        code_parts.append("# Test functions\n")
        for test_case_name, module_names in test_cases.items():
            code_parts.append(
                self._generate_test_function(
                    names.test(test_case_name), [names.module(m) for m in module_names]
                )
            )
        return "\n".join(code_parts)

    def _generate_config(self, config: Config) -> str:
        return "\n".join(
            [
                "# Get project path and setup execution output path",
                "PROJECT_PATH = os.path.dirname(os.path.abspath(__file__))",
                "EXECUTION_OUTPUT_PATH = os.environ.get('EXEC_OUTPUT', os.path.join(PROJECT_PATH, 'execution_outputs'))",
                "os.makedirs(EXECUTION_OUTPUT_PATH, exist_ok=True)",
                "",
                "CONFIG = {",
                *(f"    {key!r}: {_python_literal(config.get(key) or [])}," for key in _CONFIG_SECTIONS),
                "    'execution_output_path': EXECUTION_OUTPUT_PATH,",
                "    'project_path': PROJECT_PATH,",
                "    'event_attributes_json': os.environ.get('MOZARK_ATTRIBUTES_JSON'),",
                "}\n",
                "# Override with environment values if available",
                "CONFIG = load_config(CONFIG)",
                "",
            ]
        )

    def _generate_elements(self, elements: Elements) -> str:
        lines = ["ELEMENTS = {"]
        for name, value in elements.items():
            lines.append(f"    {_python_literal(name)}: {_python_literal(value)},")
        lines.append("}\n")
        return "\n".join(lines)

    def _resolve_params(self, params: list[str], elements: Elements, method_name: str) -> list[str]:
        return [self._render(arg) for arg in self._arguments(params, elements, method_name)]

    @staticmethod
    def _render(arg: _Arg) -> str:
        if arg.element:
            return f"ELEMENTS[{_python_literal(arg.value)}]"
        if arg.name:
            return f"{arg.name}={_python_literal(arg.value)}"
        return _python_literal(arg.value)

    def _generate_module_function(
        self, func_name: str, steps: List[Tuple[str, List[str]]], elements: Elements
    ) -> str:
        lines = [f"def {func_name}(optics: Optics) -> None:"]
        for keyword, params in steps:
            method_name = self._method_name(keyword)
            param_str = ", ".join(self._resolve_params(params, elements, method_name))
            lines.append(f"    optics.{method_name}({param_str})")
        return "\n".join(lines) + "\n"

    def _generate_test_function(self, func_name: str, module_funcs: list[str]) -> str:
        lines = [f"def {func_name}(optics):"]
        lines.extend(f"    {module_func}(optics)" for module_func in module_funcs)
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
        names = _Identifiers()
        keys = {name: names.module(name) for name in elements}
        code_parts = [
            self._generate_header(),
            self._generate_variables(elements, keys, config),
            self._generate_test_cases(test_cases),
            self._generate_keywords(modules, keys),
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
        """Keep the config sections Optics setup reads, dropping empty optional ones."""
        transformed = {
            "driver_sources": config.get('driver_sources', []),
            "elements_sources": config.get('elements_sources', []),
        }
        if config.get('image_detection'):
            transformed["image_detection"] = config.get('image_detection', [])
        if config.get('text_detection'):
            transformed["text_detection"] = config.get('text_detection', [])
        if config.get('execution_output_path'):
            transformed["execution_output_path"] = config.get('execution_output_path')
        return transformed

    def _generate_variables(self, elements: Elements, keys: dict[str, str], config: Config) -> str:
        lists = {name: f"LOCATORS_{n}" for n, name in enumerate(
            (name for name, value in elements.items() if isinstance(value, list)), 1)}
        lines = ["*** Variables ***"]
        lines.extend(f"@{{{var}}}=    " + "    ".join(map(_robot_cell, map(str, elements[name])))
                     for name, var in lists.items())
        lines.append("&{ELEMENTS}=")
        for name, value in elements.items():
            if name in lists:
                cell = f"${{{lists[name]}}}"
            elif value:
                cell = _robot_cell(str(value))
            else:
                cell = ""
            lines.append(f"...    {keys[name]}={cell}")
        lines.append("")
        config_json = json.dumps(_plain(self._transform_config_structure(config)), separators=(',', ':'))
        lines.extend([f"${{OPTICS_CONFIG_JSON}}=    {_robot_cell(config_json)}", ""])
        return "\n".join(lines)

    def _generate_test_cases(self, test_cases: TestCases) -> str:
        lines = ["*** Test Cases ***"]
        for test_case_name, module_names in test_cases.items():
            lines.append(_robot_cell(str(test_case_name)))
            lines.append("    [Setup]    Setup Optics")
            lines.extend(f"    {_robot_cell(str(module_name))}" for module_name in module_names)
            lines.append("    [Teardown]    Quit Optics")
            lines.append("")
        return "\n".join(lines)

    def _resolve_params(self, params: list[str], keys: dict[str, str], method_name: str) -> list[str]:
        return [self._render(arg, keys) for arg in self._arguments(params, keys, method_name)]

    @staticmethod
    def _render(arg: _Arg, keys: dict[str, str]) -> str:
        if arg.element:
            return f"${{ELEMENTS}}[{keys[arg.value]}]"
        if arg.name:
            return f"{arg.name}={_robot_cell(arg.value)}"
        cell = _robot_cell(arg.value)
        # The name part is an identifier, so the first "=" is the one Robot would split on.
        return cell.replace("=", "\\=", 1) if arg.looks_named else cell

    def _generate_keywords(self, modules: Modules, keys: dict[str, str]) -> str:
        lines = [
            "*** Keywords ***",
            "Setup Optics",
            "    ${config}=    BuiltIn.Evaluate    json.loads($OPTICS_CONFIG_JSON)    modules=json",
            "    Set To Dictionary    ${config}    project_path=${EXECDIR}",
            "    Setup    config=${config}",
            "",
            "Quit Optics",
            "    Quit",
            "",
        ]
        for module_name, steps in modules.items():
            lines.append(_robot_cell(str(module_name)))
            for keyword, params in steps:
                cells = [_robot_cell(" ".join(keyword.split()))]
                cells.extend(self._resolve_params(params, keys, self._method_name(keyword)))
                lines.append("    " + "    ".join(cells))
            lines.append("")
        return "\n".join(lines)


class FileWriter:
    """Writes generated code and requirements under a generated folder, never through a symlink."""

    def write(
        self, folder_path: str, filename: str, content: str, framework: str
    ) -> None:
        output_file = safe_fs.write_text(
            os.path.join(folder_path, "Tests", filename), content, root=folder_path
        )
        logger.info(f"Generated test file: {output_file}")
        requirements = [
            "optics-framework",
            "Appium-Python-Client",
            "pytest" if framework == "pytest" else "robotframework",
            "easyocr",
            "pyserial",
            "pytest-tagging",
        ]
        requirements_file = safe_fs.write_text(
            os.path.join(folder_path, "requirements.txt"), "\n".join(requirements) + "\n", root=folder_path
        )
        logger.info(f"Generated requirements file: {requirements_file}")

    def copy_input_templates(self, source_folder: str, generated_folder: str) -> None:
        """Copy ``input_templates`` into ``generated/Tests/`` if the project has one."""
        input_templates_path = os.path.join(source_folder, "input_templates")
        if not os.path.isdir(input_templates_path):
            logger.debug("No input_templates folder found - skipping copy")
            return
        try:
            destination = safe_fs.replace_tree(
                input_templates_path, os.path.join(generated_folder, "Tests", "input_templates"), generated_folder
            )
        except OSError as e:
            logger.exception(f"Failed to copy input_templates folder: {e}")
            return
        logger.info(f"Copied input_templates folder to: {destination}")


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
        if is_elements_header(set(headers)):
            return "csv", "elements"
    except Exception as e:
        logger.exception(f"Error reading CSV {file_path}: {e}")
    return None


def _detect_yaml_type(file_path: str) -> Optional[Tuple[str, str]]:
    try:
        data = load_project_yaml(file_path) or {}
        if os.path.basename(file_path) == "config.yaml":
            return "yaml", "config"
        if TestCaseKey in data:
            return "yaml", "test_cases"
        if "Modules" in data:
            return "yaml", "modules"
        if "Elements" in data:
            return "yaml", "elements"
    except Exception as e:
        logger.exception(f"Error reading YAML {file_path}: {e}")
    return None


def _assign_yaml_files(yaml_files, files):
    """Assign YAML files to test_cases, modules, and elements if not already assigned."""
    for yaml_file in yaml_files:
        data = load_project_yaml(yaml_file) or {}
        for content_type in ["test_cases", "modules", "elements"]:
            key = (
                "Test Cases"
                if content_type == "test_cases"
                else content_type.capitalize()
            )
            if key in data and not files[content_type]:
                files[content_type] = yaml_file

# Directories that never hold source project files — skipped when scanning so a
# recursive walk doesn't pick up generated code, run artefacts or caches.
_SKIP_SCAN_DIRS = {"__pycache__", "generated", "execution_output"}


def _iter_project_files(folder_path: str) -> Iterator[Tuple[str, str, str]]:
    """Yield ``(file_path, file_format, content_type)`` for every recognised file.

    Walks ``folder_path`` recursively so files organised into subfolders
    (``test_cases/``, ``modules/``, ``test_data/`` — the layout ``optics init``
    scaffolds) are discovered, not only files sitting at the project root. Caches,
    generated code and run artefacts are skipped.
    """
    for root, dirs, files in os.walk(folder_path):
        dirs[:] = [
            d for d in dirs if d not in _SKIP_SCAN_DIRS and not d.startswith(".")
        ]
        for name in files:
            file_path = os.path.join(root, name)
            result = detect_file_type(file_path)
            if not result:
                continue
            file_format, content_type = result
            yield file_path, file_format, content_type


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

    for file_path, file_format, content_type in _iter_project_files(folder_path):
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

    for file_path, file_format, content_type in _iter_project_files(folder_path):
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
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Find all files for mixed CSV/YAML support
    all_files = find_all_files(folder_path)

    required = {
        "config.yaml": all_files["config"],
        "test cases file": all_files["test_cases"],
        "modules file": all_files["modules"],
        "elements file": all_files["elements"],
    }
    missing = [label for label, found in required.items() if not found]
    if missing:
        raise ValueError(
            f"Cannot generate: missing {', '.join(missing)} in {folder_path}. "
            "Run `optics init <name>` to scaffold a complete project, or check the path."
        )

    if framework == "pytest":
        generator = PytestGenerator()
        default_filename = f"test_{os.path.basename(folder_path)}.py"
    elif framework == "robot":
        generator = RobotGenerator()
        default_filename = f"test_{os.path.basename(folder_path)}.robot"
    else:
        raise ValueError(
            f"Unsupported framework: {framework}. Use 'pytest' or 'robot'.")

    # Read and merge data from all files
    test_cases = read_mixed_data(all_files["test_cases"], "test_cases")
    modules = read_mixed_data(all_files["modules"], "modules")
    elements = read_mixed_data(all_files["elements"], "elements")

    # Config is always single YAML file
    config_reader = YAMLDataReader()
    config = config_reader.read_config(all_files["config"][0])

    output_filename = output_filename or default_filename
    generated_folder = str(safe_fs.make_dirs("generated", folder_path))
    code = generator.generate(test_cases, modules, elements, config)
    FileWriter().write(generated_folder, output_filename, code, framework)

    FileWriter().copy_input_templates(folder_path, generated_folder)
