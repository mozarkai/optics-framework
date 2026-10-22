import csv
import yaml
import re
import inspect
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Callable, Optional, Dict, Set, Union, List, Tuple, cast
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.models import (
    ApiData,
    ApiDefinition,
    ExpectedResultDefinition,
)
from optics_framework.common.utils import unescape_csv_value


# A quoted run is one token. Applied to the whole step, so a locator keeps the quotes inside
# it (`//button[@id="save"]`) while a value written as one (`text="a b"`) gives them up.
_TOKEN = re.compile(r'''(?:[^\s"']|"[^"]*"|'[^']*')+''')
_WRAPPED = re.compile(r'''^(?P<q>["'])(?P<body>.*)(?P=q)$''', re.S)


def _unbalanced_quote(step: str) -> bool:
    """Whether the token pattern would skip a non-whitespace character, which only an
    unpaired quote makes it do."""
    gap = 0
    for match in _TOKEN.finditer(step):
        if step[gap : match.start()].strip():
            return True
        gap = match.end()
    return bool(step[gap:].strip())


def _tokens(step: str) -> List[str]:
    """Whitespace-split, except that a quoted run holds together and a value written entirely
    in quotes is unwrapped — which is the only way a param can contain a space.

    Unwrapped whatever the value holds, not only when it holds a space: an editor writing
    every param as `name="value"` would otherwise hand the keyword the quotes as well, and
    `index="2"` would arrive as the string `"2"`.

    An unbalanced quote falls back to `.split()`: the pattern would drop the quote and split
    anyway, so `text=it's fine` keeps reading as it always has."""
    if _unbalanced_quote(step):
        return step.split()

    out = []
    for token in _TOKEN.findall(step):
        key, sep, value = token.partition("=")
        found = _WRAPPED.match(value if sep else token)
        if not found:
            out.append(token)
        elif sep:
            out.append(f"{key}={found['body']}")
        else:
            out.append(found["body"])
    return out


def _keyword_slug(name: str) -> str:
    return "_".join(name.replace("_", " ").split()).lower()


# The SDK/Robot facade (optics.py) exposes two keyword names the runtime map does not:
# an alias of press_element and the session teardown. They still belong in the catalogue,
# so a suite using one reports the unknown keyword instead of dispatching the words after
# it to the shorter runtime keyword that shares their prefix. Their arity is written out
# because reflection only walks the api classes.
_FACADE_KEYWORD_SLUGS = {"press_element_with_index": 3, "quit": 0}


def _arity(method) -> Optional[int]:
    """How many params a keyword takes, or nothing when it takes any number. Unreadable
    signatures answer "any number" so a keyword is never rejected for the wrong reason."""
    try:
        parameters = list(inspect.signature(method).parameters.values())
    except (TypeError, ValueError):
        return None

    count = 0
    for parameter in parameters:
        if parameter.name == "self":
            continue
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return None
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            count += 1
    return count


def _catalogue_classes(package) -> List[type]:
    """Every public class the package's modules define (not merely re-export)."""
    import importlib
    import pkgutil

    classes = []
    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{package.__name__}.{module_name}")
        for class_name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__ and not class_name.startswith("_"):
                classes.append(cls)
    return classes


def _class_keyword_entries(cls) -> Dict[str, Optional[int]]:
    """Every public callable a class exposes, against the number of params it takes."""
    entries: Dict[str, Optional[int]] = {}
    for attr in dir(cls):
        if attr.startswith("_"):
            continue
        method = getattr(cls, attr)
        if callable(method):
            entries[attr] = _arity(method)
    return entries


@lru_cache(maxsize=1)
def _keyword_catalogue() -> Dict[str, Optional[int]]:
    """Every keyword slug the runtime registers, against the number of params it takes."""
    import optics_framework.api

    catalogue: Dict[str, Optional[int]] = {}
    for cls in _catalogue_classes(optics_framework.api):
        catalogue.update(_class_keyword_entries(cls))
    catalogue.update(_FACADE_KEYWORD_SLUGS)
    return catalogue


def _keyword_names() -> frozenset:
    return frozenset(_keyword_catalogue())


def _matched_module_name(step: str, module_names: Set[str]) -> Optional[str]:
    if step in module_names:
        return step
    step_slug = _keyword_slug(step)
    for name in sorted(module_names, key=str):
        name = str(name)
        if "${" not in name and _keyword_slug(name) == step_slug:
            return name
    return None


def _split_at_variable(step: str) -> Optional[Tuple[str, List[str]]]:
    """The legacy split, for a step the catalogue cannot claim: the keyword is everything
    before the first token holding a `${...}`.

    Split on the token and not on the `${` itself, or `Press Element text=Login
    timeout=${t}` is cut through the middle and `text=Login timeout=` is read as part of
    the keyword name. Params are left exactly as `_tokens` returned them, so a quoted
    value keeps the spaces it was quoted for."""
    words = _tokens(step)
    for index, word in enumerate(words):
        if "${" in word:
            return " ".join(words[:index]), words[index:]
    return None


# A word that could be part of a keyword's name: letters, and nothing else. A param that
# is a number, a `${...}`, a `name=value`, a quoted value or a locator is none of these.
_NAME_WORD = re.compile(r"^[A-Za-z][A-Za-z_]*$")


def _mistyped(name: str, params: List[str]) -> bool:
    """Whether a catalogue match is really the front half of a misspelt longer keyword.

    `Swipe By Percent ${x} ${y}` matches `swipe`, leaving `By` and `Percent` as params —
    which the runner would then dispatch, silently doing the wrong thing instead of
    reporting an unknown keyword. Two exact signals, no fuzzy matching, and both need the
    next param to be a bare word: the word continues a name the catalogue knows
    (`swipe by` -> `swipe by percentage`), or the match cannot hold this many params
    (`Scroll To Element foo` gives `scroll` three, and it takes two).

    A keyword whose first param really is a bare word is unaffected: nothing extends
    `press element login`, and `press element` takes ten params."""
    if not params or not _NAME_WORD.match(params[0]):
        return False

    catalogue = _keyword_catalogue()
    extended = _keyword_slug(f"{name} {params[0]}") + "_"
    if any(slug.startswith(extended) for slug in catalogue):
        return True

    limit = catalogue.get(_keyword_slug(name))
    return limit is not None and len(params) > limit


def _catalogue_match(step: str) -> Optional[Tuple[str, List[str]]]:
    words = _tokens(step)
    catalogue = _keyword_names()
    for count in range(len(words), 0, -1):
        name = " ".join(words[:count])
        if _keyword_slug(name) in catalogue:
            return name, words[count:]
    return None


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
    def is_keyword_param(param: str) -> bool:
        """
        Checks if a parameter string is in key=value format.

        :param param: The parameter string to check.
        :type param: str
        :return: True if the parameter is in key=value format, False otherwise.
        :rtype: bool
        """
        if param.startswith(("/", "//", "(")):
            return False
        # Must have a single = that is not part of ==, !=, <=, >=
        return bool(re.search(r'(?<![=!<>])=(?!=)', param))

    @staticmethod
    def split_params_by_signature(
        method: Callable, param_strings: List[str]
    ) -> Tuple[List[str], Dict[str, str]]:
        """Split params into (positional, keyword) using the target method's signature.

        A ``key=value`` token is treated as a keyword argument only when ``key``
        names an actual parameter of ``method``; otherwise the whole token stays
        positional. This keeps locator strategies such as ``text=...``,
        ``css=...`` or ``id=...`` as the keyword's positional ``element`` argument
        (they merely happen to contain ``=``) while still honouring genuine
        keyword arguments like ``event_name=...`` or ``timeout=...``. When the
        signature cannot be read the historical behaviour is kept (every
        ``key=value`` is a keyword argument).
        """
        try:
            valid: Optional[set] = set(inspect.signature(method).parameters)
        except (TypeError, ValueError):
            valid = None
        positional: List[str] = []
        keywords: Dict[str, str] = {}
        for param in param_strings:
            if DataReader.is_keyword_param(param):
                key, value = param.split("=", 1)
                key = key.strip()
                if valid is None or key in valid:
                    keywords[key] = value.strip()
                    continue
            positional.append(param.strip())
        return positional, keywords

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
    def read_modules(
        self, file_path: str, module_names: Optional[Set[str]] = None
    ) -> dict:
        """
        Read a file containing module information and return a dictionary mapping
        module names to lists of tuples (module_step, params).

        :param file_path: Path to the file.
        :type file_path: str
        :param module_names: Every module name in the project, for a reader that has to tell a
            module reference from a keyword. A suite is split across files, so a name defined
            in one file may be referenced from another.
        :return: A dictionary where keys are module names and values are lists of (module_step, params) tuples.
        :rtype: dict
        """
        pass

    def read_module_names(self, file_path: str) -> Set[str]:
        """The module names a file defines, so a caller can gather the project-wide set before
        any step is parsed. The names are the dictionary's keys, which no step parsing affects.

        :param file_path: Path to the file.
        :return: The module names defined in that file.
        """
        return set(self.read_modules(file_path))

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

    def read_modules(
        self, file_path: str, module_names: Optional[Set[str]] = None
    ) -> dict:
        """
        Read a CSV file containing module information and return a dictionary mapping
        module names to lists of tuples (module_step, params).

        :param file_path: Path to the modules CSV file.
        :type file_path: str
        :param module_names: Unused — a CSV keeps each param in its own column, so the keyword
            name never has to be told from a module reference.
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
                unescape_csv_value(str(row[key]).strip())
                for key in row
                if key is not None
                if key.startswith("param_") and row[key] and str(row[key]).strip()
            ]
            if module_name not in modules:
                modules[module_name] = []
            modules[module_name].append((keyword, params))
        return modules

    def read_module_names(self, file_path: str) -> Set[str]:
        """Read only the module_name column, so the project-wide names pass neither parses
        steps nor repeats the malformed-row warnings the parse pass emits."""
        return {
            row["module_name"].strip()
            for row in self.read_file(file_path)
            if row.get("module_name") and str(row["module_name"]).strip()
        }

    def read_elements(self, file_path: Optional[str]) -> dict:
        """
        Read a CSV file containing element information and return a dictionary mapping
        element names to a list of their corresponding element IDs (for fallback support).

        :param file_path: Path to the elements CSV file, or None if not provided.
        :type file_path: Optional[str]
        :return: A dictionary where keys are element names and values are lists of element IDs.
        :rtype: Dict[str, List[str]]
        """
        if not file_path:
            return {}
        rows = self.read_file(file_path)
        elements = {}
        for row in rows:
            element_name = row.get("Element_Name", "").strip()
            # Support multiple element IDs for fallback: look for all keys starting with "Element_ID"
            element_ids = [
                unescape_csv_value(str(row[key]).strip())
                for key in row
                if key is not None
                and re.sub(r"\s+", "", key).lower().startswith("element_id")
                and row[key]
                and str(row[key]).strip()
            ]
            if element_name and element_ids:
                if element_name not in elements:
                    elements[element_name] = []
                elements[element_name].extend(element_ids)
        return elements

    def read_error_definitions(self, file_path: str) -> dict:
        """Read error_code, match_string, description, severity columns from a CSV file.

        ``match_string`` is matched as a case-insensitive substring against
        on-screen text (not a regex).
        """
        result = {}
        rows = self.read_file(file_path)
        for row in rows:
            code = row.get("error_code", "").strip()
            match_string = row.get("match_string", "").strip()
            if code and match_string:
                result[code] = {
                    "match_string": match_string,
                    "description": row.get("description", "").strip(),
                    "severity": row.get("severity", "").strip(),
                }
        return result


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

    def _parse_module_step(
        self, step: str, module_names: Optional[Set[str]] = None
    ) -> Tuple[str, List[str]]:
        """
        Parse a module step to extract the keyword and parameters.

        :param step: The module step string.
        :param module_names: Module names that win over the keyword catalogue.
        :return: Tuple of (keyword, list of parameters).
        """
        step = step.strip()
        if not step:
            return "", []

        if module_names:
            matched = _matched_module_name(step, module_names)
            if matched is not None:
                return matched, []

        # The catalogue answers first, or a keyword whose first param is a plain value has
        # nothing to split on and `Sleep 5` reads as a keyword named `sleep 5`.
        match = _catalogue_match(step)
        if match is not None and not _mistyped(*match):
            return match

        split = _split_at_variable(step)
        if split is not None:
            return split

        return step, []

    def _process_module_steps(
        self, steps: List[str], module_names: Optional[Set[str]] = None
    ) -> List[Tuple[str, List[str]]]:
        """
        Process a list of module steps into a list of (keyword, params) tuples.

        :param steps: List of step strings.
        :param module_names: Names defined in the same file, passed through to the step parser.
        :return: List of (keyword, params) tuples.
        """
        module_steps = []
        for step in steps:
            keyword, params = self._parse_module_step(step, module_names)
            if keyword:
                module_steps.append((keyword, params))
        return module_steps

    def read_modules(
        self, file_path: str, module_names: Optional[Set[str]] = None
    ) -> Dict[str, List[Tuple[str, List[str]]]]:
        """
        Read a YAML file containing module information and return a dictionary mapping
        module names to lists of tuples (module_step, params).

        :param file_path: Path to the YAML file.
        :type file_path: str
        :param module_names: Every module name in the project. Without it only this file's own
            names are known, and a step referencing a keyword-shaped module defined in another
            file — ``Sleep Well`` — would be read as the keyword ``Sleep``.
        :return: A dictionary where keys are module names and values are lists of (module_step, params) tuples.
        :rtype: dict
        """
        data = self.read_file(file_path)
        modules = {}
        modules_data = data.get("Modules", [])
        # This file's own names as well, gathered before any step is parsed: a step may
        # reference a module defined below it.
        known_modules = set(module_names or ()) | {
            str(name).strip()
            for module in modules_data
            for name in module
            if str(name).strip()
        }

        for module in modules_data:
            for name, steps in module.items():
                name = name.strip()
                if not name or not steps:
                    internal_logger.warning(
                        f"Warning: Module '{name}' is empty or invalid"
                    )
                    continue
                modules[name] = self._process_module_steps(steps, known_modules)

        return modules

    def read_module_names(self, file_path: str) -> Set[str]:
        """Read only the module keys, so the project-wide names pass does not parse, and
        warn about, every step that the parse pass reads again."""
        data = self.read_file(file_path)
        modules_data = data.get("Modules", [])
        if not isinstance(modules_data, list):
            return set()
        return {
            str(name).strip()
            for module in modules_data
            if isinstance(module, dict)
            for name in module
            if str(name).strip()
        }

    def read_elements(self, file_path: Optional[str]) -> dict:
        """
        Read a YAML file containing element information and return a dictionary mapping
        element names to a list of their corresponding element IDs (for fallback support).

        :param file_path: Path to the YAML file, or None if not provided.
        :type file_path: Optional[str]
        :return: A dictionary where keys are element names and values are lists of element IDs.
        :rtype: Dict[str, List[str]]
        """
        if not file_path:
            return {}
        data = self.read_file(file_path)
        elements = {}
        elements_data = data.get("Elements", {})
        for name, value in elements_data.items():
            name = name.strip()
            # Support both single value and list of values for fallback
            if isinstance(value, list):
                values = [str(v).strip() for v in value if str(v).strip()]
            elif value is not None:
                values = [str(value).strip()] if str(value).strip() else []
            else:
                values = []
            if name and values:
                elements[name] = values
        return elements

    def read_api_data(self, file_path: str, existing_api_data: Optional[ApiData] = None) -> ApiData:
        """
        Reads a YAML file containing API definitions and merges it with existing ApiData.

        :param file_path: Path to the YAML file.
        :type file_path: str
        :param existing_api_data: Optional existing ApiData object to merge into.
        :type existing_api_data: Optional[ApiData]
        :return: An ApiData object representing the merged API definitions.
        :rtype: ApiData
        """
        data = self.read_file(file_path)
        api_data_content = data.get("api", data)
        try:
            new_api_data = ApiData(**api_data_content)
            internal_logger.debug(
                f"YAMLDataReader: New API data parsed: {new_api_data.model_dump_json(indent=2)}"
            )
            if existing_api_data:
                internal_logger.debug(
                    f"YAMLDataReader: Existing API data before merge: {existing_api_data.model_dump_json(indent=2)}"
                )
                self._merge_global_defaults(existing_api_data, new_api_data)
                self._merge_collections(existing_api_data, new_api_data)
                internal_logger.debug(
                    f"YAMLDataReader: Existing API data after merge: {existing_api_data.model_dump_json(indent=2)}"
                )
                return existing_api_data
            return new_api_data
        except Exception as e:
            internal_logger.error(f"Error parsing API data from {file_path}: {e}")
            raise ValueError(f"Invalid API data structure in {file_path}: {e}") from e

    def _merge_global_defaults(
        self, existing_api_data: ApiData, new_api_data: ApiData
    ) -> None:
        if new_api_data.global_defaults:
            if not isinstance(existing_api_data.global_defaults, dict):
                existing_api_data.global_defaults = {}
            existing_api_data.global_defaults.update(new_api_data.global_defaults)

    def _merge_collections(
        self, existing_api_data: ApiData, new_api_data: ApiData
    ) -> None:
        for collection_name, new_collection_obj in new_api_data.collections.items():
            if collection_name in existing_api_data.collections:
                existing_collection_obj = existing_api_data.collections[collection_name]
                self._merge_collection(existing_collection_obj, new_collection_obj)
            else:
                existing_api_data.collections[collection_name] = new_collection_obj

    def _merge_collection(self, existing_collection_obj, new_collection_obj) -> None:
        existing_collection_obj.global_headers.update(new_collection_obj.global_headers)
        for api_name, new_api_def in cast(
            Dict[str, ApiDefinition], new_collection_obj.apis
        ).items():
            if api_name in existing_collection_obj.apis:
                self._merge_api_def(existing_collection_obj.apis[api_name], new_api_def)
            else:
                existing_collection_obj.apis[api_name] = new_api_def

    def _merge_api_def(
        self, existing_api_def: ApiDefinition, new_api_def: ApiDefinition
    ) -> None:
        if new_api_def.expected_result and new_api_def.expected_result.extract:
            if existing_api_def.expected_result is None:
                existing_api_def.expected_result = ExpectedResultDefinition()
            if existing_api_def.expected_result.extract is None:
                existing_api_def.expected_result.extract = {}
            existing_api_def.expected_result.extract.update(
                new_api_def.expected_result.extract
            )


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
