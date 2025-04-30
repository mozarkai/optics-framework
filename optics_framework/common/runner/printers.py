import abc
from typing import Dict, List
from pydantic import BaseModel, Field
from rich.live import Live
from rich.tree import Tree
from rich.text import Text
from rich.panel import Panel
from rich.progress import Progress, TaskID
from rich.console import Console, Group
import shutil


class TestCaseResult(BaseModel):
    """Result of a test case execution."""
    id: str
    name: str
    elapsed: str
    status: str
    modules: list = Field(default_factory=list)


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


class NullResultPrinter(IResultPrinter):
    """No-op result printer that implements IResultPrinter with empty methods."""

    def __init__(self):
        self._test_state: Dict[str, TestCaseResult] = {}

    @property
    def test_state(self) -> Dict[str, TestCaseResult]:
        # This getter returns the internal test state dictionary.
        # It’s implemented minimally to satisfy the interface.
        return self._test_state

    @test_state.setter
    def test_state(self, value: Dict[str, TestCaseResult]) -> None:
        # This setter assigns the provided value to the internal state.
        # It’s a no-op beyond storage, as this class doesn’t process or use the state.
        self._test_state = value

    def print_tree_log(self, test_case_result: TestCaseResult) -> None:
        # This method is intentionally empty because:
        #   - NullResultPrinter is a no-op implementation.
        #   - It’s meant to silently ignore printing requests, providing a safe default.
        #   - No logging or output is generated, as per the null object pattern.
        pass

    def start_live(self) -> None:
        # This method does nothing because:
        #   - It’s part of the no-op behavior.
        #   - No live updates or processes are started in this null implementation.
        #   - It exists to fulfill the interface contract without side effects.
        pass

    def stop_live(self) -> None:
        # This method is empty because:
        #   - There’s no live process to stop (start_live does nothing).
        #   - It ensures compatibility with IResultPrinter without altering state.
        #   - It’s a deliberate no-op for this null printer.
        pass

    def start_run(self, total_test_cases: int) -> None:
        # This method remains empty because:
        #   - No initialization or tracking of test runs is needed here.
        #   - It silently accepts the total_test_cases parameter without action.
        #   - The null pattern avoids any output or state change.
        pass


class TerminalWidthProvider:
    def get_terminal_width(self, default: int = 80) -> int:
        return shutil.get_terminal_size((default, 20)).columns


class TreeResultPrinter(IResultPrinter):
    STATUS_COLORS: Dict[str, str] = {
        "NOT RUN": "grey50",
        "RUNNING": "yellow",
        "PASS": "green",
        "FAIL": "red",
        "RETRYING": "orange",
        "SKIPPED": "cyan",
        "ERROR": "red",
        "PAUSED": "blue"
    }

    def __init__(self, terminal_width_provider: TerminalWidthProvider) -> None:
        self.terminal_width_provider = terminal_width_provider
        self._live: Live | None = None
        self._test_state: Dict[str, TestCaseResult] = {}
        self.progress = Progress()
        self.task_id: TaskID | None = None

    @property
    def test_state(self) -> Dict[str, TestCaseResult]:
        return self._test_state

    @test_state.setter
    def test_state(self, value: Dict[str, TestCaseResult]) -> None:
        self._test_state = value

    def start_run(self, total_test_cases: int) -> None:
        self.task_id = self.progress.add_task(
            "Running tests", total=max(1, total_test_cases))

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
        summary_text = f"Total Test Cases: {total} | Passed: {passed} | Failed: 0"
        summary_panel = Panel(
            summary_text, style="green" if failed == 0 else "red")
        return Group(self.progress, tree, summary_panel)

    def print_tree_log(self, test_case_result: TestCaseResult) -> None:
        self.test_state[test_case_result.name] = test_case_result
        if self._live:
            self._live.update(self._render_tree())

    def start_live(self) -> None:
        if not self._live:
            self._live = Live(self._render_tree(
            ), refresh_per_second=10, console=Console(force_terminal=True) )
            self._live.start()
            self._live.console.log("Testing started")

    def stop_live(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None
