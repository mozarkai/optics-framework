import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Dict, List
from pydantic import BaseModel, Field, ConfigDict
from optics_framework.common.session_manager import SessionManager, Session
from optics_framework.common.runner.test_runnner import TestRunner, TreeResultPrinter, TerminalWidthProvider, IResultPrinter, TestCaseResult
from optics_framework.common.runner.keyword_register import KeywordRegistry
from optics_framework.api import ActionKeyword, AppManagement, FlowControl, Verifier


class NullResultPrinter(IResultPrinter):
    """A no-op implementation of IResultPrinter for cases where no output is needed."""

    def __init__(self):
        self._test_state: Dict[str, TestCaseResult] = {}

    @property
    def test_state(self) -> Dict[str, TestCaseResult]:
        return self._test_state

    @test_state.setter
    def test_state(self, value: Dict[str, TestCaseResult]) -> None:
        self._test_state = value

    def print_tree_log(self, test_case_result: TestCaseResult) -> None:
        pass

    def start_live(self) -> None:
        pass

    def stop_live(self) -> None:
        pass

    def start_run(self, total_test_cases: int) -> None:
        pass


class TestCaseData(BaseModel):
    """Structure for test cases loaded from CSV."""
    test_cases: Dict[str, List[str]] = Field(
        default_factory=dict)  # {test_case: [test_steps]}


class ModuleData(BaseModel):
    """Structure for modules loaded from CSV."""
    modules: Dict[str, List[tuple[str, List[str]]]] = Field(
        default_factory=dict)  # {module_name: [(module_step, [params])]}


class ElementData(BaseModel):
    """Structure for elements loaded from CSV."""
    elements: Dict[str, str] = Field(
        default_factory=dict)  # {element_name: element_id}


class ExecutionParams(BaseModel):
    """Parameters for ExecutionEngine.execute."""
    model_config = ConfigDict(
        arbitrary_types_allowed=True)  # Allow asyncio.Queue

    session_id: str
    mode: str
    test_case: Optional[str] = None
    keyword: Optional[str] = None
    params: List[str] = Field(default_factory=list)
    event_queue: Optional[asyncio.Queue] = None
    test_cases: TestCaseData = Field(default_factory=TestCaseData)
    modules: ModuleData = Field(default_factory=ModuleData)
    elements: ElementData = Field(default_factory=ElementData)


class Executor(ABC):
    """Abstract base class for execution strategies."""
    @abstractmethod
    async def execute(self, session: Session, runner: TestRunner, event_queue: Optional[asyncio.Queue]) -> None:
        pass


class BatchExecutor(Executor):
    """Executes a batch test case from preloaded CSV data."""

    def __init__(self, test_case: Optional[str] = None):
        self.test_case = test_case

    async def execute(self, session: Session, runner: TestRunner, event_queue: Optional[asyncio.Queue]) -> None:
        if not runner.test_cases:
            status = "ERROR"
            message = "No test cases loaded"
            if event_queue:
                await event_queue.put({"execution_id": session.session_id, "status": status, "message": message})
            raise ValueError(message)

        if self.test_case:
            if self.test_case not in runner.test_cases:
                status = "ERROR"
                message = f"Test case {self.test_case} not found in loaded data"
                if event_queue:
                    await event_queue.put({"execution_id": session.session_id, "status": status, "message": message})
                raise ValueError(message)
            result = runner.execute_test_case(self.test_case)
            status = "PASS" if result.status == "PASS" else "FAIL"
            message = f"Test case {self.test_case} completed with status {status}"
        else:
            runner.run_all()
            status = "PASS" if all(
                tc.status == "PASS" for tc in runner.result_printer.test_state.values()) else "FAIL"
            message = "All test cases completed"

        if event_queue:
            await event_queue.put({"execution_id": session.session_id, "status": status, "message": message})


class DryRunExecutor(Executor):
    """Performs a dry run of a test case from preloaded CSV data."""

    def __init__(self, test_case: Optional[str] = None):
        self.test_case = test_case

    async def execute(self, session: Session, runner: TestRunner, event_queue: Optional[asyncio.Queue]) -> None:
        if not runner.test_cases:
            status = "ERROR"
            message = "No test cases loaded"
            if event_queue:
                await event_queue.put({"execution_id": session.session_id, "status": status, "message": message})
            raise ValueError(message)

        if self.test_case:
            if self.test_case not in runner.test_cases:
                status = "ERROR"
                message = f"Test case {self.test_case} not found in loaded data"
                if event_queue:
                    await event_queue.put({"execution_id": session.session_id, "status": status, "message": message})
                raise ValueError(message)
            result = runner.dry_run_test_case(self.test_case)
            status = "PASS" if result.status == "PASS" else "FAIL"
            message = f"Dry run for test case {self.test_case} completed with status {status}"
        else:
            runner.dry_run_all()
            status = "PASS" if all(
                tc.status == "PASS" for tc in runner.result_printer.test_state.values()) else "FAIL"
            message = "All test cases dry run completed"

        if event_queue:
            await event_queue.put({"execution_id": session.session_id, "status": status, "message": message})


class KeywordExecutor(Executor):
    """Executes a single keyword."""

    def __init__(self, keyword: str, params: List[str]):
        self.keyword = keyword
        self.params = params

    async def execute(self, session: Session, runner: TestRunner, event_queue: Optional[asyncio.Queue]) -> None:
        method = runner.keyword_map.get("_".join(self.keyword.split()).lower())
        if method:
            method(*self.params)
            if event_queue:
                await event_queue.put({
                    "execution_id": session.session_id,
                    "status": "PASS",
                    "keyword": self.keyword,
                    "message": "Keyword executed successfully"
                })
        else:
            if event_queue:
                await event_queue.put({
                    "execution_id": session.session_id,
                    "status": "FAIL",
                    "keyword": self.keyword,
                    "message": "Keyword not found"
                })
            raise ValueError(f"Keyword {self.keyword} not found")


class RunnerFactory:
    """Sets up a TestRunner with keywords and preloaded data."""
    @staticmethod
    def create_runner(
        session: Session,
        use_printer: bool,
        test_cases: Dict[str, List[str]],
        modules: Dict[str, List[tuple[str, List[str]]]],
        elements: Dict[str, str]
    ) -> TestRunner:
        result_printer = TreeResultPrinter(
            TerminalWidthProvider()) if use_printer else NullResultPrinter()
        runner = TestRunner(
            test_cases=test_cases,
            modules=modules,
            elements=elements,
            keyword_map={},  # Initialize with empty dict
            result_printer=result_printer
        )
        registry = KeywordRegistry()
        flow_control = FlowControl(runner)
        action_keyword = session.optics.build(ActionKeyword)
        app_management = session.optics.build(AppManagement)
        verifier = session.optics.build(Verifier)
        registry.register(action_keyword)
        registry.register(app_management)
        registry.register(flow_control)
        registry.register(verifier)
        runner.keyword_map = registry.keyword_map  # No type hint needed now
        return runner


class ExecutionEngine:
    """Orchestrates execution using session management and executor strategies."""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    async def execute(
        self,
        session_id: str,
        mode: str,
        test_case: Optional[str] = None,
        keyword: Optional[str] = None,
        params: Optional[List[str]] = None,
        event_queue: Optional[asyncio.Queue] = None,
        test_cases: Optional[Dict[str, List[str]]] = None,
        modules: Optional[Dict[str, List[tuple[str, List[str]]]]] = None,
        elements: Optional[Dict[str, str]] = None
    ) -> None:
        if params is None:
            params = []
        params_model = ExecutionParams(
            session_id=session_id,
            mode=mode,
            test_case=test_case,
            keyword=keyword,
            params=params,
            event_queue=event_queue,
            test_cases=TestCaseData(test_cases=test_cases or {}),
            modules=ModuleData(modules=modules or {}),
            elements=ElementData(elements=elements or {})
        )

        session = self.session_manager.get_session(params_model.session_id)
        if not session:
            if params_model.event_queue:
                await params_model.event_queue.put({"status": "ERROR", "message": "Session not found"})
            raise ValueError("Session not found")

        runner = RunnerFactory.create_runner(
            session,
            use_printer=params_model.event_queue is None,
            # Pylint suppression for false positives on Pydantic field access
            test_cases=params_model.test_cases.test_cases,  # pylint: disable=no-member
            modules=params_model.modules.modules,  # pylint: disable=no-member
            elements=params_model.elements.elements  # pylint: disable=no-member
        )
        if runner.result_printer:
            runner.result_printer.start_live()

        if params_model.mode == "batch":
            executor = BatchExecutor(params_model.test_case)
        elif params_model.mode == "dry_run":
            executor = DryRunExecutor(params_model.test_case)
        elif params_model.mode == "keyword":
            if not params_model.keyword:
                if params_model.event_queue:
                    await params_model.event_queue.put({"status": "ERROR", "message": "Keyword mode requires a keyword"})
                raise ValueError("Keyword mode requires a keyword")
            executor = KeywordExecutor(
                params_model.keyword, params_model.params)
        else:
            if params_model.event_queue:
                await params_model.event_queue.put({"status": "ERROR", "message": f"Unknown mode: {params_model.mode}"})
            raise ValueError(f"Unknown mode: {params_model.mode}")

        try:
            if params_model.event_queue:
                await params_model.event_queue.put({
                    "execution_id": params_model.session_id,
                    "status": "RUNNING",
                    "message": f"Starting {params_model.mode} execution"
                })
            await executor.execute(session, runner, params_model.event_queue)
        except Exception as e:
            if params_model.event_queue:
                await params_model.event_queue.put({"status": "FAIL", "message": f"Execution failed: {str(e)}"})
            raise
        finally:
            if runner.result_printer:
                runner.result_printer.stop_live()
