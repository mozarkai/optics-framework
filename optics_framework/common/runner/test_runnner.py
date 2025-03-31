import time
import shutil
import abc
from typing import Callable, Dict, List, Optional, Tuple, Union, Any
from pydantic import BaseModel, Field
from optics_framework.common.logging_config import logger, apply_logger_format_to_all, HierarchicalJsonHandler
from rich.live import Live
from rich.tree import Tree
from rich.text import Text
from rich.panel import Panel
from rich.progress import Progress, TaskID
from rich.console import Group


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


class TestCaseResult(BaseModel):
    """Result of a test case execution."""
    name: str
    elapsed: str
    status: str
    modules: List[ModuleResult] = Field(default_factory=list)


class IResultPrinter(abc.ABC):
    @property
    @abc.abstractmethod
    def test_state(self) -> Dict[str, TestCaseResult]:
        pass

    @test_state.setter
    @abc.abstractmethod
    def test_state(self, value: Dict[str, TestCaseResult]) -> None:
        pass

    @abc.abstractmethod
    def print_tree_log(self, test_case_result: TestCaseResult) -> None:
        pass

    @abc.abstractmethod
    def start_live(self) -> None:
        pass

    @abc.abstractmethod
    def stop_live(self) -> None:
        pass

    @abc.abstractmethod
    def start_run(self, total_test_cases: int) -> None:
        pass


class TerminalWidthProvider:
    def get_terminal_width(self, default: int = 80) -> int:
        return shutil.get_terminal_size((default, 20)).columns


@apply_logger_format_to_all("user")
class TreeResultPrinter(IResultPrinter):
    STATUS_COLORS: Dict[str, str] = {
        "NOT RUN": "grey50", "RUNNING": "yellow", "PASS": "green", "FAIL": "red"
    }

    def __init__(self, terminal_width_provider: TerminalWidthProvider) -> None:
        self.terminal_width_provider = terminal_width_provider
        self._live: Optional[Live] = None
        self._test_state: Dict[str, TestCaseResult] = {}
        self.progress = Progress()
        self.task_id: Optional[TaskID] = None

    @property
    def test_state(self) -> Dict[str, TestCaseResult]:
        return self._test_state

    @test_state.setter
    def test_state(self, value: Dict[str, TestCaseResult]) -> None:
        self._test_state = value

    def start_run(self, total_test_cases: int) -> None:
        self.task_id = self.progress.add_task(
            "Running tests", total=total_test_cases)

    def create_label(self, display_name: str, elapsed: str, status: str, level: int) -> Text:
        terminal_width = self.terminal_width_provider.get_terminal_width()
        ELAPSED_WIDTH, STATUS_WIDTH = 10, 10
        SEPARATOR = " | "
        FIXED_FIELDS_WIDTH = len(SEPARATOR) * 2 + ELAPSED_WIDTH + STATUS_WIDTH
        indentation_width = level * 4
        name_width = terminal_width - indentation_width - FIXED_FIELDS_WIDTH - 2

        name_part = display_name[:max(name_width - 3, 0)] + "..." if len(
            display_name) > name_width else display_name.ljust(name_width)
        elapsed_part = "..." + \
            elapsed[-7:] if len(elapsed) > ELAPSED_WIDTH else elapsed.rjust(ELAPSED_WIDTH)
        status_part = status.center(STATUS_WIDTH)

        return Text.assemble(
            Text(name_part, style="bold"),
            SEPARATOR,
            Text(elapsed_part, style="cyan"),
            SEPARATOR,
            Text(status_part, style=self.STATUS_COLORS.get(status, "white"))
        )

    def _render_tree(self) -> Group:
        tree = Tree("Test Suite", style="bold white")
        for tc_result in self.test_state.values():
            test_case_node = tree.add(self.create_label(
                tc_result.name, tc_result.elapsed, tc_result.status, 0))
            for module in tc_result.modules:
                module_node = test_case_node.add(self.create_label(
                    module.name, module.elapsed, module.status, 1))
                for keyword in module.keywords:
                    module_node.add(self.create_label(
                        keyword.resolved_name, keyword.elapsed, keyword.status, 2))

        completed = sum(1 for tc in self.test_state.values()
                        if tc.status in ["PASS", "FAIL"])
        if self.task_id is not None:
            self.progress.update(self.task_id, completed=completed)

        total, passed, failed = len(self.test_state), sum(1 for tc in self.test_state.values(
        ) if tc.status == "PASS"), sum(1 for tc in self.test_state.values() if tc.status == "FAIL")
        summary_text = f"Total Test Cases: {total} | Passed: {passed} | Failed: {failed}"
        summary_panel = Panel(
            summary_text, style="green" if failed == 0 else "red")
        return Group(self.progress, tree, summary_panel)

    def print_tree_log(self, test_case_result: TestCaseResult) -> None:
        self.test_state[test_case_result.name] = test_case_result
        if self._live:
            self._live.update(self._render_tree())

    def start_live(self) -> None:
        if not self._live:
            self._live = Live(self._render_tree(), refresh_per_second=10)
            self._live.start()
            self._live.console.log("Testing started")

    def stop_live(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None


@apply_logger_format_to_all("user")
class TestRunner:
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

    def _init_test_case(self, test_case_name: str) -> TestCaseResult:
        return TestCaseResult(name=test_case_name, elapsed="0.00s", status="NOT RUN")

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
        test_case_result.modules.append(
            module_result)  # pylint: disable=no-member
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

    def execute_test_case(self, test_case_name: str) -> TestCaseResult:
        start_time = time.time()
        extra = self._extra(test_case_name)
        logger.debug("Starting test case execution", extra=extra)
        test_case_result = self._init_test_case(test_case_name)
        self.result_printer.print_tree_log(test_case_result)

        if test_case_name not in self.test_cases:
            logger.error("Test case not found", extra=extra)
            self._update_status(test_case_result, "FAIL",
                                time.time() - start_time)
            self.result_printer.print_tree_log(test_case_result)
            return test_case_result

        self._update_status(test_case_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        for module_name in self.test_cases[test_case_name]:
            if not self._process_module(module_name, test_case_result, self._extra(test_case_name, module_name)):
                return test_case_result

        test_case_result.elapsed = f"{time.time() - start_time:.2f}s"
        test_case_result.status = "PASS" if all(
            m.status == "PASS" for m in test_case_result.modules) else "FAIL"
        logger.debug("Completed test case execution", extra=extra)
        self.result_printer.print_tree_log(test_case_result)
        return test_case_result

    def run_with_logging_tree(self, test_case_name: str) -> None:
        self.result_printer.start_live()
        self.execute_test_case(test_case_name)
        self.result_printer.stop_live()

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
        for test_case_name in test_case_names:
            self.execute_test_case(test_case_name)
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
        test_case_result.modules.append(
            module_result)  # pylint: disable=no-member
        self.result_printer.print_tree_log(test_case_result)

        self._update_status(module_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        for keyword, params in self.modules.get(module_name, []):
            keyword_result = self._init_keyword(keyword)
            module_result.keywords.append(  # pylint: disable=no-member
                keyword_result)
            extra["keyword"] = keyword
            if not self._dry_run_keyword(keyword, params, keyword_result, module_result, test_case_result, extra):
                return False
            module_result.status = "PASS" if all(
                k.status == "PASS" for k in module_result.keywords) else "FAIL"
            module_result.elapsed = "0.00s"
            self.result_printer.print_tree_log(test_case_result)
        return True

    def dry_run_test_case(self, test_case_name: str) -> TestCaseResult:
        start_time = time.time()
        extra = self._extra(test_case_name)
        logger.debug("Starting dry run", extra=extra)
        test_case_result = self._init_test_case(test_case_name)
        self.result_printer.print_tree_log(test_case_result)

        if test_case_name not in self.test_cases:
            logger.warning(
                f"Test case '{test_case_name}' not found.", extra=extra)
            self._update_status(test_case_result, "FAIL",
                                time.time() - start_time)
            self.result_printer.print_tree_log(test_case_result)
            return test_case_result

        self._update_status(test_case_result, "RUNNING")
        self.result_printer.print_tree_log(test_case_result)

        for module_name in self.test_cases[test_case_name]:
            if not self._dry_run_module(module_name, test_case_result, self._extra(test_case_name, module_name)):
                return test_case_result

        test_case_result.elapsed = f"{time.time() - start_time:.2f}s"
        test_case_result.status = "PASS" if all(
            m.status == "PASS" for m in test_case_result.modules) else "FAIL"
        logger.debug("Completed dry run", extra=extra)
        self.result_printer.print_tree_log(test_case_result)
        return test_case_result

    def dry_run_with_logging_tree(self, test_case_name: str) -> None:
        self.result_printer.start_live()
        self.dry_run_test_case(test_case_name)
        self.result_printer.stop_live()

    def dry_run_all(self, test_case_names: Union[str, List[str]] = "") -> None:
        if isinstance(test_case_names, str):
            test_case_names = list(self.test_cases.keys()) if test_case_names == "" else [
                test_case_names]
        self.result_printer.start_live()
        for test_case_name in test_case_names:
            self.dry_run_test_case(test_case_name)
        self.result_printer.stop_live()
