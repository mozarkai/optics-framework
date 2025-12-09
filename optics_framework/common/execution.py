from uuid import uuid4
import asyncio
import json
import inspect
from abc import ABC, abstractmethod
from typing import Optional, List, Any, get_origin, get_args, Union, Callable
from pydantic import BaseModel, Field, ConfigDict
from optics_framework.common.session_manager import SessionManager, Session
from optics_framework.common.runner.keyword_register import KeywordRegistry
from optics_framework.common.runner.printers import TreeResultPrinter, TerminalWidthProvider, NullResultPrinter
from optics_framework.common.runner.test_runnner import TestRunner, PytestRunner, Runner, KeywordRunner
from optics_framework.common.logging_config import LoggerContext, internal_logger
from optics_framework.common.models import TestCaseNode
from optics_framework.common.error import OpticsError, Code
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


def _is_list_type(param_type: Any) -> bool:
    """
    Check if a parameter type annotation indicates it's a list type.

    Args:
        param_type: The type annotation from inspect.Parameter

    Returns:
        bool: True if the type is a list type (List[str], Optional[List[str]], etc.)
    """
    if param_type is None or param_type == inspect.Parameter.empty:
        return False

    # Handle Optional[List[str]] -> Union[List[str], None]
    origin = get_origin(param_type)
    if origin is Union:
        args = get_args(param_type)
        # Check if any of the union types is a list
        for arg in args:
            if get_origin(arg) is list or (hasattr(arg, '__origin__') and arg.__origin__ is list):
                return True

    # Handle List[str] directly
    if origin is list:
        return True

    # Handle typing.List[str] (Python < 3.9 compatibility)
    if hasattr(param_type, '__origin__') and param_type.__origin__ is list:
        return True

    return False


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

    deserialized = []
    for i, param_value in enumerate(params):
        if i < len(param_types):
            param_type = param_types[i]
            if _is_list_type(param_type):
                # Try to deserialize JSON string back to list
                try:
                    deserialized_value = json.loads(param_value)
                    if isinstance(deserialized_value, list):
                        deserialized.append(deserialized_value)
                    else:
                        deserialized.append(param_value)
                except (json.JSONDecodeError, TypeError):
                    # If it's not valid JSON, pass through as-is
                    deserialized.append(param_value)
            else:
                deserialized.append(param_value)
        else:
            deserialized.append(param_value)

    return deserialized


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
            await event_manager.publish_event(Event(
                entity_type="keyword",
                entity_id=session.session_id,
                name=self.keyword,
                status=EventStatus.PASS,
                message="Keyword executed successfully",
                extra={"session_id": session.session_id}
            ))
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


class ExecutionEngine:
    """Orchestrates execution."""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    async def execute(self, params: ExecutionParams) -> Any:
        # Get session-specific event manager
        event_manager = get_event_manager(params.session_id)

        session = self.session_manager.get_session(params.session_id)
        if not session:
            await event_manager.publish_event(Event(
                entity_type="session",
                entity_id=params.session_id,
                name="Session",
                status=EventStatus.ERROR,
                message="Session not found",
                extra={"session_id": params.session_id}
            ))
            raise OpticsError(Code.E0702, message="Session not found")

        if params.mode in ("batch", "dry_run") and not session.test_cases:
            await event_manager.publish_event(Event(
                entity_type="execution",
                entity_id=params.session_id,
                name="Execution",
                status=EventStatus.ERROR,
                message="Test cases are required",
                extra={"session_id": params.session_id}
            ))
            raise OpticsError(Code.E0702, message="Test cases are required")

        event_manager.start()

        use_printer = params.use_printer
        internal_logger.debug(
            "Using printer: %s for runner: %s", 'TreeResultPrinter' if use_printer else 'NullResultPrinter', params.runner_type)

        # Use minimal runner for keyword mode
        if params.mode == "keyword":
            runner_type = "keyword_runner"
        else:
            runner_type = params.runner_type

        with LoggerContext(params.session_id):
            runner = RunnerFactory.create_runner(
                session,
                runner_type,
                use_printer,
                event_manager=event_manager
            )
            if hasattr(runner, 'result_printer') and runner.result_printer and use_printer:
                internal_logger.debug("Starting result printer live display")
                runner.result_printer.start_live()

            try:
                await event_manager.publish_event(Event(
                    entity_type="execution",
                    entity_id=params.session_id,
                    name="Execution",
                    status=EventStatus.RUNNING,
                    message=f"Starting {params.mode} execution",
                    extra={"session_id": params.session_id}
                ))
                if params.mode == "batch":
                    executor = BatchExecutor(test_case=session.test_cases, event_manager=event_manager)
                elif params.mode == "dry_run":
                    executor = DryRunExecutor(test_case=session.test_cases, event_manager=event_manager)
                elif params.mode == "keyword":
                    if not params.keyword:
                        await event_manager.publish_event(Event(
                            entity_type="execution",
                            entity_id=params.session_id,
                            name="Execution",
                            status=EventStatus.ERROR,
                            message="Keyword mode requires a keyword",
                            extra={"session_id": params.session_id}
                        ))
                        raise OpticsError(Code.E0403, message="Keyword mode requires a keyword")
                    try:
                        executor = KeywordExecutor(params.keyword, params.params, event_manager=event_manager)
                    except Exception as e:
                        await event_manager.publish_event(Event(
                            entity_type="execution",
                            entity_id=params.session_id,
                            name="Execution",
                            status=EventStatus.ERROR,
                            message=f"Failed to create keyword executor: {str(e)}",
                            extra={"session_id": params.session_id}
                        ))
                        raise OpticsError(Code.E0401, message=f"Failed to create keyword executor: {str(e)}") from e
                else:
                    await event_manager.publish_event(Event(
                        entity_type="execution",
                        entity_id=params.session_id,
                        name="Execution",
                        status=EventStatus.ERROR,
                        message=f"Unknown mode: {params.mode}",
                        extra={"session_id": params.session_id}
                    ))
                    raise OpticsError(Code.E0702, message=f"Unknown mode: {params.mode}")
                try:
                    result =  await executor.execute(session, runner)
                    return result
                except Exception as e:
                    await event_manager.publish_event(Event(
                        entity_type="execution",
                        entity_id=params.session_id,
                        name="Execution",
                        status=EventStatus.ERROR,
                        message=f"Execution failed: {str(e)}",
                        extra={"session_id": params.session_id}
                    ))
                    raise OpticsError(Code.E0701, message=f"Execution failed: {str(e)}") from e
            except Exception as e:
                await event_manager.publish_event(Event(
                    entity_type="execution",
                    entity_id=params.session_id,
                    name="Execution",
                    status=EventStatus.FAIL,
                    message=f"Execution failed: {str(e)}",
                    extra={"session_id": params.session_id}
                ))
                internal_logger.error(f"Execution error in session {params.session_id}: {e}")
                raise OpticsError(Code.E0701, message=f"Execution failed: {str(e)}") from e
            finally:
                if hasattr(runner, 'result_printer') and runner.result_printer:
                    internal_logger.debug(
                        "Stopping result printer live display")
                # Wait for event queue to drain
                internal_logger.debug(
                    "Event queue size before drain: %d", event_manager.event_queue.qsize())
                while event_manager.event_queue.qsize() > 0:
                    internal_logger.debug(
                        "Waiting for %d events to process", event_manager.event_queue.qsize())
                    await asyncio.sleep(0.1)
                event_manager.shutdown()
