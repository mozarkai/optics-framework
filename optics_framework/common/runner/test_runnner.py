import time
import asyncio
from abc import ABC, abstractmethod
import tempfile
import shutil
import sys
from typing import Callable, Dict, List, Optional, Tuple, Union, Any
import pytest
from pydantic import BaseModel, Field
from optics_framework.common.session_manager import Session
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.runner.printers import IResultPrinter, TestCaseResult
from optics_framework.common.models import TestCaseNode, ModuleNode, KeywordNode, State, Node


class KeywordResult(BaseModel):
    id: str
    name: str
    resolved_name: str
    elapsed: str
    status: str
    reason: str


class ModuleResult(BaseModel):
    name: str
    elapsed: str
    status: str
    keywords: List[KeywordResult] = Field(default_factory=list)


class Runner(ABC):
    """Abstract base class for test runners with explicit attributes."""
    test_cases: TestCaseNode
    result_printer: IResultPrinter
    keyword_map: Dict[str, Callable[..., Any]]

    @abstractmethod
    async def execute_test_case(self, test_case: str, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> TestCaseResult:
        pass

    @abstractmethod
    async def run_all(self, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> None:
        pass

    @abstractmethod
    async def dry_run_test_case(self, test_case: str, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> TestCaseResult:
        pass

    @abstractmethod
    async def dry_run_all(self, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> None:
        pass


class TestRunner(Runner):
    def __init__(
        self,
        test_cases: TestCaseNode,
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
        self.config = ConfigHandler.get_instance().config
        self._initialize_test_state()

    def _initialize_test_state(self) -> None:
        """Pre-populate test_state with all test cases, modules, and keywords as NOT RUN."""
        test_state = {}
        current_test = self.test_cases
        while current_test:
            test_result = TestCaseResult(
                name=current_test.name, elapsed="0.00s", status="NOT RUN", modules=[])
            current_module = current_test.modules_head
            while current_module:
                module_result = ModuleResult(
                    name=current_module.name, elapsed="0.00s", status="NOT RUN", keywords=[])
                current_keyword = current_module.keywords_head
                while current_keyword:
                    resolved_params = [self.resolve_param(
                        param) for param in current_keyword.params]
                    resolved_name = f"{current_keyword.name} ({', '.join(str(p) for p in resolved_params)})" if resolved_params else current_keyword.name
                    keyword_result = KeywordResult(
                        id=current_keyword.id,
                        name=current_keyword.name,
                        resolved_name=resolved_name,
                        elapsed="0.00s",
                        status="NOT RUN",
                        reason=""
                    )
                    module_result.keywords.append(keyword_result)
                    current_keyword = current_keyword.next
                test_result.modules.append(module_result)
                current_module = current_module.next
            test_state[current_test.name] = test_result
            current_test = current_test.next
        self.result_printer.test_state = test_state
        internal_logger.debug(
            f"Initialized test_state: {list(test_state.keys())} with {sum(len(m.modules) for m in test_state.values())} modules")

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
        return self.result_printer.test_state.get(test_case, TestCaseResult(name=test_case, elapsed="0.00s", status="NOT RUN"))

    def _find_result(self, test_case_name: str, module_name: Optional[str] = None, keyword_id: Optional[str] = None) -> Union[TestCaseResult, ModuleResult, KeywordResult]:
        """Find the result object in test_state by test case, module, and keyword id."""
        test_result = self.result_printer.test_state.get(test_case_name)
        if not test_result:
            raise ValueError(
                f"Test case {test_case_name} not found in test_state")
        if module_name is None:
            return test_result
        for module_result in test_result.modules:
            if module_result.name == module_name:
                if keyword_id is None:
                    return module_result
                for keyword_result in module_result.keywords:
                    if keyword_result.id == keyword_id:
                        internal_logger.debug(
                            f"Found keyword: {keyword_result.name} (id: {keyword_id})")
                        return keyword_result
                raise ValueError(
                    f"Keyword id {keyword_id} not found in module {module_name}")
        raise ValueError(f"Module {module_name} not found in test_state")

    def _update_status(self, result: Union[TestCaseResult, ModuleResult, KeywordResult], status: str, elapsed: Optional[float] = None, test_case_name: str = "") -> None:
        result.status = status
        if elapsed is not None:
            result.elapsed = f"{elapsed:.2f}s"
        if test_case_name:
            internal_logger.debug(
                f"Updating tree log for {result.__class__.__name__}: {result.name} -> {status}")
            test_case_result = self.result_printer.test_state.get(
                test_case_name)
            if test_case_result:
                self.result_printer.print_tree_log(test_case_result)

    async def _send_event(self, queue: Optional[asyncio.Queue], entity_type: str, node: Node, status: str, reason: Optional[str] = None) -> None:
        if queue:
            await queue.put({
                "entity_type": entity_type,
                "entity_id": node.id,
                "name": node.name,
                "status": status,
                "reason": reason or "",
                "attempt_count": node.attempt_count
            })

    async def _process_commands(self, command_queue: Optional[asyncio.Queue], node: KeywordNode, parent: Optional[ModuleNode]) -> bool:
        if not command_queue:
            return False
        retry = False
        while not command_queue.empty():
            command = await command_queue.get()
            if command["command"] == "Retry" and command["entity_id"] == node.id:
                node.state = State.RETRYING
                node.attempt_count += 1
                retry = True
            elif command["command"] == "Add" and parent and command["parent_id"] == parent.id:
                new_node = KeywordNode(
                    name=command["name"], params=command.get("params", []))
                new_node.next = node.next
                node.next = new_node
        return retry

    async def _execute_keyword(
        self,
        keyword_node: KeywordNode,
        module_node: ModuleNode,
        test_case_result: TestCaseResult,
        extra: Dict[str, str],
        event_queue: Optional[asyncio.Queue],
        command_queue: Optional[asyncio.Queue]
    ) -> bool:
        keyword_result = self._find_result(
            test_case_result.name, module_node.name, keyword_node.id)
        internal_logger.debug(
            f"Executing keyword: {keyword_node.name} (id: {keyword_node.id})")
        start_time = time.time()

        keyword_node.state = State.RUNNING
        await self._send_event(event_queue, "keyword", keyword_node, "KeywordStart")
        self._update_status(keyword_result, "RUNNING",
                            time.time() - start_time, test_case_result.name)

        func_name = "_".join(keyword_node.name.split()).lower()
        method = self.keyword_map.get(func_name)
        if not method:
            keyword_node.state = State.ERROR
            keyword_node.last_failure_reason = "Keyword not found"
            await self._send_event(event_queue, "keyword", keyword_node, "KeywordFail", "Keyword not found")
            self._update_status(keyword_result, "FAIL",
                                time.time() - start_time, test_case_result.name)
            return False

        try:
            raw_indices = getattr(method, '_raw_param_indices', [])
            resolved_params = [param if i in raw_indices else self.resolve_param(
                param) for i, param in enumerate(keyword_node.params)]
            if isinstance(keyword_result, KeywordResult):
                keyword_result.resolved_name = f"{keyword_node.name} ({', '.join(str(p) for p in resolved_params)})" if resolved_params else keyword_node.name
            method(*resolved_params)
            keyword_node.state = State.COMPLETED_PASSED
            await self._send_event(event_queue, "keyword", keyword_node, "KeywordPass")
            self._update_status(keyword_result, "PASS",
                                time.time() - start_time, test_case_result.name)
            return True
        except Exception as e:
            keyword_node.state = State.COMPLETED_FAILED
            keyword_node.last_failure_reason = str(e)
            await self._send_event(event_queue, "keyword", keyword_node, "KeywordFail", str(e))
            self._update_status(keyword_result, "FAIL",
                                time.time() - start_time, test_case_result.name)

            if keyword_node.attempt_count < self.config.max_attempts:
                await asyncio.sleep(self.config.halt_duration)
                if await self._process_commands(command_queue, keyword_node, module_node):
                    return await self._execute_keyword(keyword_node, module_node, test_case_result, extra, event_queue, command_queue)
            return False

    async def _process_module(self, module_node: ModuleNode, test_case_result: TestCaseResult, extra: Dict[str, str], event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> bool:
        module_result = self._find_result(
            test_case_result.name, module_node.name)
        start_time = time.time()
        module_node.state = State.RUNNING
        await self._send_event(event_queue, "module", module_node, "ModuleStart")
        self._update_status(module_result, "RUNNING",
                            time.time() - start_time, test_case_result.name)

        current = module_node.keywords_head
        while current:
            extra["keyword"] = current.name
            if not await self._execute_keyword(current, module_node, test_case_result, extra, event_queue, command_queue):
                module_node.state = State.COMPLETED_FAILED
                await self._send_event(event_queue, "module", module_node, "ModuleFail")
                self._update_status(
                    module_result, "FAIL", time.time() - start_time, test_case_result.name)
                return False
            current = current.next

        module_node.state = State.COMPLETED_PASSED
        await self._send_event(event_queue, "module", module_node, "ModulePass")
        self._update_status(module_result, "PASS",
                            time.time() - start_time, test_case_result.name)
        return True

    async def execute_test_case(self, test_case: str, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> TestCaseResult:
        start_time = time.time()
        extra = self._extra(test_case)
        test_case_result = self._init_test_case(test_case)
        current = self.test_cases
        while current and current.name != test_case:
            current = current.next
        if not current:
            self._update_status(test_case_result, "FAIL",
                                time.time() - start_time, test_case_result.name)
            return test_case_result

        current.state = State.RUNNING
        await self._send_event(event_queue, "test_case", current, "TestCaseStart")
        self._update_status(test_case_result, "RUNNING",
                            time.time() - start_time, test_case_result.name)

        module_current = current.modules_head
        while module_current:
            if not await self._process_module(module_current, test_case_result, extra, event_queue, command_queue):
                current.state = State.COMPLETED_FAILED
                await self._send_event(event_queue, "test_case", current, "TestCaseFail")
                self._update_status(
                    test_case_result, "FAIL", time.time() - start_time, test_case_result.name)
                return test_case_result
            module_current = module_current.next

        current.state = State.COMPLETED_PASSED
        await self._send_event(event_queue, "test_case", current, "TestCasePass")
        self._update_status(test_case_result, "PASS",
                            time.time() - start_time, test_case_result.name)
        return test_case_result

    async def run_all(self, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> None:
        current = self.test_cases
        self.result_printer.start_run(len(self.result_printer.test_state))
        self.result_printer.start_live()
        while current:
            await self.execute_test_case(current.name, event_queue, command_queue)
            current = current.next
        self.result_printer.stop_live()

    async def dry_run_test_case(self, test_case: str, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> TestCaseResult:
        start_time = time.time()
        extra = self._extra(test_case)
        test_case_result = self._init_test_case(test_case)
        current = self.test_cases
        while current and current.name != test_case:
            current = current.next
        if not current:
            self._update_status(test_case_result, "FAIL",
                                time.time() - start_time, test_case_result.name)
            return test_case_result

        current.state = State.RUNNING
        await self._send_event(event_queue, "test_case", current, "TestCaseStart")
        self._update_status(test_case_result, "RUNNING",
                            time.time() - start_time, test_case_result.name)

        module_current = current.modules_head
        while module_current:
            module_result = self._find_result(
                test_case_result.name, module_current.name)
            module_current.state = State.RUNNING
            await self._send_event(event_queue, "module", module_current, "ModuleStart")
            self._update_status(module_result, "RUNNING",
                                0.0, test_case_result.name)

            keyword_current = module_current.keywords_head
            while keyword_current:
                keyword_result = self._find_result(
                    test_case_result.name, module_current.name, keyword_current.id)
                keyword_current.state = State.RUNNING
                await self._send_event(event_queue, "keyword", keyword_current, "KeywordStart")
                self._update_status(keyword_result, "RUNNING",
                                    0.0, test_case_result.name)

                try:
                    resolved_params = [self.resolve_param(
                        param) for param in keyword_current.params]
                    if isinstance(keyword_result, KeywordResult):
                        keyword_result.resolved_name = f"{keyword_current.name} ({', '.join(resolved_params)})" if resolved_params else keyword_current.name
                except ValueError as e:
                    keyword_current.state = State.COMPLETED_FAILED
                    await self._send_event(event_queue, "keyword", keyword_current, "KeywordFail", str(e))
                    self._update_status(
                        keyword_result, "FAIL", 0.0, test_case_result.name)
                    self._update_status(
                        module_result, "FAIL", 0.0, test_case_result.name)
                    self._update_status(
                        test_case_result, "FAIL", time.time() - start_time, test_case_result.name)
                    return test_case_result

                func_name = "_".join(keyword_current.name.split()).lower()
                if func_name not in self.keyword_map:
                    keyword_current.state = State.COMPLETED_FAILED
                    await self._send_event(event_queue, "keyword", keyword_current, "KeywordFail", "Keyword not found")
                    self._update_status(
                        keyword_result, "FAIL", 0.0, test_case_result.name)
                    self._update_status(
                        module_result, "FAIL", 0.0, test_case_result.name)
                    self._update_status(
                        test_case_result, "FAIL", time.time() - start_time, test_case_result.name)
                    return test_case_result

                keyword_current.state = State.COMPLETED_PASSED
                await self._send_event(event_queue, "keyword", keyword_current, "KeywordPass")
                self._update_status(keyword_result, "PASS",
                                    0.0, test_case_result.name)
                keyword_current = keyword_current.next

            module_current.state = State.COMPLETED_PASSED
            await self._send_event(event_queue, "module", module_current, "ModulePass")
            self._update_status(module_result, "PASS",
                                0.0, test_case_result.name)
            module_current = module_current.next

        current.state = State.COMPLETED_PASSED
        await self._send_event(event_queue, "test_case", current, "TestCasePass")
        self._update_status(test_case_result, "PASS",
                            time.time() - start_time, test_case_result.name)
        return test_case_result

    async def dry_run_all(self, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> None:
        current = self.test_cases
        self.result_printer.start_run(len(self.result_printer.test_state))
        self.result_printer.start_live()
        while current:
            await self.dry_run_test_case(current.name, event_queue, command_queue)
            current = current.next
        self.result_printer.stop_live()


class PytestRunner(Runner):
    instance = None

    def __init__(self, session: Session, test_cases: TestCaseNode, modules: Dict, elements: Dict, keyword_map: Dict):
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

    async def execute_test_case(self, test_case: str, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> TestCaseResult:
        start_time = time.time()
        result = TestCaseResult(
            name=test_case, elapsed="0.00s", status="NOT RUN")
        current = self.test_cases
        while current and current.name != test_case:
            current = current.next
        if not current:
            result.status = "FAIL"
            result.elapsed = f"{time.time() - start_time:.2f}s"
            return result

        result.status = "RUNNING"
        module_current = current.modules_head
        while module_current:
            if not self._process_module(module_current.name):
                result.status = "FAIL"
                break
            module_current = module_current.next
        else:
            result.status = "PASS"
        result.elapsed = f"{time.time() - start_time:.2f}s"
        return result

    async def run_all(self, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> None:
        await self._run_pytest([node.name for node in self._iter_test_cases()])

    async def dry_run_test_case(self, test_case: str, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> TestCaseResult:
        return await self.execute_test_case(test_case, event_queue, command_queue)

    async def dry_run_all(self, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> None:
        await self._run_pytest([node.name for node in self._iter_test_cases()], dry_run=True)

    def _iter_test_cases(self):
        current = self.test_cases
        while current:
            yield current
            current = current.next

    async def _run_pytest(self, test_cases: List[str], dry_run: bool = False) -> bool:
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
            f"@pytest.mark.asyncio\n"
            f"async def test_{tc.replace(' ', '_')}(runner):\n"
            f"    result = await runner.execute_test_case('{tc}', None, None)\n"
            f"    assert result.status == 'PASS', f'Test case failed with status: {{result.status}}'\n"
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
            [temp_dir, '-q', '--disable-warnings',
                f'--junitxml={junit_path}', '--no-cov'],
            plugins=["pytest_asyncio"]
        )
        shutil.rmtree(temp_dir)
        return result == 0
