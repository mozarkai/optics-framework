import re
from typing import Optional, Any, List, Union, Tuple, Callable, Dict
from urllib.parse import urlparse, parse_qsl
import os.path
import ast
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
import csv
from jsonpath_ng import parse
import requests
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.runner.test_runnner import Runner
from optics_framework.common.models import ApiData

NO_RUNNER_PRESENT = "Runner is None after ensure_runner call."

def raw_params(*indices):
    """Decorator to mark parameter indices that should remain unresolved."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)
        wrapper._raw_param_indices = indices  # pylint: disable=protected-access  # type: ignore[attr-defined]
        return wrapper
    return decorator


class FlowControl:
    """Manages control flow operations (loops, conditions, data) for a Runner."""

    def __init__(self, runner: Optional[Runner] = None, modules: Optional[Dict[str, List[Tuple[str, List[str]]]]] = None) -> None:
        self.runner: Optional[Runner] = runner
        self.modules: Dict[str, List[Tuple[str, List[str]]]
                           ] = modules if modules is not None else {}

    def _ensure_runner(self) -> None:
        """Ensures a Runner instance is set."""
        if self.runner is None:
            raise ValueError(
                "FlowControl.runner is not set. Please assign a valid runner instance before using FlowControl.")

    def _resolve_param(self, param: str) -> str:
        """Resolve ${variable} references from runner.elements."""
        if not isinstance(param, str) or not param.startswith("${") or not param.endswith("}"):
            return str(param)
        if self.runner is None:
            raise ValueError("Runner is None in resolve_param.")
        var_name = param[2:-1].strip()
        # Access the shared elements dictionary from the runner
        value = self.runner.elements.get(var_name)
        if value is None:
            raise ValueError(
                f"Variable '{param}' not found in elements dictionary")
        return str(value)

    def execute_module(self, module_name: str) -> List[Any]:
        """Executes a module's keywords using the runner's keyword_map."""
        self._ensure_runner()
        if self.runner is None:
            raise ValueError(NO_RUNNER_PRESENT)
        if module_name not in self.modules:
            raise ValueError(f"Module '{module_name}' not found in modules.")
        results = []

        for keyword, params in self.modules[module_name]:
            func_name = "_".join(keyword.split()).lower()
            method = self.runner.keyword_map.get(func_name)
            if method is None:
                raise ValueError(
                    f"Keyword '{keyword}' not found in keyword_map.")
            try:
                raw_indices = getattr(method, '_raw_param_indices', [])
                resolved_params = [
                    param if i in raw_indices else self._resolve_param(param)
                    for i, param in enumerate(params)
                ]
                internal_logger.debug(
                    f"Executing {keyword} with params: {resolved_params}")
                result = method(*resolved_params)
                results.append(result)
            except Exception as e:
                internal_logger.error(f"Error executing keyword '{keyword}': {e}")
                raise  # Propagate exception to fail the test
        return results

    @raw_params(1, 3, 5, 7, 9, 11, 13, 15)
    def run_loop(self, target: str, *args: str) -> List[Any]:
        """Runs a loop over a target module, either by count or with variables."""
        self._ensure_runner()
        if self.runner is None:
            raise ValueError(NO_RUNNER_PRESENT)
        if len(args) == 1:
            return self._loop_by_count(target, args[0])
        return self._loop_with_variables(target, args)

    def _loop_by_count(self, target: str, count_str: str) -> List[Any]:
        """Runs a loop a specified number of times."""
        try:
            iterations = int(count_str)
            if iterations < 1:
                raise ValueError("Iteration count must be at least 1.")
        except ValueError as e:
            if str(e) == "Iteration count must be at least 1.":
                raise
            raise ValueError(
                f"Expected an integer for loop count, got '{count_str}'.")

        results = []
        for i in range(iterations):
            internal_logger.debug(
                f"[RUN LOOP] Iteration {i+1}: Executing target '{target}'")
            result = self.execute_module(target)
            results.append(result)
        return results

    def _loop_with_variables(self, target: str, args: Tuple[str, ...]) -> List[Any]:
        """Runs a loop with variable-iterable pairs."""
        if len(args) % 2 != 0:
            raise ValueError(
                "Expected an even number of arguments for variable-iterable pairs.")

        variables = args[0::2]
        iterables = args[1::2]
        var_names, parsed_iterables = self._parse_variable_iterable_pairs(
            variables, iterables)
        min_length = min(len(lst) for lst in parsed_iterables)
        if self.runner is None:
            raise ValueError(NO_RUNNER_PRESENT)
        runner_elements: Dict[str, Any] = getattr(self.runner, 'elements', {})
        if not isinstance(runner_elements, dict):
            runner_elements = {}
            setattr(self.runner, 'elements', runner_elements)

        results = []
        for i in range(min_length):
            for var_name, iterable_values in zip(var_names, parsed_iterables):
                value = iterable_values[i]
                internal_logger.debug(
                    f"[RUN LOOP] Iteration {i+1}: Setting {var_name} = {value}")
                runner_elements[var_name] = value
            internal_logger.debug(
                f"[RUN LOOP] Iteration {i+1}: Executing target '{target}'")
            result = self.execute_module(target)
            results.append(result)
        return results

    def _parse_variable_iterable_pairs(self, variables: Tuple[str, ...], iterables: Tuple[str, ...]) -> Tuple[List[str], List[List[Any]]]:
        """Parses variable names and their corresponding iterables."""
        var_names = self._parse_variable_names(variables)
        parsed_iterables = self._parse_iterables(variables, iterables)
        return var_names, parsed_iterables

    def _parse_variable_names(self, variables: Tuple[str, ...]) -> List[str]:
        """Extracts and cleans variable names from the input tuple."""
        var_names = []
        for variable in variables:
            var_name = variable.strip()
            if var_name.startswith("${") and var_name.endswith("}"):
                var_name = var_name[2:-1].strip()
            else:
                internal_logger.warning(
                    f"[RUN LOOP] Expected variable in format '${{name}}', got '{variable}'. Using as is.")
            var_names.append(var_name)
        return var_names

    def _parse_iterables(self, variables: Tuple[str, ...], iterables: Tuple[str, ...]) -> List[List[Any]]:
        """Parses iterables into lists, handling JSON strings and validating input."""
        parsed_iterables = []
        for i, iterable in enumerate(iterables):
            parsed = self._parse_single_iterable(iterable, variables[i])
            if not parsed:
                raise ValueError(
                    f"Iterable for variable '{variables[i]}' is empty.")
            parsed_iterables.append(parsed)
        return parsed_iterables

    def _parse_single_iterable(self, iterable: Any, variable: str) -> List[Any]:
        """Parses a single iterable, converting JSON strings or validating lists."""
        if isinstance(iterable, str):
            try:
                values = json.loads(iterable)
                if not isinstance(values, list):
                    raise ValueError(
                        f"Iterable '{iterable}' for variable '{variable}' must resolve to a list.")
                return values
            except json.JSONDecodeError:
                raise ValueError(
                    f"Invalid iterable format for variable '{variable}': '{iterable}'.")
        elif isinstance(iterable, list):
            return iterable
        else:
            raise ValueError(
                f"Expected a list or JSON string for iterable of variable '{variable}', got {type(iterable).__name__}.")

    def condition(self, *args: str) -> Optional[List[Any]]:
        """Evaluates conditions and executes corresponding targets."""
        self._ensure_runner()
        if self.runner is None:
            raise ValueError(NO_RUNNER_PRESENT)
        if not args:
            raise ValueError("No condition-target pairs provided.")
        pairs, else_target = self._split_condition_args(args)
        return self._evaluate_conditions(pairs, else_target)

    def _split_condition_args(self, args: Tuple[str, ...]) -> Tuple[List[Tuple[str, str]], Optional[str]]:
        """Splits args into condition-target pairs and an optional else target."""
        pairs = []
        else_target = None
        if len(args) % 2 == 1:
            for i in range(0, len(args) - 1, 2):
                pairs.append((args[i], args[i + 1]))
            else_target = args[-1]
        else:
            for i in range(0, len(args), 2):
                pairs.append((args[i], args[i + 1]))
        return pairs, else_target

    def _evaluate_conditions(self, pairs: List[Tuple[str, str]], else_target: Optional[str]) -> Optional[List[Any]]:
        """Evaluates conditions and executes the first true target's module."""
        for cond, target in pairs:
            cond_str = cond.strip()
            if not cond_str:
                continue
            if self._is_condition_true(cond_str):
                internal_logger.debug(
                    f"[CONDITION] Condition '{cond_str}' is True. Executing target '{target}'.")
                return self.execute_module(target)
            internal_logger.debug(f"[CONDITION] Condition '{cond_str}' is False.")
        if else_target is not None:
            internal_logger.debug(
                f"[CONDITION] No condition met. Executing ELSE target '{else_target}'.")
            return self.execute_module(else_target)
        return None

    def _is_condition_true(self, cond: str) -> bool:
        """Evaluates if a condition is true."""
        try:
            resolved_cond = self._resolve_condition(cond)
            return bool(self._safe_eval(resolved_cond))
        except Exception as e:
            raise ValueError(f"Error evaluating condition '{cond}': {e}")

    def _resolve_condition(self, cond: str) -> str:
        """Resolves variables in a condition string."""
        if self.runner is None:
            raise ValueError(NO_RUNNER_PRESENT)
        runner_elements: Dict[str, Any] = getattr(self.runner, 'elements', {})
        pattern = re.compile(r"\$\{([^}]+)\}")

        def replacer(match):
            var_name = match.group(1).strip()
            value = runner_elements.get(var_name)
            if value is None:
                raise ValueError(
                    f"Variable '{var_name}' not found for condition resolution.")
            return f"'{value}'" if isinstance(value, str) else str(value)
        return pattern.sub(replacer, cond)

    @raw_params(0)
    def read_data(self, input_element: str, file_path: Union[str, List[Any]], index: str = "0") -> List[Any]:
        """Reads data from a file, API, or list and stores it in runner.elements."""
        self._ensure_runner()
        if self.runner is None:
            raise ValueError(NO_RUNNER_PRESENT)
        elem_name = self._extract_element_name(input_element)
        data = self._load_data(file_path, int(index))
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        runner_elements: Dict[str, Any] = getattr(self.runner, 'elements', {})
        if not isinstance(runner_elements, dict):
            runner_elements = {}
            setattr(self.runner, 'elements', runner_elements)
        runner_elements[elem_name] = data
        return data

    def _extract_element_name(self, input_element: str) -> str:
        """Extracts and cleans the element name from input."""
        elem_name = input_element.strip()
        if elem_name.startswith("${") and elem_name.endswith("}"):
            return elem_name[2:-1].strip()
        internal_logger.warning(
            f"[READ DATA] Expected element in format '${{name}}', got '{input_element}'. Using as is.")
        return elem_name

    def _load_data(self,file_path: Union[str, List[Any]], index: Optional[int]) -> List[Any]:
        """Loads data from a list, API, or CSV file."""
        if isinstance(file_path, list):
            return file_path
        if file_path.lower().startswith("http"):
            return FlowControl._load_from_api(file_path, index)
        return FlowControl._load_from_csv(file_path, index)

    @staticmethod
    def _load_from_api(url: str, index: Optional[int]) -> List[Any]:
        """Loads and extracts data from an API."""
        try:
            response = requests.get(url, timeout=(5, 30))
            response.raise_for_status()
            json_data = response.json()
            if isinstance(json_data, dict):
                if not isinstance(index, str):
                    raise ValueError("For json object, 'index' must be a string (JSON key).")
                if index not in json_data:
                    raise ValueError(f"Key '{index}' not found in API response.")
                return [json_data[index]]

            elif isinstance(json_data, list):
                if not isinstance(index, str):
                    raise ValueError("For JSON list, 'index' must be a string key.")
                return [item[index] for item in json_data if index in item]
            else:
                raise ValueError("Unsupported API response format.")
        except requests.RequestException as e:
            raise ValueError(f"Failed to fetch data from {url}: {e}")

    @staticmethod
    def _load_from_csv(file_path: str, index: Optional[int]) -> List[Any]:
        """Loads and extracts data from a CSV file."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File '{file_path}' not found.")
        if os.path.splitext(file_path)[-1].lower() != '.csv':
            raise ValueError(
                "Unsupported file format. Use CSV or provide a list/URL.")

        with open(file_path, newline='') as csvfile:
            reader = csv.reader(csvfile)
            data = list(reader)
            if not data:
                raise ValueError(f"CSV file '{file_path}' is empty.")
            return FlowControl._extract_csv_data(data, index)

    @staticmethod
    def _extract_csv_data(data: List[List[str]], index: Optional[int]) -> List[Any]:
        """Extracts data from CSV based on index or column name."""
        headers = data[0]
        rows = data[1:]

        if isinstance(index, str):
            if index not in headers:
                raise ValueError(f"Column '{index}' not found in CSV file.")
            col_idx = headers.index(index)
            return [row[col_idx] for row in rows if row[col_idx]]
        if isinstance(index, int):
            if index >= len(headers):
                raise IndexError("Index out of range.")
            return [row[index] for row in rows if row[index]]
        if index is None:
            return [row[0] for row in rows if row[0]]
        raise ValueError(
            "Index must be a string (column name) or an integer (column index).")

    @raw_params(0)
    def evaluate(self, param1: str, param2: str) -> Any:
        """Evaluates an expression and stores the result in runner.elements."""
        self._ensure_runner()
        if self.runner is None:
            raise ValueError(NO_RUNNER_PRESENT)
        var_name = self._extract_variable_name(param1)
        result = self._compute_expression(param2)
        runner_elements: Dict[str, Any] = getattr(self.runner, 'elements', {})
        if not isinstance(runner_elements, dict):
            runner_elements = {}
            setattr(self.runner, 'elements', runner_elements)
        runner_elements[var_name] = str(result)
        return result

    def _extract_variable_name(self, param1: str) -> str:
        """Extracts and cleans the variable name from param1."""
        var_name = param1.strip()
        if var_name.startswith("${") and var_name.endswith("}"):
            return var_name[2:-1].strip()
        internal_logger.warning(
            f"[EVALUATE] Expected param1 in format '${{name}}', got '{param1}'. Using as is.")
        return var_name

    def _compute_expression(self, param2: str) -> Any:
        """Computes an expression by resolving variables and evaluating it."""
        if self.runner is None:
            raise ValueError(NO_RUNNER_PRESENT)
        runner_elements: Dict[str, Any] = getattr(self.runner, 'elements', {})

        def replace_var(match):
            var_name = match.group(1)
            if var_name not in runner_elements:
                raise ValueError(
                    f"Variable '{var_name}' not found in elements.")
            return str(runner_elements[var_name])
        param2_resolved = re.sub(r"\$\{([^}]+)\}", replace_var, param2)
        return self._safe_eval(param2_resolved)

    def _safe_eval(self, expression: str) -> Any:
        """Safely evaluates an expression with restricted operations."""
        try:
            node = ast.parse(expression, mode='eval')
            allowed_nodes = (
                ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
                ast.IfExp, ast.Constant, ast.Name, ast.Load,
                ast.List, ast.Tuple)
            allowed_operators = (
                ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
                ast.Lt, ast.Gt, ast.Eq, ast.NotEq, ast.LtE, ast.GtE,
                ast.And, ast.Or, ast.Not)
            for n in ast.walk(node):
                if not isinstance(n, allowed_nodes) and not isinstance(n, allowed_operators):
                    raise ValueError(
                        f"Unsafe expression detected: {expression}")
            if self.runner is None:
                raise ValueError(NO_RUNNER_PRESENT)
            runner_elements = getattr(self.runner, 'elements', {})
            return eval(expression, {"__builtins__": None}, {k: str(v) for k, v in runner_elements.items()})  # nosec B307 # pylint: disable=eval-used
            # Note: eval() is used here for simplicity, i know it should be should be avoided in production code.
            # In some time, i will replace it with a safer alternative.
            # For now, we are using it with a restricted environment.
        except Exception as e:
            raise ValueError(
                f"Error evaluating expression '{expression}': {e}")


    def _detect_date_format(self,date_str: str) -> str:
        """Detect the format of the input date string."""
        common_formats = [
            "%m/%d/%Y",  # 04/25/2025
            "%d/%m/%Y",  # 25/04/2025
            "%Y-%m-%d",  # 2025-04-25
            "%d-%m-%Y",  # 25-04-2025
            "%Y/%m/%d",  # 2025/04/25
        ]
        for fmt in common_formats:
            try:
                datetime.strptime(date_str, fmt)
                return fmt
            except ValueError:
                continue
        raise ValueError(f"Unable to detect date format for input: {date_str}")


    @raw_params(0)
    def date_evaluate(self, param1: str, param2: str, param3: str, param4: Optional[str] = "%d %B") -> str:
        """
        Evaluates a date expression based on an input date and stores the result in runner.elements.

        Args:
            param1 (str): The variable name (placeholder) where the evaluated date result will be stored.
            param2 (str): The input date string (e.g., "04/25/2025" or "2025-04-25"). Format is auto-detected.
            param3 (str): The date expression to evaluate, such as "+1 day", "-2 days", or "today".
            param4 (Optional[str]): The output format for the evaluated date (default is "%d %B", e.g., "26 April").

        Returns:
            str: The resulting evaluated and formatted date string.

        Raises:
            ValueError: If the runner is not present, the input date format cannot be detected,
                        or the expression format is invalid.

        Example:
            date_evaluate("tomorrow", "04/25/2025", "+1 day")
            ➔ Stores "26 April" in runner.elements["tomorrow"]
        """
        self._ensure_runner()
        if self.runner is None:
            raise ValueError(NO_RUNNER_PRESENT)

        var_name = self._extract_variable_name(param1)
        input_date = param2.strip()
        expression = param3.strip()
        output_format = param4 or "%d %B"

        # Detect input format
        input_format = self._detect_date_format(input_date)

        # Parse current date
        base_date = datetime.strptime(input_date, input_format)

        # Parse and apply expression
        expr = expression.lower()
        if expr.startswith("+"):
            number, unit = expr[1:].split()
            number = int(number)
            if unit.startswith("day"):
                base_date += timedelta(days=number)
            else:
                raise ValueError(f"Unsupported unit in expression: {unit}")
        elif expr.startswith("-"):
            number, unit = expr[1:].split()
            number = int(number)
            if unit.startswith("day"):
                base_date -= timedelta(days=number)
            else:
                raise ValueError(f"Unsupported unit in expression: {unit}")
        elif expr in ("today", "now"):
            pass  # No change
        else:
            raise ValueError(f"Unsupported expression format: {expression}")

        # Format result
        result = base_date.strftime(output_format)

        # Store in runner.elements
        runner_elements: Dict[str, Any] = getattr(self.runner, 'elements', {})
        if not isinstance(runner_elements, dict):
            runner_elements = {}
            setattr(self.runner, 'elements', runner_elements)

        runner_elements[var_name] = result
        return result

    def invoke_api(self, api_identifier: str) -> None:
        """Invokes an API call based on a definition from the runner's API data."""
        self._ensure_runner()
        if self.runner is None:
            raise ValueError(NO_RUNNER_PRESENT)
        collection_name, api_name = self._parse_api_identifier(api_identifier)
        collection = self._get_api_collection(collection_name)
        api_def = self._get_api_definition(collection, api_name)

        url, headers, body = self._prepare_request_details(collection, api_def)
        response = self._execute_request(url, headers, body, api_def.request.method)
        self._process_response(response, api_def)

    def _parse_api_identifier(self, identifier: str) -> Tuple[str, str]:
        """Parses 'collection.api' into a tuple."""
        parts = identifier.split('.', 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid API identifier format: '{identifier}'. Expected 'collection.api_name'.")
        return parts[0], parts[1]
    def _get_api_collection(self, name: str):
        """Retrieves an API collection from the runner."""
        if self.runner is None:
            raise ValueError(NO_RUNNER_PRESENT)
        if not isinstance(self.runner.apis, ApiData):
            raise ValueError("Runner instance does not have a valid 'apis' attribute.")
        collection = self.runner.apis.collections.get(name)
        if not collection:
            raise ValueError(f"API collection '{name}' not found.")
        return collection

    def _get_api_definition(self, collection, name: str):
        """Retrieves a specific API definition from a collection."""
        api_def = collection.apis.get(name)
        if not api_def:
            raise ValueError(f"API '{name}' not found in collection '{collection.name}'.")
        return api_def

    def _prepare_request_details(self, collection, api_def) -> Tuple[str, Dict[str, str], Optional[Any]]:
        """Constructs the full request URL, headers, and body."""
        base_url = collection.base_url
        endpoint = self._resolve_placeholders(api_def.endpoint)
        if endpoint.startswith(("http://", "https://")):
            full_url = endpoint
        else:
            full_url = f"{base_url}{endpoint}"

        headers = {**collection.global_headers, **api_def.request.headers}
        resolved_headers = self._resolve_placeholders(headers)

        body = self._resolve_placeholders(api_def.request.body)
        return full_url, resolved_headers, body

    def _resolve_placeholders(self, data: Any) -> Any:
        """Recursively resolves ${...} placeholders in strings, dicts, or lists."""
        if isinstance(data, str):
            return re.sub(r'\$\{([^}]+)\}', lambda m: self._resolve_param(m.group(0)), data)
        if isinstance(data, dict):
            return {k: self._resolve_placeholders(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self._resolve_placeholders(i) for i in data]
        return data

    def _execute_request(self, url: str, headers: Dict, body: Optional[Any], method: str) -> requests.Response:
        """Executes the HTTP request and returns the response."""
        try:
            config_handler = ConfigHandler.get_instance()
            execution_output_path = config_handler.get_execution_output_path()
            api_log_dir = execution_output_path

            api_log_file_path = None
            api_har_file_path = None
            if api_log_dir:
                os.makedirs(api_log_dir, exist_ok=True)
                api_log_file_path = os.path.join(api_log_dir, "api_details.log")
                api_har_file_path = os.path.join(api_log_dir, "api_details.har")

            start_time = datetime.now(timezone.utc)
            response = requests.request(method, url, headers=headers, json=body, timeout=30)
            time_taken = response.elapsed.total_seconds() * 1000

            if api_log_dir and api_log_file_path is not None:
                self._write_api_log(api_log_file_path, method, url, headers, body, response)
                self._write_api_har(api_har_file_path, start_time, time_taken, method, url, headers, body, response)

            return response
        except requests.RequestException as e:
            raise RuntimeError(f"API request to {url} failed: {e}") from e

    def _write_api_log(self, log_file_path, method, url, headers, body, response):
        """Writes API request/response details to a log file."""
        with open(log_file_path, "a", encoding="utf-8") as f:
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            f.write(f"Method: {method}\n")
            f.write(f"URL: {url}\n")
            f.write(f"Headers: {json.dumps(headers, indent=2)}\n")
            f.write(f"Body: {json.dumps(body, indent=2) if body else 'None'}\n")
            f.write(f"Response Status Code: {response.status_code}\n")
            try:
                f.write(f"Response Body: {json.dumps(response.json(), indent=2)}\n")
            except json.JSONDecodeError:
                f.write(f"Response Body: {response.text}\n")
            f.write("-" * 50 + "\n")

    def _write_api_har(self, har_file_path, start_time, time_taken, method, url, headers, body, response):
        """Writes API request/response details to a HAR file."""
        parsed_url = urlparse(url)
        query_string = [{"name": k, "value": v} for k, v in parse_qsl(parsed_url.query)]

        post_data = None
        request_body_size = 0
        if body:
            request_body_text = json.dumps(body)
            request_body_size = len(request_body_text.encode('utf-8'))
            post_data = {
                "mimeType": "application/json",
                "text": request_body_text
            }

        har_entry = {
            "startedDateTime": start_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + "Z",
            "time": time_taken,
            "request": {
                "method": method,
                "url": url,
                "httpVersion": "HTTP/1.1",
                "cookies": [],
                "headers": [{"name": k, "value": v} for k, v in headers.items()],
                "queryString": query_string,
                "postData": post_data,
                "headersSize": -1,
                "bodySize": request_body_size
            },
            "response": {
                "status": response.status_code,
                "statusText": response.reason,
                "httpVersion": "HTTP/1.1",
                "cookies": [],
                "headers": [{"name": k, "value": v} for k, v in response.headers.items()],
                "content": {
                    "size": len(response.content),
                    "mimeType": response.headers.get('Content-Type', ''),
                    "text": response.text
                },
                "redirectURL": response.headers.get('Location', ''),
                "headersSize": -1,
                "bodySize": len(response.content)
            },
            "cache": {},
            "timings": { "wait": response.elapsed.total_seconds() * 1000, "send": -1, "receive": -1 }
        }

        if har_file_path is not None and os.path.exists(har_file_path):
            with open(har_file_path, "r", encoding="utf-8") as f:
                try:
                    har_data = json.load(f)
                    if "log" not in har_data or "entries" not in har_data["log"]:
                        har_data = self._create_har_structure()
                except json.JSONDecodeError:
                    har_data = self._create_har_structure()
        else:
            har_data = self._create_har_structure()

        har_data["log"]["entries"].append(har_entry)

        if har_file_path is not None:
            with open(har_file_path, "w", encoding="utf-8") as f:
                json.dump(har_data, f, indent=2)

    def _create_har_structure(self) -> Dict[str, Any]:
        """Creates a basic HAR file structure."""
        return {
            "log": {
                "version": "1.2",
                "creator": {
                    "name": "Optics Framework",
                    "version": "1.0"
                },
                "entries": []
            }
        }

    def _process_response(self, response: requests.Response, api_def) -> None:
        """Extracts data from the response and saves it to runner elements."""
        if not api_def.expected_result:
            internal_logger.debug("No expected_result defined in API definition.")
            return

        if not api_def.expected_result.extract:
            internal_logger.debug("No extraction paths defined in API definition.")
        else:
            internal_logger.debug(f"Attempting to extract values using paths: {api_def.expected_result.extract}")
        if self.runner is None:
            raise ValueError(NO_RUNNER_PRESENT)

        try:
            response_data = response.json()
        except json.JSONDecodeError:
            internal_logger.warning("API response is not valid JSON; cannot extract values.")
            return

        if api_def.expected_result.extract:
            for element_name, path in api_def.expected_result.extract.items():
                value = self._extract_from_json(response_data, path)
                if value is not None:
                    self.runner.elements[element_name] = value
                    internal_logger.debug(f"Extracted '{element_name}' = '{value}' from response.")
                else:
                    internal_logger.warning(f"Could not extract '{element_name}' using path '{path}'.")
            internal_logger.debug(f"Current runner elements after extraction: {self.runner.elements}")

        if api_def.expected_result.jsonpath_assertions:
            self._evaluate_jsonpath_assertions(response_data, api_def.expected_result.jsonpath_assertions)

    def _evaluate_jsonpath_assertions(self, data: Any, assertions: List[Dict[str, Any]]) -> None:
        """Evaluates JSONPath assertions against the response data."""
        for assertion in assertions:
            path = assertion.get("path")
            condition = assertion.get("condition")
            if not path or not condition:
                internal_logger.warning(f"Skipping malformed JSONPath assertion: {assertion}")
                continue

            try:
                jsonpath_expr = parse(path)
                match = jsonpath_expr.find(data)
                if not match:
                    raise AssertionError(f"JSONPath '{path}' found no match.")

                matched_value = match[0].value
                eval_condition = condition.replace('$', repr(matched_value))

                if not eval(eval_condition): # nosec B307 # pylint: disable=eval-used
                    raise AssertionError(f"JSONPath assertion failed: Path '{path}', Condition '{condition}', Matched value '{matched_value}'.")
                internal_logger.debug(f"JSONPath assertion passed: Path '{path}', Condition '{condition}'.")
            except Exception as e:
                raise AssertionError(f"Error evaluating JSONPath assertion {assertion}: {e}") from e

    def _extract_from_json(self, data: Any, path: str) -> Optional[Any]:
        """Extracts a value from a nested dictionary using a dot-separated path."""
        current_data = data
        for key in path.split('.'):
            internal_logger.debug(f"_extract_from_json: Current data type: {type(current_data)}, Key: {key}")
            if isinstance(current_data, dict):
                current_data = current_data.get(key)
                internal_logger.debug(f"_extract_from_json: Value after get('{key}'): {current_data}")
            else:
                internal_logger.warning(f"_extract_from_json: Data is not a dictionary at key '{key}'. Current data: {current_data}")
                return None
        return current_data
