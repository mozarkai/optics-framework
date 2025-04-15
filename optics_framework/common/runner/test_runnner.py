import time
from abc import ABC, abstractmethod
from typing import Callable, Dict, List, Optional, Tuple, Union, Any
import pytest
import tempfile
import shutil
import sys
from pydantic import BaseModel, Field
from optics_framework.common.session_manager import Session
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.logging_config import user_logger, internal_logger
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
        internal_logger.debug(f"Executing keyword: {keyword}", extra=extra)
        user_logger.debug(f"Executing keyword: {keyword}", extra=extra)
        self._update_status(keyword_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        func_name = "_".join(keyword.split()).lower()
        method = self.keyword_map.get(func_name)
        if not method:
            user_logger.error(f"Keyword not found: {keyword}", extra=extra)
            internal_logger.error(f"Keyword not found: {keyword}", extra=extra)
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
            method(*resolved_params)
            internal_logger.debug(
                f"Keyword '{keyword}' executed successfully", extra=extra)
            user_logger.debug(
                f"Keyword '{keyword}' executed successfully", extra=extra)
            self._update_status(keyword_result, "PASS",
                                time.time() - start_time)
            self.result_printer.print_tree_log(test_case_result)
            return True
        except Exception as e:
            internal_logger.error(
                f"Error executing keyword '{keyword}': {e}", extra=extra)
            user_logger.error(
                f"Error executing keyword '{keyword}': {e}", extra=extra)
            keyword_result.reason = str(e)
            keyword_result.elapsed = f"{time.time() - start_time:.2f}s"
            self._update_status(keyword_result, "FAIL")
            self._update_status(module_result, "FAIL")
            self._update_status(test_case_result, "FAIL")
            self.result_printer.print_tree_log(test_case_result)
            return False

    def _process_module(self, module_name: str, test_case_result: TestCaseResult, extra: Dict[str, str]) -> bool:
        internal_logger.debug(f"Loading module: {module_name}", extra=extra)
        user_logger.debug(f"Loading module: {module_name}", extra=extra)
        module_result = self._init_module(module_name)
        if module_name not in self.modules:
            user_logger.error("Module not found", extra=extra)
            internal_logger.error("Module not found", extra=extra)
            self._update_status(module_result, "FAIL")
            return False
        test_case_result.modules.append(module_result)
        self.result_printer.print_tree_log(test_case_result)

        module_start = time.time()
        self._update_status(module_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        for keyword, params in self.modules[module_name]:
            keyword_result = self._init_keyword(keyword)
            module_result.keywords = module_result.keywords + [keyword_result]
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
        user_logger.info("Starting test case", extra={
                         **extra, "test_start": True})
        internal_logger.info("Starting test case", extra={
                             **extra, "test_start": True})
        test_case_result = self._init_test_case(test_case)
        self.result_printer.print_tree_log(test_case_result)

        if test_case not in self.test_cases:
            user_logger.error("Test case not found", extra=extra)
            internal_logger.error("Test case not found", extra=extra)
            self._update_status(test_case_result, "FAIL",
                                time.time() - start_time)
            self.result_printer.print_tree_log(test_case_result)
            user_logger.info("Completed test case", extra={
                             **extra, "test_end": True})
            internal_logger.info("Completed test case", extra={
                                 **extra, "test_end": True})
            return test_case_result

        self._update_status(test_case_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        for module_name in self.test_cases[test_case]:
            if not self._process_module(module_name, test_case_result, self._extra(test_case, module_name)):
                user_logger.info("Completed test case", extra={
                                 **extra, "test_end": True})
                internal_logger.info("Completed test case", extra={
                                     **extra, "test_end": True})
                return test_case_result

        test_case_result.elapsed = f"{time.time() - start_time:.2f}s"
        test_case_result.status = "PASS" if all(
            m.status == "PASS" for m in test_case_result.modules) else "FAIL"
        user_logger.debug("Completed test case execution", extra=extra)
        internal_logger.debug("Completed test case execution", extra=extra)
        self.result_printer.print_tree_log(test_case_result)
        user_logger.info("Completed test case", extra={
                         **extra, "test_end": True})
        internal_logger.info("Completed test case", extra={
                             **extra, "test_end": True})
        return test_case_result

    def run_all(self, test_case_names: Union[str, List[str]] = "") -> None:
        if isinstance(test_case_names, str):
            test_case_names = list(self.test_cases.keys()) if test_case_names == "" else [
                test_case_names]
        if not test_case_names:
            user_logger.error("No test cases found to run.",
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

    def _dry_run_keyword(self, keyword: str, params: List[str], keyword_result: KeywordResult, module_result: ModuleResult, test_case_result: TestCaseResult, extra: Dict[str, str]) -> bool:
        internal_logger.debug(f"Executing keyword: {keyword}", extra=extra)
        user_logger.debug(f"Executing keyword: {keyword}", extra=extra)
        self._update_status(keyword_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        try:
            resolved_params = [self.resolve_param(param) for param in params]
            if resolved_params:
                keyword_result.resolved_name = f"{keyword} ({', '.join(resolved_params)})"
        except ValueError as e:
            user_logger.error(f"Parameter resolution failed: {e}", extra=extra)
            internal_logger.error(
                f"Parameter resolution failed: {e}", extra=extra)
            keyword_result.reason = str(e)
            self._update_status(keyword_result, "FAIL")
            self._update_status(module_result, "FAIL")
            self._update_status(test_case_result, "FAIL")
            self.result_printer.print_tree_log(test_case_result)
            return False

        func_name = "_".join(keyword.split()).lower()
        if func_name not in self.keyword_map:
            user_logger.error(f"Keyword not found: {keyword}", extra=extra)
            internal_logger.error(f"Keyword not found: {keyword}", extra=extra)
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
        internal_logger.debug(f"Loading module: {module_name}", extra=extra)
        user_logger.debug(f"Loading module: {module_name}", extra=extra)
        module_result = self._init_module(module_name)
        test_case_result.modules.append(module_result)
        self.result_printer.print_tree_log(test_case_result)

        self._update_status(module_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        for keyword, params in self.modules.get(module_name, []):
            keyword_result = self._init_keyword(keyword)
            module_result.keywords.append(keyword_result)
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
        user_logger.debug("Starting dry run", extra=extra)
        internal_logger.debug("Starting dry run", extra=extra)
        test_case_result = self._init_test_case(test_case)
        self.result_printer.print_tree_log(test_case_result)

        if test_case not in self.test_cases:
            user_logger.warning(
                f"Test case '{test_case}' not found.", extra=extra)
            internal_logger.warning(
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
        user_logger.debug("Completed dry run", extra=extra)
        internal_logger.debug("Completed dry run", extra=extra)
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
        from .printers import NullResultPrinter
        self.result_printer = NullResultPrinter()
        PytestRunner.instance = self

    def resolve_param(self, param: str) -> str:
        if not param.startswith("${") or not param.endswith("}"):
            return param
        var_name = param[2:-1].strip()
        if var_name not in self.elements:
            pytest.fail(f"Variable '{var_name}' not found")
        return self.elements[var_name]

    def _execute_keyword(self, keyword: str, params: List[str]) -> bool:
        func_name = "_".join(keyword.split()).lower()
        method = self.keyword_map.get(func_name)
        if not method:
            pytest.fail(f"Keyword not found: {keyword}")
        try:
            resolved_params = [self.resolve_param(param) for param in params]
            method(*resolved_params)
            return True
        except Exception as e:
            pytest.fail(f"Keyword '{keyword}' failed: {e}")
            return False

    def _process_module(self, module_name: str) -> bool:
        if module_name not in self.modules:
            pytest.fail(f"Module '{module_name}' not found")
        for keyword, params in self.modules[module_name]:
            if not self._execute_keyword(keyword, params):
                return False
        return True

    def execute_test_case(self, test_case: str, dry_run: bool = False) -> TestCaseResult:
        start_time = time.time()
        result = TestCaseResult(
            name=test_case, elapsed="0.00s", status="NOT RUN")
        if test_case not in self.test_cases:
            result.status = "FAIL"
            result.elapsed = f"{time.time() - start_time:.2f}s"
            return result

        result.status = "RUNNING"
        for module_name in self.test_cases[test_case]:
            if dry_run:
                if module_name not in self.modules:
                    result.status = "FAIL"
                    break
            else:
                if not self._process_module(module_name):
                    result.status = "FAIL"
                    break
        else:
            result.status = "PASS"
        result.elapsed = f"{time.time() - start_time:.2f}s"
        return result

    def run_all(self) -> None:
        self._run_pytest(list(self.test_cases.keys()))

    def dry_run_test_case(self, test_case: str) -> TestCaseResult:
        return self.execute_test_case(test_case, dry_run=True)

    def dry_run_all(self) -> None:
        self._run_pytest(list(self.test_cases.keys()), dry_run=True)

    def _run_pytest(self, test_cases: List[str], dry_run: bool = False) -> bool:
        temp_dir = tempfile.mkdtemp()
        test_file_path = f"{temp_dir}/test_generated_{int(time.time()*1000)}.py"
        conftest_path = f"{temp_dir}/conftest.py"
        extra = {"test_cases": ", ".join(test_cases)}

        internal_logger.debug(
            f"Generating test file: {test_file_path}", extra=extra)

        with open(conftest_path, "w") as f:
            f.write("""
import pytest
from optics_framework.common.runner.test_runnner import PytestRunner

@pytest.fixture
def runner():
    return PytestRunner.instance
""")

        test_code = "".join(
            f"def test_{tc.replace(' ', '_')}(runner):\n"
            f"    result = runner.execute_test_case('{tc}', dry_run={dry_run})\n"
            f"    assert result.status == 'PASS', 'Test case failed with status: ' + result.status\n"
            for tc in test_cases
        )
        internal_logger.debug(
            f"Generated test code:\n{test_code}", extra=extra)
        with open(test_file_path, "w") as f:
            f.write(test_code)

        for module_name in list(sys.modules.keys()):
            if module_name.startswith("test_generated"):
                del sys.modules[module_name]

        junit_path = f"{ConfigHandler.get_instance().get_project_path()}/execution_output/junit_output.xml"
        result = pytest.main(
            [temp_dir, '-q', '--disable-warnings', f'--junitxml={junit_path}', '--no-cov'])
        shutil.rmtree(temp_dir)
        return result == 0
