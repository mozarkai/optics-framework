import time
from abc import ABC, abstractmethod
from typing import Callable, Dict, List, Optional, Tuple, Union, Any
from pydantic import BaseModel, Field
from optics_framework.common.logging_config import logger, apply_logger_format_to_all, HierarchicalJsonHandler
import pytest
import tempfile
import shutil
import sys
from optics_framework.common.session_manager import Session
from .printers import IResultPrinter, TestCaseResult


class KeywordResult(BaseModel):
    """Result of a single keyword execution."""
    name: str
    resolved_name: str
    elapsed: str
    status: str
    reason: str


class ModuleResult(BaseModel):
    """Result of a module execution."""
    name: str
    elapsed: str
    status: str
    keywords: List[KeywordResult] = Field(default_factory=list)


class Runner(ABC):
    """Abstract base class for test runners with explicit attributes."""
    test_cases: Dict[str, List[str]]
    result_printer: IResultPrinter
    keyword_map: Dict[str, Callable[..., Any]]

    @abstractmethod
    def execute_test_case(self, test_case: str) -> Union[dict, TestCaseResult]:
        pass

    @abstractmethod
    def run_all(self) -> None:
        pass

    @abstractmethod
    def dry_run_test_case(self, test_case: str) -> Union[dict, TestCaseResult]:
        pass

    @abstractmethod
    def dry_run_all(self) -> None:
        pass


@apply_logger_format_to_all("user")
class TestRunner(Runner):
    def __init__(
        self,
        test_cases: Dict[str, List[str]],
        modules: Dict[str, List[Tuple[str, List[str]]]],
        elements: Dict[str, str],
        keyword_map: Dict[str, Callable[..., Any]],
        result_printer: IResultPrinter
    ) -> None:
        self.test_cases = test_cases
        self.modules = modules
        self.elements = elements
        self.keyword_map = keyword_map
        self.result_printer = result_printer

    def _extra(self, test_case: str, module: str = "N/A", keyword: str = "N/A") -> Dict[str, str]:
        return {"test_case": test_case, "test_module": module, "keyword": keyword}

    def resolve_param(self, param: str) -> str:
        if not param.startswith("${") or not param.endswith("}"):
            return param
        var_name = param[2:-1].strip()
        resolved_value = self.elements.get(var_name)
        if resolved_value is None:
            raise ValueError(
                f"Variable '{param}' not found in elements dictionary")
        return resolved_value

    def _init_test_case(self, test_case: str) -> TestCaseResult:
        return TestCaseResult(name=test_case, elapsed="0.00s", status="NOT RUN")

    def _init_module(self, module_name: str) -> ModuleResult:
        return ModuleResult(name=module_name, elapsed="0.00s", status="NOT RUN")

    def _init_keyword(self, keyword: str) -> KeywordResult:
        return KeywordResult(name=keyword, resolved_name=keyword, elapsed="0.00s", status="NOT RUN", reason="")

    def _update_status(self, result: Union[TestCaseResult, ModuleResult, KeywordResult], status: str, elapsed: Optional[float] = None) -> None:
        result.status = status
        if elapsed is not None:
            result.elapsed = f"{elapsed:.2f}s"
        if isinstance(result, TestCaseResult):
            self.result_printer.print_tree_log(result)

    def _execute_keyword(self, keyword: str, params: List[str], keyword_result: KeywordResult, module_result: ModuleResult, test_case_result: TestCaseResult, start_time: float, extra: Dict[str, str]) -> bool:
        logger.debug(f"Executing keyword: {keyword}", extra=extra)
        self._update_status(keyword_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        func_name = "_".join(keyword.split()).lower()
        method = self.keyword_map.get(func_name)
        if not method:
            logger.error(f"Keyword not found: {keyword}", extra=extra)
            keyword_result.reason = "Keyword not found"
            keyword_result.elapsed = f"{time.time() - start_time:.2f}s"
            self._update_status(keyword_result, "FAIL")
            self._update_status(module_result, "FAIL")
            self._update_status(test_case_result, "FAIL")
            self.result_printer.print_tree_log(test_case_result)
            return False

        try:
            raw_indices = getattr(method, '_raw_param_indices', [])
            resolved_params = [param if i in raw_indices else self.resolve_param(
                param) for i, param in enumerate(params)]
            keyword_result.resolved_name = f"{keyword} ({', '.join(str(p) for p in resolved_params)})"
            method(*resolved_params)  # Return value ignored
            logger.debug(
                f"Keyword '{keyword}' executed successfully", extra=extra)
            self._update_status(keyword_result, "PASS",
                                time.time() - start_time)
            self.result_printer.print_tree_log(test_case_result)
            return True
        except Exception as e:
            logger.error(
                f"Error executing keyword '{keyword}': {e}", extra=extra)
            keyword_result.reason = str(e)
            keyword_result.elapsed = f"{time.time() - start_time:.2f}s"
            self._update_status(keyword_result, "FAIL")
            self._update_status(module_result, "FAIL")
            self._update_status(test_case_result, "FAIL")
            self.result_printer.print_tree_log(test_case_result)
            return False

    def _process_module(self, module_name: str, test_case_result: TestCaseResult, extra: Dict[str, str]) -> bool:
        logger.debug(f"Loading module: {module_name}", extra=extra)
        module_result = self._init_module(module_name)
        if module_name not in self.modules:
            logger.error("Module not found", extra=extra)
            self._update_status(module_result, "FAIL")
            return False
        test_case_result.modules.append(module_result)
        self.result_printer.print_tree_log(test_case_result)

        if module_name not in self.modules:
            logger.error("Module not found", extra=extra)
            self._update_status(module_result, "FAIL")
            self._update_status(test_case_result, "FAIL")
            self.result_printer.print_tree_log(test_case_result)
            return False

        module_start = time.time()
        self._update_status(module_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        for keyword, params in self.modules[module_name]:
            keyword_result = self._init_keyword(keyword)
            module_result.keywords.append(  # pylint: disable=no-member
                keyword_result)
            extra["keyword"] = keyword
            keyword_start = time.time()
            if not self._execute_keyword(keyword, params, keyword_result, module_result, test_case_result, keyword_start, extra):
                return False
            module_result.elapsed = f"{time.time() - module_start:.2f}s"
            module_result.status = "PASS" if all(
                k.status == "PASS" for k in module_result.keywords) else "FAIL"
            self.result_printer.print_tree_log(test_case_result)
        return True

    def execute_test_case(self, test_case: str) -> TestCaseResult:
        start_time = time.time()
        extra = self._extra(test_case)
        logger.debug("Starting test case execution", extra=extra)
        test_case_result = self._init_test_case(test_case)
        self.result_printer.print_tree_log(test_case_result)

        if test_case not in self.test_cases:
            logger.error("Test case not found", extra=extra)
            self._update_status(test_case_result, "FAIL",
                                time.time() - start_time)
            self.result_printer.print_tree_log(test_case_result)
            return test_case_result

        self._update_status(test_case_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        for module_name in self.test_cases[test_case]:
            if not self._process_module(module_name, test_case_result, self._extra(test_case, module_name)):
                return test_case_result

        test_case_result.elapsed = f"{time.time() - start_time:.2f}s"
        test_case_result.status = "PASS" if all(
            m.status == "PASS" for m in test_case_result.modules) else "FAIL"
        logger.debug("Completed test case execution", extra=extra)
        self.result_printer.print_tree_log(test_case_result)
        return test_case_result

    def run_all(self, test_case_names: Union[str, List[str]] = "") -> None:
        if isinstance(test_case_names, str):
            test_case_names = list(self.test_cases.keys()) if test_case_names == "" else [
                test_case_names]
        if not test_case_names:
            logger.error("No test cases found to run.",
                         extra=self._extra("N/A"))
            return

        for tc_name in test_case_names:
            self.result_printer.test_state[tc_name] = self._init_test_case(
                tc_name)
        self.result_printer.start_run(len(test_case_names))
        self.result_printer.start_live()
        for test_case in test_case_names:
            self.execute_test_case(test_case)
        self.result_printer.stop_live()
        for handler in logger.handlers:
            if isinstance(handler, HierarchicalJsonHandler):
                handler.flush()

    def _dry_run_keyword(self, keyword: str, params: List[str], keyword_result: KeywordResult, module_result: ModuleResult, test_case_result: TestCaseResult, extra: Dict[str, str]) -> bool:
        logger.debug(f"Executing keyword: {keyword}", extra=extra)
        self._update_status(keyword_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        try:
            resolved_params = [self.resolve_param(param) for param in params]
            if resolved_params:
                keyword_result.resolved_name = f"{keyword} ({', '.join(resolved_params)})"
        except ValueError as e:
            logger.error(f"Parameter resolution failed: {e}", extra=extra)
            keyword_result.reason = str(e)
            self._update_status(keyword_result, "FAIL")
            self._update_status(module_result, "FAIL")
            self._update_status(test_case_result, "FAIL")
            self.result_printer.print_tree_log(test_case_result)
            return False

        func_name = "_".join(keyword.split()).lower()
        if func_name not in self.keyword_map:
            logger.error(f"Keyword not found: {keyword}", extra=extra)
            keyword_result.reason = "Keyword not found"
            self._update_status(keyword_result, "FAIL")
            self._update_status(module_result, "FAIL")
            self._update_status(test_case_result, "FAIL")
            self.result_printer.print_tree_log(test_case_result)
            return False

        self._update_status(keyword_result, "PASS", 0.0)
        self.result_printer.print_tree_log(test_case_result)
        return True

    def _dry_run_module(self, module_name: str, test_case_result: TestCaseResult, extra: Dict[str, str]) -> bool:
        logger.debug(f"Loading module: {module_name}", extra=extra)
        module_result = self._init_module(module_name)
        test_case_result.modules.append(module_result)
        self.result_printer.print_tree_log(test_case_result)

        self._update_status(module_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        for keyword, params in self.modules.get(module_name, []):
            keyword_result = self._init_keyword(keyword)
            module_result.keywords.append(   # pylint: disable=no-member
                keyword_result)
            extra["keyword"] = keyword
            if not self._dry_run_keyword(keyword, params, keyword_result, module_result, test_case_result, extra):
                return False
            module_result.status = "PASS" if all(
                k.status == "PASS" for k in module_result.keywords) else "FAIL"
            module_result.elapsed = "0.00s"
            self.result_printer.print_tree_log(test_case_result)
        return True

    def dry_run_test_case(self, test_case: str) -> TestCaseResult:
        start_time = time.time()
        extra = self._extra(test_case)
        logger.debug("Starting dry run", extra=extra)
        test_case_result = self._init_test_case(test_case)
        self.result_printer.print_tree_log(test_case_result)

        if test_case not in self.test_cases:
            logger.warning(
                f"Test case '{test_case}' not found.", extra=extra)
            self._update_status(test_case_result, "FAIL",
                                time.time() - start_time)
            self.result_printer.print_tree_log(test_case_result)
            return test_case_result

        self._update_status(test_case_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        for module_name in self.test_cases[test_case]:
            if not self._dry_run_module(module_name, test_case_result, self._extra(test_case, module_name)):
                return test_case_result

        test_case_result.elapsed = f"{time.time() - start_time:.2f}s"
        test_case_result.status = "PASS" if all(
            m.status == "PASS" for m in test_case_result.modules) else "FAIL"
        logger.debug("Completed dry run", extra=extra)
        self.result_printer.print_tree_log(test_case_result)
        return test_case_result

    def dry_run_all(self, test_case_names: Union[str, List[str]] = "") -> None:
        if isinstance(test_case_names, str):
            test_case_names = list(self.test_cases.keys()) if test_case_names == "" else [
                test_case_names]
        self.result_printer.start_live()
        for test_case in test_case_names:
            self.dry_run_test_case(test_case)
        self.result_printer.stop_live()


class PytestRunner(Runner):
    """Pytest-based test runner."""
    instance = None

    def __init__(self, session: Session, test_cases: Dict, modules: Dict, elements: Dict, keyword_map: Dict):
        self.test_cases = test_cases
        self.modules = modules
        self.elements = elements
        self.session = session
        self.keyword_map = keyword_map
        from .printers import NullResultPrinter  # Local import to avoid circularity
        self.result_printer = NullResultPrinter()
        PytestRunner.instance = self

    def resolve_param(self, param: str) -> str:
        if not param.startswith("${") or not param.endswith("}"):
            return param
        var_name = param[2:-1].strip()
        if var_name not in self.elements:
            pytest.fail(f"Variable '{var_name}' not found")
        return self.elements[var_name]

    def execute_test_case(self, test_case: str, dry_run: bool = False) -> dict:
        if test_case not in self.test_cases:
            return {"name": test_case, "status": "FAIL", "elapsed": "0.00s", "message": "Test case not found"}
        success = self._run_pytest([test_case], dry_run)
        return {"name": test_case, "status": "PASS" if success else "FAIL", "elapsed": "0.00s", "modules": []}

    def run_all(self) -> None:
        if not self.test_cases:
            self.result_printer.test_state = {}
            return
        success = self._run_pytest(list(self.test_cases.keys()))
        self.result_printer.test_state = {tc: TestCaseResult(
            name=tc, elapsed="0.00s", status="PASS" if success else "FAIL") for tc in self.test_cases}

    def dry_run_test_case(self, test_case: str) -> dict:
        return self.execute_test_case(test_case, dry_run=True)

    def dry_run_all(self) -> None:
        if not self.test_cases:
            self.result_printer.test_state = {}
            return
        success = self._run_pytest(list(self.test_cases.keys()), dry_run=True)
        self.result_printer.test_state = {tc: TestCaseResult(
            name=tc, elapsed="0.00s", status="PASS" if success else "FAIL") for tc in self.test_cases}

    def _run_pytest(self, test_cases: List[str], dry_run: bool = False) -> bool:
        temp_dir = tempfile.mkdtemp()
        test_file_path = f"{temp_dir}/test_generated_{int(time.time()*1000)}.py"
        conftest_path = f"{temp_dir}/conftest.py"

        with open(conftest_path, "w") as f:
            f.write("""
import pytest
from optics_framework.common.runners import PytestRunner

@pytest.fixture
def runner():
    return PytestRunner.instance
""")

        test_code = "".join(
            f"def test_{tc.replace(' ', '_')}(runner):\n    runner.execute_test_case('{tc}', dry_run={dry_run})\n"
            for tc in test_cases
        )
        with open(test_file_path, "w") as f:
            f.write(test_code)

        for module_name in list(sys.modules.keys()):
            if module_name.startswith("test_generated"):
                del sys.modules[module_name]

        result = pytest.main([temp_dir, '-q', '--disable-warnings'])
        shutil.rmtree(temp_dir)
        return result == 0
