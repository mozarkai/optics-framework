import csv
from abc import ABC, abstractmethod
from typing import Optional
from optics_framework.common.logging_config import logger, use_logger_format


class DataReader(ABC):
    """Abstract base class for reading data from various file formats."""

    @abstractmethod
    def read_file(self, file_path: str) -> list:
        """
        Read a file and return its contents as a list of dictionaries.

        :param file_path: Path to the file.
        :type file_path: str
        :return: A list of dictionaries representing the file rows.
        :rtype: list
        """
        pass

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

        Each row in the CSV file is converted to a dictionary where the keys are
        the column headers and the values are the corresponding row values.

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

        The CSV file is expected to have at least two columns: 'test_case' and 'test_step'.
        Each row with non-empty values for both keys is processed.

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

    @use_logger_format("user")
    def read_modules(self, file_path: str) -> dict:
        """
        Read a CSV file containing module information and return a dictionary mapping
        module names to lists of tuples. Each tuple consists of a module step and its parameters.

        The CSV file is expected to have the columns 'module_name' and 'module_step'.
        Additional columns starting with "param_" are treated as parameters.
        Logs a warning if required fields are missing.

        :param file_path: Path to the modules CSV file.
        :type file_path: str
        :return: A dictionary where keys are module names and values are lists of (module_step, params) tuples.
        :rtype: dict
        """


        rows = self.read_file(file_path)
        modules = {}
        for row in rows:
            if "module_name" not in row or not row["module_name"]:
                logger.warning(f"Warning: Row missing 'module_name': {row}\n")
                continue
            if "module_step" not in row or not row["module_step"]:
                logger.warning(f"Warning: Row missing 'module_step': {row}\n")
                continue
            module_name = row["module_name"].strip()
            keyword = row["module_step"].strip()
            params = [
                row[key].strip()
                for key in row
                if key is not None  # Add this check
                and key.startswith("param_")
                and row[key]  # Ensure the value is not empty or None
                and row[key].strip()  # Ensure it’s not just whitespace
            ]
            if module_name not in modules:
                modules[module_name] = []
            modules[module_name].append((keyword, params))
        return modules

    def read_elements(self, file_path: Optional[str]) -> dict:
        """
        Read a CSV file containing element information and return a dictionary mapping
        element names to their corresponding element IDs. Returns an empty dict if file_path is None.

        The CSV file is expected to have columns 'Element_Name' and 'Element_ID'.

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
