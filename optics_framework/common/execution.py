from uuid import uuid4
import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Dict, List
from pydantic import BaseModel, Field, ConfigDict
from optics_framework.common.session_manager import SessionManager, Session
from optics_framework.common.runner.keyword_register import KeywordRegistry
from optics_framework.common.runner.printers import TreeResultPrinter, TerminalWidthProvider, NullResultPrinter
from optics_framework.common.runner.test_runnner import TestRunner, PytestRunner, Runner
from optics_framework.common.logging_config import LoggerContext, internal_logger
from optics_framework.common.models import TestCaseNode, ElementData
from optics_framework.api import ActionKeyword, AppManagement, FlowControl, Verifier


class ExecutionParams(BaseModel):
    """Execution parameters with Pydantic validation."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str = Field(default_factory=lambda: str(uuid4()))
    mode: str
    test_case: Optional[str] = None
    keyword: Optional[str] = None
    params: List[str] = Field(default_factory=list)
    event_queue: Optional[asyncio.Queue] = None
    command_queue: Optional[asyncio.Queue] = None
    test_cases: TestCaseNode
    modules: Dict[str, List[tuple[str, List[str]]]
                  ] = Field(default_factory=dict)
    elements: ElementData = Field(default_factory=ElementData)
    runner_type: str = "test_runner"


class Executor(ABC):
    """Abstract base class for execution strategies."""
    @abstractmethod
    async def execute(self, session: Session, runner: Runner, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> None:
        pass


class BatchExecutor(Executor):
    """Executes batch test cases."""

    def __init__(self, test_case: Optional[str] = None):
        self.test_case = test_case

    async def execute(self, session: Session, runner: Runner, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> None:
        if not runner.test_cases:
            await self._send_event(event_queue, session.session_id, "ERROR", "No test cases loaded")
            raise ValueError("No test cases loaded")

        try:
            if self.test_case:
                result = await runner.execute_test_case(self.test_case, event_queue, command_queue)
                status = "PASS" if result.status == "PASS" else "FAIL"
                message = f"Test case {self.test_case} completed with status {status}"
            else:
                await runner.run_all(event_queue, command_queue)
                all_passed = all(
                    tc.status == "PASS" for tc in runner.result_printer.test_state.values())
                status = "PASS" if all_passed else "FAIL"
                message = "All test cases completed" if all_passed else "Some test cases failed"
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

    async def execute(self, session: Session, runner: Runner, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> None:
        if not runner.test_cases:
            await self._send_event(event_queue, session.session_id, "ERROR", "No test cases loaded")
            raise ValueError("No test cases loaded")

        if self.test_case:
            result = await runner.dry_run_test_case(self.test_case, event_queue, command_queue)
            status = "PASS" if result.status == "PASS" else "FAIL"
            message = f"Dry run for test case {self.test_case} completed with status {status}"
        else:
            await runner.dry_run_all(event_queue, command_queue)
            all_passed = all(
                tc.status == "PASS" for tc in runner.result_printer.test_state.values())
            status = "PASS" if all_passed else "FAIL"
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

    async def execute(self, session: Session, runner: Runner, event_queue: Optional[asyncio.Queue], command_queue: Optional[asyncio.Queue]) -> None:
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


class RunnerFactory:
    """Creates runners with dependency injection."""
    @staticmethod
    def create_runner(
        session: Session,
        runner_type: str,
        use_printer: bool,
        test_cases: TestCaseNode,
        modules: Dict[str, List[tuple[str, List[str]]]],
        elements: Dict[str, str]
    ) -> Runner:

        registry = KeywordRegistry()
        action_keyword = session.optics.build(ActionKeyword)
        app_management = session.optics.build(AppManagement)
        verifier = session.optics.build(Verifier)

        if runner_type == "test_runner":
            result_printer = TreeResultPrinter(
                TerminalWidthProvider()) if use_printer else NullResultPrinter()
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


class ExecutionEngine:
    """Orchestrates execution."""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    async def execute(self, params: ExecutionParams) -> None:
        session = self.session_manager.get_session(params.session_id)
        if not session:
            await self._send_event(params.event_queue, params.session_id, "ERROR", "Session not found")
            raise ValueError("Session not found")

        if not params.test_cases:
            await self._send_event(params.event_queue, params.session_id, "ERROR", "Test cases are required")
            raise ValueError("Test cases are required")

        event_queue = asyncio.Queue()
        command_queue = asyncio.Queue()
        params.event_queue = event_queue
        params.command_queue = command_queue

        # Use TreeResultPrinter for test_runner, NullResultPrinter for pytest
        use_printer = params.event_queue is None or params.runner_type == "test_runner"
        internal_logger.debug(
            f"Using printer: {'TreeResultPrinter' if use_printer else 'NullResultPrinter'} for runner: {params.runner_type}")

        with LoggerContext(params.session_id):
            runner = RunnerFactory.create_runner(
                session,
                params.runner_type,
                use_printer,
                params.test_cases,
                params.modules,
                params.elements.elements
            )
            if hasattr(runner, 'result_printer') and runner.result_printer:
                internal_logger.debug("Starting result printer live display")
                runner.result_printer.start_live()

            try:
                await self._send_event(event_queue, params.session_id, "RUNNING", f"Starting {params.mode} execution")
                if params.mode == "batch":
                    executor = BatchExecutor(test_case=params.test_case)
                elif params.mode == "dry_run":
                    executor = DryRunExecutor(params.test_case)
                elif params.mode == "keyword":
                    if not params.keyword:
                        await self._send_event(event_queue, params.session_id, "ERROR", "Keyword mode requires a keyword")
                        raise ValueError("Keyword mode requires a keyword")
                    executor = KeywordExecutor(params.keyword, params.params)
                else:
                    await self._send_event(event_queue, params.session_id, "ERROR", f"Unknown mode: {params.mode}")
                    raise ValueError(f"Unknown mode: {params.mode}")

                await executor.execute(session, runner, event_queue, command_queue)
            except Exception as e:
                await self._send_event(event_queue, params.session_id, "FAIL", f"Execution failed: {str(e)}")
                raise
            finally:
                if hasattr(runner, 'result_printer') and runner.result_printer:
                    internal_logger.debug(
                        "Stopping result printer live display")
                    runner.result_printer.stop_live()

    async def _send_event(self, queue: Optional[asyncio.Queue], session_id: str, status: str, message: str) -> None:
        if queue:
            await queue.put({"execution_id": session_id, "status": status, "message": message})
