from uuid import uuid4
import asyncio
import os
import json
import inspect
from abc import ABC, abstractmethod
from typing import Optional, List, Any, Callable
from pydantic import BaseModel, Field, ConfigDict
from optics_framework.common.session_manager import SessionManager, Session
from optics_framework.common.runner.keyword_register import KeywordRegistry
from optics_framework.common.runner.printers import TreeResultPrinter, TerminalWidthProvider, NullResultPrinter
from optics_framework.common.runner.test_runnner import TestRunner, PytestRunner, Runner, KeywordRunner
from optics_framework.common.logging_config import LoggerContext, internal_logger
from optics_framework.common.models import TestCaseNode
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.utils import _is_list_type
from optics_framework.api import ActionKeyword, AppManagement, FlowControl, Verifier
from optics_framework.common.events import Event, EventManager, EventStatus, get_event_manager

NO_TEST_CASES_LOADED = "No test cases loaded"

class ExecutionParams(BaseModel):
    """Execution parameters with Pydantic validation."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    mode: str
    keyword: Optional[str] = None
    params: List[str] = Field(default_factory=list)
    # test_cases, modules, elements, apis are now part of Session
    runner_type: str = "test_runner"
    use_printer: bool = True


class Executor(ABC):
    """Abstract base class for execution strategies."""
    @abstractmethod
    async def execute(self, session: Session, runner: Runner) -> None:
        pass


class BatchExecutor(Executor):
    """Executes batch test cases."""

    def __init__(self, test_case: Optional[TestCaseNode], event_manager: EventManager):
        self.test_case = test_case
        self.event_manager = event_manager
        if not self.test_case:
            raise OpticsError(Code.E0702, message="Test case is required")

    async def execute(self, session: Session, runner: Runner) -> None:
        event_manager = self.event_manager
        if not session.test_cases:
            await event_manager.publish_event(Event(
                entity_type="execution",
                entity_id=session.session_id,
                name="Execution",
                status=EventStatus.ERROR,
                message="NO_TEST_CASES_LOADED",
                extra={"session_id": session.session_id}
            ))
            raise OpticsError(Code.E0702, message="NO_TEST_CASES_LOADED")

        try:
            status = EventStatus.NOT_RUN
            message = "No test case to execute"
            if self.test_case:
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
                message="Execution failed: %s" % str(e),
                extra={"session_id": session.session_id}
            ))
            raise


class DryRunExecutor(Executor):
    """Performs dry run of test cases."""

    def __init__(self, test_case: Optional[TestCaseNode], event_manager: EventManager):
        self.test_case = test_case
        self.event_manager = event_manager
        if not self.test_case:
            raise OpticsError(Code.E0702, message="Test case is required")

    async def execute(self, session: Session, runner: Runner) -> None:
        status = EventStatus.FAIL
        message = "No test case to execute"
        event_manager = self.event_manager
        if not session.test_cases:
            await event_manager.publish_event(Event(
                entity_type="execution",
                entity_id=session.session_id,
                name="Execution",
                status=EventStatus.ERROR,
                message="NO_TEST_CASES_LOADED",
                extra={"session_id": session.session_id}
            ))
            raise OpticsError(Code.E0702, message=NO_TEST_CASES_LOADED)

        if self.test_case:
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


def _deserialize_single_param(param_value: str, param_type: Any) -> Any:
    """Deserialize one param when its type is a list; otherwise return as-is."""
    if param_type is None or not _is_list_type(param_type):
        return param_value
    try:
        parsed = json.loads(param_value)
        return parsed if isinstance(parsed, list) else param_value
    except (json.JSONDecodeError, TypeError):
        return param_value


def _deserialize_params(method: Callable[..., Any], params: List[str]) -> List[Any]:
    """
    Deserialize parameters based on method signature.
    JSON-encoded lists are deserialized back to Python lists.

    Args:
        method: The keyword method
        params: List of string parameters

    Returns:
        List of deserialized parameters
    """
    sig = inspect.signature(method)
    param_types = [p.annotation for p in sig.parameters.values() if p.name != "self"]
    return [
        _deserialize_single_param(params[i], param_types[i] if i < len(param_types) else None)
        for i in range(len(params))
    ]


class KeywordExecutor(Executor):
    """Executes a single keyword."""

    def __init__(self, keyword: str, params: List[str], event_manager: EventManager):
        self.keyword = keyword
        self.params = params
        self.event_manager = event_manager

    async def execute(self, session: Session, runner: Runner) -> None:
        event_manager = self.event_manager
        method = runner.keyword_map.get("_".join(self.keyword.split()).lower())
        result = None
        if method:
            try:
                # Deserialize parameters based on method signature
                deserialized_params = _deserialize_params(method, self.params)
                result = method(*deserialized_params)
            except Exception as e:
                await event_manager.publish_event(Event(
                    entity_type="keyword",
                    entity_id=session.session_id,
                    name=self.keyword,
                    status=EventStatus.FAIL,
                    message="Keyword execution failed: %s" % str(e),
                    extra={"session_id": session.session_id}
                ))
                raise OpticsError(Code.E0401, message=f"Keyword execution failed: {str(e)}") from e
            # Same read-and-clear side channel TestRunner/LiveController use: whichever
            # ActionKeyword instance the bound method belongs to may have just recorded a
            # successful self-heal, which is otherwise invisible here (the keyword call
            # itself returns normally either way).
            heal_owner = getattr(method, "__self__", None)
            heal_info = heal_owner._pop_last_heal_info() if hasattr(heal_owner, "_pop_last_heal_info") else None
            extra = {"session_id": session.session_id}
            if heal_info:
                extra["healed"] = "true"
                extra["heal_summary"] = heal_info["summary"]
            await event_manager.publish_event(Event(
                entity_type="keyword",
                entity_id=session.session_id,
                name=self.keyword,
                status=EventStatus.PASS,
                message="Keyword executed successfully",
                extra=extra,
            ))
            if heal_info:
                # "result" mirrors expose_api.KEY_RESULT; execution.py is a lower layer
                # and shouldn't import the HTTP-facing module, so the literal is kept in
                # sync by convention rather than a shared import.
                return {"result": result, "healed": True, "heal_summary": heal_info["summary"]}
            return result
        else:
            await event_manager.publish_event(Event(
                entity_type="keyword",
                entity_id=session.session_id,
                name=self.keyword,
                status=EventStatus.FAIL,
                message="Keyword not found",
                extra={"session_id": session.session_id}
            ))
            raise OpticsError(Code.E0402, message=f"Keyword {self.keyword} not found")


class RunnerFactory:
    """Creates runners with dependency injection."""
    @staticmethod
    def create_runner(
        session: Session,
        runner_type: str,
        use_printer: bool,
        event_manager: EventManager
    ) -> Runner:

        registry = KeywordRegistry()
        action_keyword = session.optics.build(ActionKeyword)
        app_management = session.optics.build(AppManagement)
        verifier = session.optics.build(Verifier)
        registry.register(action_keyword)
        registry.register(app_management)
        registry.register(verifier)
        registry.register(FlowControl(session=session, keyword_map=registry.keyword_map))

        if runner_type == "test_runner":
            result_printer = TreeResultPrinter.get_instance(
                TerminalWidthProvider()) if use_printer else NullResultPrinter()
            runner = TestRunner(
                session, registry.keyword_map, result_printer, event_manager=event_manager
            )
        elif runner_type == "pytest":
            runner = PytestRunner(
                session, registry.keyword_map, event_manager=event_manager
            )
        elif runner_type == "keyword_runner":
            runner = KeywordRunner(registry.keyword_map)
        else:
            raise OpticsError(Code.E0601, message=f"Unknown runner type: {runner_type}")
        return runner


def _execution_event(
    session_id: str, status: EventStatus, message: str
) -> Event:
    """Build a standard execution Event."""
    return Event(
        entity_type="execution",
        entity_id=session_id,
        name="Execution",
        status=status,
        message=message,
        extra={"session_id": session_id},
    )


class ExecutionEngine:
    """Orchestrates execution."""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager
        self._event_drain_timeout_s = float(os.getenv("OPTICS_EVENT_DRAIN_TIMEOUT_S", "2.0"))

    async def _validate_execution_params(
        self,
        session: Optional[Session],
        params: ExecutionParams,
        event_manager: EventManager,
    ) -> None:
        """Validate session and test cases; publish errors and raise if invalid."""
        if not session:
            await event_manager.publish_event(Event(
                entity_type="session",
                entity_id=params.session_id,
                name="Session",
                status=EventStatus.ERROR,
                message="Session not found",
                extra={"session_id": params.session_id},
            ))
            raise OpticsError(Code.E0702, message="Session not found")
        if params.mode in ("batch", "dry_run") and not session.test_cases:
            await event_manager.publish_event(
                _execution_event(params.session_id, EventStatus.ERROR, "Test cases are required")
            )
            raise OpticsError(Code.E0702, message="Test cases are required")

    async def _create_executor(
        self,
        params: ExecutionParams,
        session: Session,
        event_manager: EventManager,
    ) -> Executor:
        """Create the mode-specific executor; publish errors and raise on failure."""
        if params.mode == "batch":
            return BatchExecutor(test_case=session.test_cases, event_manager=event_manager)
        if params.mode == "dry_run":
            return DryRunExecutor(test_case=session.test_cases, event_manager=event_manager)
        if params.mode == "keyword":
            return await self._create_keyword_executor(params, event_manager)
        await event_manager.publish_event(
            _execution_event(params.session_id, EventStatus.ERROR, f"Unknown mode: {params.mode}")
        )
        raise OpticsError(Code.E0702, message=f"Unknown mode: {params.mode}")

    async def _create_keyword_executor(
        self, params: ExecutionParams, event_manager: EventManager
    ) -> Executor:
        """Create KeywordExecutor; publish errors and raise if keyword missing or creation fails."""
        if not params.keyword:
            await event_manager.publish_event(
                _execution_event(params.session_id, EventStatus.ERROR, "Keyword mode requires a keyword")
            )
            raise OpticsError(Code.E0403, message="Keyword mode requires a keyword")
        try:
            return KeywordExecutor(params.keyword, params.params, event_manager=event_manager)
        except Exception as e:
            await event_manager.publish_event(
                _execution_event(
                    params.session_id,
                    EventStatus.ERROR,
                    f"Failed to create keyword executor: {str(e)}",
                )
            )
            raise OpticsError(Code.E0401, message=f"Failed to create keyword executor: {str(e)}") from e

    async def _run_executor(
        self,
        executor: Executor,
        session: Session,
        runner: Runner,
        event_manager: EventManager,
        params: ExecutionParams,
    ) -> Any:
        """Run the executor and return result; publish error and raise on failure."""
        try:
            return await executor.execute(session, runner)
        except Exception as e:
            await event_manager.publish_event(
                _execution_event(params.session_id, EventStatus.ERROR, f"Execution failed: {str(e)}")
            )
            raise OpticsError(Code.E0701, message=f"Execution failed: {str(e)}") from e

    async def _drain_events_and_shutdown(self, event_manager: EventManager) -> None:
        """Wait for event queue to drain (with timeout), then shutdown."""
        internal_logger.debug("Event queue size before drain: %d", event_manager.event_queue.qsize())
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._event_drain_timeout_s
        while event_manager.event_queue.qsize() > 0:
            if loop.time() >= deadline:
                internal_logger.warning(
                    "Event drain timed out after %.2fs; proceeding with shutdown. "
                    "Remaining events: %d",
                    self._event_drain_timeout_s,
                    event_manager.event_queue.qsize(),
                )
                break
            internal_logger.debug("Waiting for %d events to process", event_manager.event_queue.qsize())
            await asyncio.sleep(0.1)
        event_manager.shutdown()

    async def execute(self, params: ExecutionParams) -> Any:
        event_manager = get_event_manager(params.session_id)
        session = self.session_manager.get_session(params.session_id)
        await self._validate_execution_params(session, params, event_manager)
        if session is None:
            raise RuntimeError("session must not be None after _validate_execution_params")

        event_manager.start()
        use_printer = params.use_printer
        internal_logger.debug(
            "Using printer: %s for runner: %s",
            "TreeResultPrinter" if use_printer else "NullResultPrinter",
            params.runner_type,
        )
        runner_type = "keyword_runner" if params.mode == "keyword" else params.runner_type

        with LoggerContext(params.session_id):
            runner = RunnerFactory.create_runner(
                session, runner_type, use_printer, event_manager=event_manager
            )
            if hasattr(runner, "result_printer") and runner.result_printer and use_printer:
                internal_logger.debug("Starting result printer live display")
                runner.result_printer.start_live()

            try:
                await event_manager.publish_event(
                    _execution_event(params.session_id, EventStatus.RUNNING, f"Starting {params.mode} execution")
                )
                executor = await self._create_executor(params, session, event_manager)
                return await self._run_executor(executor, session, runner, event_manager, params)
            except Exception as e:
                await event_manager.publish_event(
                    _execution_event(params.session_id, EventStatus.FAIL, f"Execution failed: {str(e)}")
                )
                internal_logger.error("Execution error in session %s: %s", params.session_id, e)
                raise OpticsError(Code.E0701, message=f"Execution failed: {str(e)}") from e
            finally:
                if hasattr(runner, "result_printer") and runner.result_printer:
                    internal_logger.debug("Stopping result printer live display")
                await self._drain_events_and_shutdown(event_manager)
