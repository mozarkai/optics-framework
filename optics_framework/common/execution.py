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
from optics_framework.common.events import Event, get_event_manager, EventStatus


class ExecutionParams(BaseModel):
    """Execution parameters with Pydantic validation."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    mode: str
    test_case: Optional[str] = None
    keyword: Optional[str] = None
    params: List[str] = Field(default_factory=list)
    test_cases: TestCaseNode
    modules: Dict[str, List[tuple[str, List[str]]]
                  ] = Field(default_factory=dict)
    elements: ElementData = Field(default_factory=ElementData)
    runner_type: str = "test_runner"


class Executor(ABC):
    """Abstract base class for execution strategies."""
    @abstractmethod
    async def execute(self, session: Session, runner: Runner) -> None:
        pass


class BatchExecutor(Executor):
    """Executes batch test cases."""

    def __init__(self, test_case: Optional[str] = None):
        self.test_case = test_case

    async def execute(self, session: Session, runner: Runner) -> None:
        event_manager = get_event_manager()
        if not runner.test_cases:
            await event_manager.publish_event(Event(
                entity_type="execution",
                entity_id=session.session_id,
                name="Execution",
                status=EventStatus.ERROR,
                message="No test cases loaded",
                extra={"session_id": session.session_id}
            ))
            raise ValueError("No test cases loaded")

        try:
            if self.test_case:
                result = await runner.execute_test_case(self.test_case)
                status = EventStatus.PASS if result.status == "PASS" else EventStatus.FAIL
                message = f"Test case {self.test_case} completed with status {status.value}"
            else:
                await runner.run_all()
                all_passed = all(
                    tc.status == "PASS" for tc in runner.result_printer.test_state.values())
                status = EventStatus.PASS if all_passed else EventStatus.FAIL
                message = "All test cases completed" if all_passed else "Some test cases failed"
            await event_manager.publish_event(Event(
                entity_type="execution",
                entity_id=session.session_id,
                name="Execution",
                status=status,
                message=message,
                extra={"session_id": session.session_id}
            ))
        except Exception as e:
            await event_manager.publish_event(Event(
                entity_type="execution",
                entity_id=session.session_id,
                name="Execution",
                status=EventStatus.FAIL,
                message=f"Execution failed: {str(e)}",
                extra={"session_id": session.session_id}
            ))
            raise


class DryRunExecutor(Executor):
    """Performs dry run of test cases."""

    def __init__(self, test_case: Optional[str] = None):
        self.test_case = test_case

    async def execute(self, session: Session, runner: Runner) -> None:
        event_manager = get_event_manager()
        if not runner.test_cases:
            await event_manager.publish_event(Event(
                entity_type="execution",
                entity_id=session.session_id,
                name="Execution",
                status=EventStatus.ERROR,
                message="No test cases loaded",
                extra={"session_id": session.session_id}
            ))
            raise ValueError("No test cases loaded")

        if self.test_case:
            result = await runner.dry_run_test_case(self.test_case)
            status = EventStatus.PASS if result.status == "PASS" else EventStatus.FAIL
            message = f"Dry run for test case {self.test_case} completed with status {status.value}"
        else:
            await runner.dry_run_all()
            all_passed = all(
                tc.status == "PASS" for tc in runner.result_printer.test_state.values())
            status = EventStatus.PASS if all_passed else EventStatus.FAIL
            message = "All test cases dry run completed"
        await event_manager.publish_event(Event(
            entity_type="execution",
            entity_id=session.session_id,
            name="Execution",
            status=status,
            message=message,
            extra={"session_id": session.session_id}
        ))


class KeywordExecutor(Executor):
    """Executes a single keyword."""

    def __init__(self, keyword: str, params: List[str]):
        self.keyword = keyword
        self.params = params

    async def execute(self, session: Session, runner: Runner) -> None:
        event_manager = get_event_manager()
        method = runner.keyword_map.get("_".join(self.keyword.split()).lower())
        if method:
            method(*self.params)
            await event_manager.publish_event(Event(
                entity_type="keyword",
                entity_id=session.session_id,
                name=self.keyword,
                status=EventStatus.PASS,
                message="Keyword executed successfully",
                extra={"session_id": session.session_id}
            ))
        else:
            await event_manager.publish_event(Event(
                entity_type="keyword",
                entity_id=session.session_id,
                name=self.keyword,
                status=EventStatus.FAIL,
                message="Keyword not found",
                extra={"session_id": session.session_id}
            ))
            raise ValueError(f"Keyword {self.keyword} not found")


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
            runner = TestRunner(
                test_cases, modules, elements, {}, result_printer, session_id=session.session_id
            )
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
        self.event_manager = get_event_manager()

    async def execute(self, params: ExecutionParams) -> None:
        session = self.session_manager.get_session(params.session_id)
        if not session:
            await self.event_manager.publish_event(Event(
                entity_type="session",
                entity_id=params.session_id,
                name="Session",
                status=EventStatus.ERROR,
                message="Session not found",
                extra={"session_id": params.session_id}
            ))
            raise ValueError("Session not found")

        if not params.test_cases:
            await self.event_manager.publish_event(Event(
                entity_type="execution",
                entity_id=params.session_id,
                name="Execution",
                status=EventStatus.ERROR,
                message="Test cases are required",
                extra={"session_id": params.session_id}
            ))
            raise ValueError("Test cases are required")

        self.event_manager.start()

        use_printer = params.runner_type == "test_runner"
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
                await self.event_manager.publish_event(Event(
                    entity_type="execution",
                    entity_id=params.session_id,
                    name="Execution",
                    status=EventStatus.RUNNING,
                    message=f"Starting {params.mode} execution",
                    extra={"session_id": params.session_id}
                ))
                if params.mode == "batch":
                    executor = BatchExecutor(test_case=params.test_case)
                elif params.mode == "dry_run":
                    executor = DryRunExecutor(params.test_case)
                elif params.mode == "keyword":
                    if not params.keyword:
                        await self.event_manager.publish_event(Event(
                            entity_type="execution",
                            entity_id=params.session_id,
                            name="Execution",
                            status=EventStatus.ERROR,
                            message="Keyword mode requires a keyword",
                            extra={"session_id": params.session_id}
                        ))
                        raise ValueError("Keyword mode requires a keyword")
                    executor = KeywordExecutor(params.keyword, params.params)
                else:
                    await self.event_manager.publish_event(Event(
                        entity_type="execution",
                        entity_id=params.session_id,
                        name="Execution",
                        status=EventStatus.ERROR,
                        message=f"Unknown mode: {params.mode}",
                        extra={"session_id": params.session_id}
                    ))
                    raise ValueError(f"Unknown mode: {params.mode}")

                await executor.execute(session, runner)
            except Exception as e:
                await self.event_manager.publish_event(Event(
                    entity_type="execution",
                    entity_id=params.session_id,
                    name="Execution",
                    status=EventStatus.FAIL,
                    message=f"Execution failed: {str(e)}",
                    extra={"session_id": params.session_id}
                ))
                raise
            finally:
                if hasattr(runner, 'result_printer') and runner.result_printer:
                    internal_logger.debug(
                        "Stopping result printer live display")
                    runner.result_printer.stop_live()
                # Wait for event queue to drain
                internal_logger.debug(
                    f"Event queue size before drain: {self.event_manager.event_queue.qsize()}")
                while self.event_manager.event_queue.qsize() > 0:
                    internal_logger.debug(
                        f"Waiting for {self.event_manager.event_queue.qsize()} events to process")
                    await asyncio.sleep(0.1)
                self.event_manager.dump_state()
                self.event_manager.stop()
