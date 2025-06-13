import csv
import yaml
import re
from abc import ABC, abstractmethod
from typing import Optional, Dict, Union, List, Tuple
from optics_framework.common.logging_config import internal_logger


class DataReader(ABC):
    """Abstract base class for reading data from various file formats."""

    @abstractmethod
    def read_file(self, file_path: str) -> Union[list, dict]:
        """
        Read a file and return its contents as a list of dictionaries (CSV) or a dictionary (YAML).

        :param file_path: Path to the file.
        :type file_path: str
        :return: A list of dictionaries (CSV) or a dictionary (YAML) representing the file contents.
        :rtype: Union[list, dict]
        """
        pass

    @staticmethod
    def get_keyword_params(param_strings: List[str]) -> Dict[str, str]:
        """
        Parses a list of params (string) and filters only keyword params (i.e in format key=value) and returns them as a Dictionary
        """
        args = {}
        for param in param_strings:
            if "=" in param:
                arg_name, value = param.split("=", 1)
                args[arg_name.strip()] = value.strip()
        return args

    @staticmethod
    def get_positional_params(param_strings: List[str]) -> List[str]:
        """
        Parses a list of params (string) and returns a list of positional params (i.e not in key=value format)
        """
        args = []
        for param in param_strings:
            if "=" not in param:
                args.append(param.strip())
        return args

    @abstractmethod
    def read_test_cases(self, file_path: str) -> dict:
        """
        Read a file containing test cases and return a dictionary mapping
        each test case to its list of test steps.

        :param file_path: Path to the file.
        :type file_path: str
        :return: A dictionary where keys are test case names and values are lists of test steps.
        :rtype: dict
        """
        pass

    @abstractmethod
    def read_modules(self, file_path: str) -> dict:
        """
        Read a file containing module information and return a dictionary mapping
        module names to lists of tuples (module_step, params).

        :param file_path: Path to the file.
        :type file_path: str
        :return: A dictionary where keys are module names and values are lists of (module_step, params) tuples.
        :rtype: dict
        """
        pass

    @abstractmethod
    def read_elements(self, file_path: Optional[str]) -> dict:
        """
        Read a file containing element information and return a dictionary mapping
        element names to their corresponding element IDs.

        :param file_path: Path to the file, or None if elements are not provided.
        :type file_path: Optional[str]
        :return: A dictionary where keys are element names and values are element IDs.
        :rtype: dict
        """
        pass


class CSVDataReader(DataReader):
    """Concrete implementation of DataReader for CSV files."""

    def read_file(self, file_path: str) -> list:
        """
        Read a CSV file and return its contents as a list of dictionaries.

        :param file_path: Path to the CSV file.
        :type file_path: str
        :return: A list of dictionaries representing the CSV rows.
        :rtype: list
        """
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)

    def read_test_cases(self, file_path: str) -> dict:
        """
        Read a CSV file containing test cases and return a dictionary mapping
        each test case to its list of test steps.

        :param file_path: Path to the test_cases CSV file.
        :type file_path: str
        :return: A dictionary where keys are test case names and values are lists of test steps.
        :rtype: dict
        """
        rows = self.read_file(file_path)
        test_cases = {}
        for row in rows:
            test_case = row.get("test_case", "").strip()
            test_step = row.get("test_step", "").strip()
            if not test_case or not test_step:
                continue
            if test_case not in test_cases:
                test_cases[test_case] = []
            test_cases[test_case].append(test_step)
        return test_cases

    def read_modules(self, file_path: str) -> dict:
        """
        Read a CSV file containing module information and return a dictionary mapping
        module names to lists of tuples (module_step, params).

        :param file_path: Path to the modules CSV file.
        :type file_path: str
        :return: A dictionary where keys are module names and values are lists of (module_step, params) tuples.
        :rtype: dict
        """
        rows = self.read_file(file_path)
        modules = {}
        for row in rows:
            if "module_name" not in row or not row["module_name"]:
                internal_logger.warning(f"Warning: Row missing 'module_name': {row}")
                continue
            if "module_step" not in row or not row["module_step"]:
                internal_logger.warning(f"Warning: Row missing 'module_step': {row}")
                continue
            module_name = row["module_name"].strip()
            keyword = row["module_step"].strip()
            params = [
                row[key].strip()
                for key in row
                if key is not None
                and key.startswith("param_")
                and row[key]
                and row[key].strip()
            ]
            if module_name not in modules:
                modules[module_name] = []
            modules[module_name].append((keyword, params))
        return modules

    def read_elements(self, file_path: Optional[str]) -> dict:
        """
        Read a CSV file containing element information and return a dictionary mapping
        element names to their corresponding element IDs.

        :param file_path: Path to the elements CSV file, or None if not provided.
        :type file_path: Optional[str]
        :return: A dictionary where keys are element names and values are element IDs.
        :rtype: dict
        """
        if not file_path:
            return {}
        rows = self.read_file(file_path)
        elements = {}
        for row in rows:
            element_name = row.get("Element_Name", "").strip()
            element_id = row.get("Element_ID", "").strip()
            if element_name and element_id:
                elements[element_name] = element_id
        return elements


class YAMLDataReader(DataReader):
    """Concrete implementation of DataReader for YAML files."""

    def read_file(self, file_path: str) -> dict:
        """
        Read a YAML file and return its contents as a dictionary.

        :param file_path: Path to the YAML file.
        :type file_path: str
        :return: A dictionary representing the YAML content.
        :rtype: dict
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                return data
        except yaml.YAMLError as e:
            internal_logger.error(f"Error parsing YAML file {file_path}: {e}")
            return {}

    def read_test_cases(self, file_path: str) -> dict:
        """
        Read a YAML file containing test cases and return a dictionary mapping
        each test case to its list of test steps.

        :param file_path: Path to the YAML file.
        :type file_path: str
        :return: A dictionary where keys are test case names and values are lists of test steps.
        :rtype: dict
        """
        data = self.read_file(file_path)
        test_cases = {}
        test_cases_data = data.get("Test Cases", [])
        for test_case in test_cases_data:
            for name, steps in test_case.items():
                name = name.strip()
                if not name or not steps:
                    continue
                test_cases[name] = [step.strip() for step in steps if step.strip()]
        return test_cases

    def _parse_module_step(self, step: str) -> Tuple[str, List[str]]:
        """
        Parse a module step to extract the keyword and parameters.

        :param step: The module step string.
        :return: Tuple of (keyword, list of parameters).
        """
        step = step.strip()
        if not step:
            return "", []

        param_pattern = re.compile(r"\${[^{}]+}")
        params = param_pattern.findall(step)
        if not params:
            return step, []

        param_start = step.index(params[0])
        keyword = step[:param_start].strip()
        param_str = step[param_start:].strip()
        param_parts = param_str.split()
        return keyword, [p.strip() for p in param_parts if p.strip()]

    def _process_module_steps(self, steps: List[str]) -> List[Tuple[str, List[str]]]:
        """
        Process a list of module steps into a list of (keyword, params) tuples.

        :param steps: List of step strings.
        :return: List of (keyword, params) tuples.
        """
        module_steps = []
        for step in steps:
            keyword, params = self._parse_module_step(step)
            if keyword:
                module_steps.append((keyword, params))
        return module_steps

    def read_modules(self, file_path: str) -> Dict[str, List[Tuple[str, List[str]]]]:
        """
        Read a YAML file containing module information and return a dictionary mapping
        module names to lists of tuples (module_step, params).

        :param file_path: Path to the YAML file.
        :type file_path: str
        :return: A dictionary where keys are module names and values are lists of (module_step, params) tuples.
        :rtype: dict
        """
        data = self.read_file(file_path)
        modules = {}
        modules_data = data.get("Modules", [])

        for module in modules_data:
            for name, steps in module.items():
                name = name.strip()
                if not name or not steps:
                    internal_logger.warning(
                        f"Warning: Module '{name}' is empty or invalid"
                    )
                    continue
                modules[name] = self._process_module_steps(steps)

        return modules

    def read_elements(self, file_path: Optional[str]) -> dict:
        """
        Read a YAML file containing element information and return a dictionary mapping
        element names to their corresponding element IDs.

        :param file_path: Path to the YAML file, or None if not provided.
        :type file_path: Optional[str]
        :return: A dictionary where keys are element names and values are element IDs.
        :rtype: dict
        """
        if not file_path:
            return {}
        data = self.read_file(file_path)
        elements = {}
        elements_data = data.get("Elements", {})
        for name, value in elements_data.items():
            name = name.strip()
            value = str(value).strip() if value is not None else ""
            if name and value:
                elements[name] = value
        return elements


def merge_dicts(dict1: Dict, dict2: Dict, data_type: str) -> Dict:
    """
    Merge two dictionaries, logging warnings for duplicate keys.

    :param dict1: First dictionary.
    :param dict2: Second dictionary.
    :param data_type: Type of data (e.g., 'test_cases', 'modules', 'elements') for logging.
    :return: Merged dictionary.
    """
    merged = dict1.copy()
    for key, value in dict2.items():
        if key in merged:
            internal_logger.warning(
                f"Duplicate {data_type} key '{key}' found. Keeping value from second source.")
        merged[key] = value
    return merged
