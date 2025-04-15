from uuid import uuid4
import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Dict, List
from pydantic import BaseModel, Field, ConfigDict
from optics_framework.common.session_manager import SessionManager, Session
from optics_framework.common.runner.keyword_register import KeywordRegistry
from optics_framework.api import ActionKeyword, AppManagement, FlowControl, Verifier
from optics_framework.common.runner.printers import TreeResultPrinter, TerminalWidthProvider, NullResultPrinter, TestCaseResult
from optics_framework.common.runner.test_runnner import TestRunner, PytestRunner, Runner

NO_TEST_CASES_LOADED = "No test cases loaded"

# Data Models
class TestCaseData(BaseModel):
    """Structure for test cases."""
    test_cases: Dict[str, List[str]] = Field(default_factory=dict)


class ModuleData(BaseModel):
    """Structure for modules."""
    modules: Dict[str, List[tuple[str, List[str]]]
                  ] = Field(default_factory=dict)


class ElementData(BaseModel):
    """Structure for elements."""
    elements: Dict[str, str] = Field(default_factory=dict)


class ExecutionParams(BaseModel):
    """Execution parameters with Pydantic validation."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str = Field(default_factory=lambda: str(uuid4()))
    mode: str
    test_case: Optional[str] = None
    keyword: Optional[str] = None
    params: List[str] = Field(default_factory=list)
    event_queue: Optional[asyncio.Queue] = None
    test_cases: TestCaseData = Field(default_factory=TestCaseData)
    modules: ModuleData = Field(default_factory=ModuleData)
    elements: ElementData = Field(default_factory=ElementData)
    runner_type: str = "test_runner"


# Abstract Executor
class Executor(ABC):
    """Abstract base class for execution strategies."""
    @abstractmethod
    async def execute(self, session: Session, runner: Runner, event_queue: Optional[asyncio.Queue]) -> None:
        pass


# Concrete Executors
class BatchExecutor(Executor):
    """Executes batch test cases."""

    def __init__(self, test_case: Optional[str] = None):
        self.test_case = test_case

    async def execute(self, session: Session, runner: Runner, event_queue: Optional[asyncio.Queue]) -> None:
        if not runner.test_cases:
            await self._send_event(event_queue, session.session_id, "ERROR", NO_TEST_CASES_LOADED)
            raise ValueError(NO_TEST_CASES_LOADED)


        try:

            if self.test_case:
                if self.test_case not in runner.test_cases:
                    await self._send_event(event_queue, session.session_id, "ERROR", f"Test case {self.test_case} not found")
                    raise ValueError(f"Test case {self.test_case} not found")

                result = runner.execute_test_case(self.test_case)
                is_result_test_case = isinstance(result, TestCaseResult)
                is_result_pass = is_result_test_case and result.status == "PASS"
                is_result_dict = isinstance(result, dict)

                if is_result_pass:
                        status = "PASS"
                elif is_result_dict:
                    status = result.get("status", "FAIL")
                else:
                    status = "FAIL"

                message = f"Test case {self.test_case} completed with status {status}"
            else:
                runner.run_all()
                all_tests_passed = all(
                    tc.status == "PASS" for tc in runner.result_printer.test_state.values())
            if all_tests_passed:
                status = "PASS"
                message = "All test cases completed successfully"
            else:
                status = "FAIL"
                message = "All test cases completed"


            await self._send_event(event_queue, session.session_id, status, message)
        except Exception as e:
            await self._send_event(event_queue, session.session_id, "FAIL", f"Execution failed: {str(e)}")
            raise

    async def _send_event(self, queue: Optional[asyncio.Queue], session_id: str, status: str, message: str) -> None:
        if queue:
            await queue.put({"execution_id": session_id, "status": status, "message": message})


class DryRunExecutor(Executor):
    """Performs dry run of test cases."""

    def __init__(self, test_case: Optional[str] = None):
        self.test_case = test_case

    async def execute(self, session: Session, runner: Runner, event_queue: Optional[asyncio.Queue]) -> None:
        if not runner.test_cases:
            await self._send_event(event_queue, session.session_id, "ERROR", NO_TEST_CASES_LOADED)
            raise ValueError(NO_TEST_CASES_LOADED)

        if self.test_case:
            if self.test_case not in runner.test_cases:
                await self._send_event(event_queue, session.session_id, "ERROR", f"Test case {self.test_case} not found")
                raise ValueError(f"Test case {self.test_case} not found")

            result = runner.dry_run_test_case(self.test_case)
            is_result_test_case = isinstance(result, TestCaseResult)
            is_result_pass = is_result_test_case and result.status == "PASS"
            is_result_dict = isinstance(result, dict)

            if is_result_pass:
                status = "PASS"
            elif is_result_dict:
                status = result.get("status", "FAIL")
            else:
                status = "FAIL"

            message = f"Dry run for test case {self.test_case} completed with status {status}"
        else:
            runner.dry_run_all()
            all_tests_passed = all(
                tc.status == "PASS" for tc in runner.result_printer.test_state.values())
            if all_tests_passed:
                status = "PASS"
            else:
                status = "FAIL"
            message = "All test cases dry run completed"

        await self._send_event(event_queue, session.session_id, status, message)

    async def _send_event(self, queue: Optional[asyncio.Queue], session_id: str, status: str, message: str) -> None:
        if queue:
            await queue.put({"execution_id": session_id, "status": status, "message": message})


class KeywordExecutor(Executor):
    """Executes a single keyword."""

    def __init__(self, keyword: str, params: List[str]):
        self.keyword = keyword
        self.params = params

    async def execute(self, session: Session, runner: Runner, event_queue: Optional[asyncio.Queue]) -> None:
        method = runner.keyword_map.get("_".join(self.keyword.split()).lower())
        if method:
            method(*self.params)
            await self._send_event(event_queue, session.session_id, "PASS", "Keyword executed successfully", self.keyword)
        else:
            await self._send_event(event_queue, session.session_id, "FAIL", "Keyword not found", self.keyword)
            raise ValueError(f"Keyword {self.keyword} not found")

    async def _send_event(self, queue: Optional[asyncio.Queue], session_id: str, status: str, message: str, keyword: Optional[str] = None) -> None:
        if queue:
            event = {"execution_id": session_id,
                     "status": status, "message": message}
            if keyword:
                event["keyword"] = keyword
            await queue.put(event)


# Factory
class RunnerFactory:
    """Creates runners with dependency injection."""
    @staticmethod
    def create_runner(
        session: Session,
        runner_type: str,
        use_printer: bool,
        test_cases: Dict[str, List[str]],
        modules: Dict[str, List[tuple[str, List[str]]]],
        elements: Dict[str, str]
    ) -> Runner:
        registry = KeywordRegistry()
        action_keyword = session.optics.build(ActionKeyword)
        app_management = session.optics.build(AppManagement)
        verifier = session.optics.build(Verifier)

        if runner_type == "test_runner":
            if use_printer:
                result_printer = TreeResultPrinter(TerminalWidthProvider())
            else:
                result_printer = NullResultPrinter()
            runner = TestRunner(test_cases, modules,
                                elements, {}, result_printer)
            flow_control = FlowControl(runner, modules)
        elif runner_type == "pytest":
            runner = PytestRunner(session, test_cases, modules, elements, {})
            flow_control = FlowControl(runner, modules)
        else:
            raise ValueError(f"Unknown runner type: {runner_type}")

        registry.register(action_keyword)
        registry.register(app_management)
        registry.register(flow_control)
        registry.register(verifier)
        runner.keyword_map = registry.keyword_map
        return runner


# Engine
class ExecutionEngine:
    """Orchestrates execution."""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    async def execute(self, params: ExecutionParams) -> None:
        session = self.session_manager.get_session(params.session_id)
        if not session:
            await self._send_event(params.event_queue, params.session_id, "ERROR", "Session not found")
            raise ValueError("Session not found")

        runner = RunnerFactory.create_runner(
            session, params.runner_type, params.event_queue is None,
            params.test_cases.test_cases, params.modules.modules, params.elements.elements
        )
        if hasattr(runner, 'result_printer') and runner.result_printer:
            runner.result_printer.start_live()

        if params.mode == "batch":
            executor = BatchExecutor(test_case=params.test_case)
        elif params.mode == "dry_run":
            executor = DryRunExecutor(params.test_case)
        elif params.mode == "keyword":
            if not params.keyword:
                await self._send_event(params.event_queue, params.session_id, "ERROR", "Keyword mode requires a keyword")
                raise ValueError("Keyword mode requires a keyword")
            executor = KeywordExecutor(params.keyword, params.params)
        else:
            await self._send_event(params.event_queue, params.session_id, "ERROR", f"Unknown mode: {params.mode}")
            raise ValueError(f"Unknown mode: {params.mode}")

        try:
            await self._send_event(params.event_queue, params.session_id, "RUNNING", f"Starting {params.mode} execution")
            await executor.execute(session, runner, params.event_queue)
        except Exception as e:
            await self._send_event(params.event_queue, params.session_id, "FAIL", f"Execution failed: {str(e)}")
            raise
        finally:
            if hasattr(runner, 'result_printer') and runner.result_printer:
                runner.result_printer.stop_live()

    async def _send_event(self, queue: Optional[asyncio.Queue], session_id: str, status: str, message: str) -> None:
        if queue:
            await queue.put({"execution_id": session_id, "status": status, "message": message})


# Main execution example
async def main():
    session_manager = SessionManager()
    engine = ExecutionEngine(session_manager)
    params = ExecutionParams(
        mode="batch",
        test_cases=TestCaseData(test_cases={"test1": ["step1"]}),
        modules=ModuleData(modules={"step1": [("click", ["button"])]}),
        elements=ElementData(elements={"button": "btn1"})
    )
    await engine.execute(params)
