import time
import uuid
import asyncio
import tempfile
import shutil
import sys
import logging
from itertools import product
from typing import Callable, Dict, List, Optional, Union, Any
import pytest
from optics_framework.common.session_manager import Session
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.config_handler import Config, ConfigHandler
from optics_framework.common.logging_config import (
    internal_logger,
    execution_logger,
    LogCaptureBuffer,
)
from optics_framework.common import test_context
from optics_framework.common.runner.printers import (
    IResultPrinter,
    TestCaseResult,
    KeywordResult,
    ModuleResult,
    NullResultPrinter,
)
from optics_framework.common.models import (
    TestCaseNode,
    ModuleNode,
    ModuleData,
    KeywordNode,
    ElementData,
    State,
    Node,
    ApiData,
)
from optics_framework.common.events import (
    EventStatus,
    CommandType,
    Event,
)
from optics_framework.common.runner.data_reader import DataReader


class Runner:
    test_case: TestCaseNode
    result_printer: IResultPrinter
    keyword_map: Dict[str, Callable[..., Any]]
    apis: ApiData
    elements: ElementData

    def execute_test_case(self, test_case: str) -> Optional[TestCaseResult]:
        """Empty implementation to satisfy the interface contract.
        Subclasses must provide concrete logic for executing test cases."""
        pass

    async def run_all(self) -> None:
        """Empty implementation to satisfy the interface contract.
        Subclasses must implement logic to run all test cases."""
        pass

    async def dry_run_test_case(self, test_case: str) -> Optional[TestCaseResult]:
        """Empty implementation to satisfy the interface contract.
        Subclasses must provide logic for dry-running test cases."""
        pass

    async def dry_run_all(self) -> None:
        """Empty implementation to satisfy the interface contract.
        Subclasses must implement logic to dry-run all test cases."""
        pass


async def queue_event(event: Event, event_manager) -> None:
    """Queue an event for async processing."""
    internal_logger.debug(f"Queueing event: {event.model_dump()}")
    await event_manager.publish_event(event)


def queue_event_sync(event: Event, event_manager) -> None:
    """Queue an event synchronously for pytest."""
    internal_logger.debug(f"Queueing event (sync): {event.model_dump()}")
    for _, subscriber in event_manager.subscribers.items():
        try:
            asyncio.run(subscriber.on_event(event))
        except RuntimeError as e:
            internal_logger.warning(f"Failed to process event synchronously: {e}")


class TestRunner(Runner):
    def __init__(
        self,
        session: Session,
        keyword_map: Dict[str, Callable[..., Any]],
        result_printer: IResultPrinter,
        event_manager,
    ) -> None:
        self.session = session
        self.session_id = session.session_id
        self.test_cases = self.session.test_cases
        self.modules = self.session.modules
        self.elements = self.session.elements if self.session.elements is not None else ElementData()
        self.apis = self.session.apis if self.session.apis is not None else ApiData()
        self.keyword_map = keyword_map
        self.result_printer = result_printer
        self.config = session.config
        self.event_manager = event_manager
        if hasattr(self.modules, "modules"):
            execution_logger.debug(
                "Initialized test_state: %s with %d modules",
                list(self.modules.modules.keys()),
                len(self.modules.modules)
            )
        self._initialize_test_state()

    def _initialize_test_state(self) -> None:
        def _init_keywords(module_node):
            keywords = []
            current_keyword = module_node.keywords_head
            while current_keyword:
                resolved_params = [
                    self.resolve_param(param) for param in current_keyword.params
                ]
                resolved_name = (
                    f"{current_keyword.name} ({', '.join(str(p) for p in resolved_params)})"
                    if resolved_params
                    else current_keyword.name
                )
                keyword_result = KeywordResult(
                    id=current_keyword.id,
                    name=current_keyword.name,
                    resolved_name=resolved_name,
                    elapsed="0.00s",
                    status="NOT_RUN",
                    reason="",
                )
                keywords.append(keyword_result)
                current_keyword = current_keyword.next
            return keywords

        def _init_modules(test_case_node):
            modules = []
            current_module = test_case_node.modules_head
            while current_module:
                module_result = ModuleResult(
                    name=current_module.name,
                    elapsed="0.00s",
                    status="NOT_RUN",
                    keywords=_init_keywords(current_module),
                )
                if not isinstance(module_result.keywords, list):
                    module_result.keywords = []
                modules.append(module_result)
                current_module = current_module.next
            return modules

        test_state = {}
        current_test = self.test_cases
        while current_test:
            test_result = TestCaseResult(
                id=str(uuid.uuid4()),
                name=current_test.name,
                elapsed="0.00s",
                status="NOT_RUN",
                modules=_init_modules(current_test),
            )
            if not isinstance(test_result.modules, list):
                test_result.modules = []
            test_state[current_test.name] = test_result
            current_test = current_test.next
        self.result_printer.test_state = test_state
        execution_logger.debug(
            "Initialized test_state: %s with %d modules",
            list(test_state.keys()),
            sum(len(m.modules) for m in test_state.values())
        )

    def _extra(
        self, test_case: str, module: str = "N/A", keyword: str = "N/A"
    ) -> Dict[str, str]:
        return {
            "test_case": test_case,
            "test_module": module,
            "keyword": keyword,
            "session_id": self.session_id,
        }

    def resolve_param(self, param: str) -> str:
        if not param.startswith("${") or not param.endswith("}"):
            return param  # If param is not a variable reference, return it as is.
        var_name = param[2:-1].strip()
        resolved_value = self.elements.get_first(var_name)
        if resolved_value is None:
            raise OpticsError(Code.E0201, f"Element not found: {var_name}")
        internal_logger.debug(f"Resolved '{param}' to '{resolved_value}'")
        return resolved_value

    def _init_test_case(self, test_case: str) -> TestCaseResult:
        test_context.current_test_case.set(test_case)
        return self.result_printer.test_state.get(
            test_case,
            TestCaseResult(
                id=str(uuid.uuid4()), name=test_case, elapsed="0.00s", status="NOT_RUN"
            ),
        )

    def _find_result(
        self,
        test_case_name: str,
        module_name: Optional[str] = None,
        keyword_id: Optional[str] = None,
    ) -> Union[TestCaseResult, ModuleResult, KeywordResult]:
        test_result = self.result_printer.test_state.get(test_case_name)
        if not test_result:
            raise ValueError(f"Test case {test_case_name} not found in test_state")
        if module_name is None:
            return test_result
        for module_result in test_result.modules:
            if module_result.name == module_name:
                if keyword_id is None:
                    return module_result
                for keyword_result in module_result.keywords:
                    if keyword_result.id == keyword_id:
                        internal_logger.debug(
                            f"Found keyword: {keyword_result.name} (id: {keyword_id})"
                        )
                        return keyword_result
                raise ValueError(
                    f"Keyword id {keyword_id} not found in module {module_name}"
                )
        raise ValueError(f"Module {module_name} not found in test_state")

    def _update_status(
        self,
        result: Union[TestCaseResult, ModuleResult, KeywordResult],
        status: str,
        elapsed: Optional[float] = None,
        test_case_name: str = "",
    ) -> None:
        result.status = status
        if elapsed is not None:
            result.elapsed = f"{elapsed:.2f}s"
        if test_case_name:
            internal_logger.debug(
                f"Updating status for {result.__class__.__name__}: {result.name} -> {status}"
            )
            test_case_result = self.result_printer.test_state.get(test_case_name)
            if test_case_result:
                self.result_printer.print_tree_log(test_case_result)

    async def _send_event(
        self,
        entity_type: str,
        node: Node,
        status: EventStatus,
        reason: Optional[str] = None,
        parent_id: Optional[str] = None,
        args: Optional[List[Any]] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        elapsed: Optional[float] = None,
        logs: Optional[List[logging.LogRecord]] = None,
    ) -> None:
        log_messages = None
        if logs is not None:
            log_messages = [
                record.getMessage()
                if isinstance(record, logging.LogRecord)
                else str(record)
                for record in logs
            ]
        event = Event(
            entity_type=entity_type,
            entity_id=node.id,
            name=node.name,
            status=status,
            message=reason or "",
            parent_id=parent_id,
            extra={"session_id": self.session_id},
            args=args,
            start_time=start_time,
            end_time=end_time,
            elapsed=elapsed,
            logs=log_messages,
        )
        await queue_event(event, self.event_manager)

    async def _process_commands(
        self, node: KeywordNode, parent: Optional[ModuleNode]
    ) -> bool:
        retry = False
        command = await self.event_manager.get_command()
        if command:
            if command.command == CommandType.RETRY and command.entity_id == node.id:
                node.state = State.RETRYING
                node.attempt_count += 1
                retry = True
            elif (
                command.command == CommandType.ADD
                and parent
                and command.parent_id == parent.id
            ):
                new_node = KeywordNode(
                    name=command.params[0] if command.params else "NewKeyword",
                    params=command.params[1:] if command.params else [],
                )
                new_node.next = node.next
                node.next = new_node
        return retry

    async def _execute_keyword(
        self,
        keyword_node: KeywordNode,
        module_node: ModuleNode,
        test_case_result: TestCaseResult,
        extra: Dict[str, str],
    ) -> bool:
        capture_handler = LogCaptureBuffer()
        execution_logger.addHandler(capture_handler)

        keyword_result = self._find_result(
            test_case_result.name, module_node.name, keyword_node.id
        )
        execution_logger.debug(
            "Executing keyword: %s (id: %s)", keyword_node.name, keyword_node.id
        )
        start_time = time.time()

        keyword_node.state = State.RUNNING
        await self._send_event(
            "keyword",
            keyword_node,
            EventStatus.RUNNING,
            parent_id=module_node.id,
            start_time=start_time,
        )
        self._update_status(
            keyword_result, "RUNNING", time.time() - start_time, test_case_result.name
        )

        func_name = "_".join(keyword_node.name.split()).lower()
        method = self.keyword_map.get(func_name)
        if not method:
            await self._handle_keyword_not_found(keyword_node, module_node, keyword_result, start_time, test_case_result)
            return False

        param_candidates = await self._build_param_candidates(keyword_node, keyword_node.params, module_node, keyword_result, start_time, test_case_result, capture_handler)
        if param_candidates is None:
            return False

        result = await self._try_execute_with_fallback(
            method, param_candidates, keyword_node, module_node, keyword_result, start_time, test_case_result, capture_handler
        )
        if result is not None:
            return result

        await self._handle_fallback_exhausted(keyword_node, module_node, keyword_result, start_time, test_case_result, capture_handler)
        await asyncio.sleep(self.config.halt_duration)
        if await self._process_commands(keyword_node, module_node):
            return await self._execute_keyword(
                keyword_node, module_node, test_case_result, extra
            )
        return False

    async def _handle_keyword_not_found(self, keyword_node, module_node, keyword_result, start_time, test_case_result):
        keyword_node.state = State.COMPLETED_FAILED
        await self._send_event(
            "keyword",
            keyword_node,
            EventStatus.FAIL,
            reason=f"Keyword not found: {keyword_node.name}",
            parent_id=module_node.id,
            start_time=start_time,
            end_time=time.time(),
            elapsed=time.time() - start_time,
        )
        self._update_status(keyword_result, "FAIL", time.time() - start_time, test_case_result.name)

    async def _build_param_candidates(self, keyword_node, params, module_node, keyword_result, start_time, test_case_result, capture_handler):
        param_candidates = []
        for param in params:
            if isinstance(param, str) and param.startswith("${") and param.endswith("}"):
                var_name = param[2:-1].strip()
                values = self.elements.get_element(var_name)
                if not values:
                    # Await error handler to ensure error is processed before returning
                    await self._handle_element_not_found(
                        keyword_node, module_node, keyword_result, start_time, test_case_result, var_name, capture_handler
                    )
                    return None
                param_candidates.append(values)
            else:
                param_candidates.append([param])
        return param_candidates

    async def _handle_element_not_found(self, keyword_node, module_node, keyword_result, start_time, test_case_result, var_name, capture_handler):
        keyword_node.state = State.COMPLETED_FAILED
        await self._send_event(
            "keyword",
            keyword_node,
            EventStatus.FAIL,
            f"Element not found: {var_name}",
            parent_id=module_node.id,
            start_time=start_time,
            end_time=time.time(),
            elapsed=time.time() - start_time,
            logs=capture_handler.records,
        )
        self._update_status(keyword_result, "FAIL", time.time() - start_time, test_case_result.name)

    async def _try_execute_with_fallback(
        self, method, param_candidates, keyword_node, module_node, keyword_result, start_time, test_case_result, capture_handler
    ):
        # Try all combinations of param candidates (for fallback)
        MAX_ATTEMPTS = 20
        attempts = 0
        for candidate_args in product(*param_candidates):
            attempts += 1
            if attempts > MAX_ATTEMPTS:
                break
            try:
                resolved_positional_params, resolved_kw_params = self._resolve_candidate_params(candidate_args)
                method(*resolved_positional_params, **resolved_kw_params)
                keyword_node.state = State.COMPLETED_PASSED
                await self._send_event(
                    "keyword",
                    keyword_node,
                    EventStatus.PASS,
                    parent_id=module_node.id,
                    start_time=start_time,
                    end_time=time.time(),
                    elapsed=time.time() - start_time,
                    logs=capture_handler.records,
                )
                self._update_status(keyword_result, "PASS", time.time() - start_time, test_case_result.name)
                return True
            except OpticsError as oe:
                if str(oe.code).startswith("E02") or oe.code == Code.X0201:
                    internal_logger.debug(f"Keyword fallback: tried {candidate_args}, error: {oe}")
                    continue
                else:
                    await self._handle_keyword_exception(keyword_node, module_node, keyword_result, start_time, test_case_result, oe, capture_handler)
                    return False
            except Exception as e:
                await self._handle_keyword_exception(keyword_node, module_node, keyword_result, start_time, test_case_result, e, capture_handler)
                return False
        return None

    async def _handle_keyword_exception(self, keyword_node, module_node, keyword_result, start_time, test_case_result, exc, capture_handler):
        keyword_node.state = State.COMPLETED_FAILED
        await self._send_event(
            "keyword",
            keyword_node,
            EventStatus.FAIL,
            f"Keyword '{keyword_node.name}' failed: {exc}",
            parent_id=module_node.id,
            start_time=start_time,
            end_time=time.time(),
            elapsed=time.time() - start_time,
            logs=capture_handler.records,
        )
        self._update_status(keyword_result, "FAIL", time.time() - start_time, test_case_result.name)

    async def _handle_fallback_exhausted(self, keyword_node, module_node, keyword_result, start_time, test_case_result, capture_handler):
        keyword_node.state = State.COMPLETED_FAILED
        await self._send_event(
            "keyword",
            keyword_node,
            EventStatus.FAIL,
            f"Keyword '{keyword_node.name}' failed after fallback attempts",
            parent_id=module_node.id,
            start_time=start_time,
            end_time=time.time(),
            elapsed=time.time() - start_time,
            logs=capture_handler.records,
        )
        self._update_status(keyword_result, "FAIL", time.time() - start_time, test_case_result.name)

    async def _process_module(
        self,
        module_node: ModuleNode,
        test_case_result: TestCaseResult,
        extra: Dict[str, str],
    ) -> bool:
        module_result = self._find_result(test_case_result.name, module_node.name)
        start_time = time.time()
        module_node.state = State.RUNNING
        await self._send_event(
            "module",
            module_node,
            EventStatus.RUNNING,
            parent_id=test_case_result.id,
            start_time=start_time,
        )
        self._update_status(
            module_result, "RUNNING", time.time() - start_time, test_case_result.name
        )

        current = module_node.keywords_head
        while current:
            extra["keyword"] = current.name
            if not await self._execute_keyword(
                current, module_node, test_case_result, extra
            ):
                module_node.state = State.COMPLETED_FAILED
                await self._send_event(
                    "module",
                    module_node,
                    EventStatus.FAIL,
                    parent_id=test_case_result.id,
                    start_time=start_time,
                    end_time=time.time(),
                    elapsed=time.time() - start_time,
                )
                self._update_status(
                    module_result,
                    "FAIL",
                    time.time() - start_time,
                    test_case_result.name,
                )
                return False
            current = current.next

        module_node.state = State.COMPLETED_PASSED
        await self._send_event(
            "module",
            module_node,
            EventStatus.PASS,
            parent_id=test_case_result.id,
            start_time=start_time,
            end_time=time.time(),
            elapsed=time.time() - start_time,
        )
        self._update_status(
            module_result, "PASS", time.time() - start_time, test_case_result.name
        )
        return True

    async def _process_test_case(
        self, test_case: str, dry_run: bool = False
    ) -> TestCaseResult:
        start_time = time.time()
        testcase_id = str(uuid.uuid4())
        extra = self._extra(test_case)
        test_case_result = self._init_test_case(test_case)
        current = self.test_cases
        while current and current.name != test_case:
            current = current.next
        if not current:
            self._update_status(
                test_case_result,
                "FAIL",
                time.time() - start_time,
                test_case_result.name,
            )
            await self._send_event(
                "test_case",
                TestCaseNode(name=test_case, id=testcase_id),
                EventStatus.FAIL,
                start_time=start_time,
                end_time=time.time(),
                elapsed=time.time() - start_time,
            )
            return test_case_result

        current.id = testcase_id
        test_case_result = TestCaseResult(
            id=testcase_id,
            name=test_case_result.name,
            elapsed=test_case_result.elapsed,
            status=test_case_result.status,
            modules=test_case_result.modules,
        )
        self.result_printer.test_state[test_case] = test_case_result
        current.state = State.RUNNING
        await self._send_event(
            "test_case", current, EventStatus.RUNNING, start_time=start_time
        )
        self._update_status(
            test_case_result, "RUNNING", time.time() - start_time, test_case_result.name
        )

        module_current = current.modules_head
        while module_current:
            if dry_run:
                if not await self._dry_run_module(
                    module_current, test_case_result, testcase_id
                ):
                    current.state = State.COMPLETED_FAILED
                    await self._send_event(
                        "test_case",
                        current,
                        EventStatus.FAIL,
                        start_time=start_time,
                        end_time=time.time(),
                        elapsed=time.time() - start_time,
                    )
                    self._update_status(
                        test_case_result,
                        "FAIL",
                        time.time() - start_time,
                        test_case_result.name,
                    )
                    return test_case_result
            else:
                if not await self._process_module(
                    module_current, test_case_result, extra
                ):
                    current.state = State.COMPLETED_FAILED
                    await self._send_event(
                        "test_case",
                        current,
                        EventStatus.FAIL,
                        start_time=start_time,
                        end_time=time.time(),
                        elapsed=time.time() - start_time,
                    )
                    self._update_status(
                        test_case_result,
                        "FAIL",
                        time.time() - start_time,
                        test_case_result.name,
                    )
                    return test_case_result
            module_current = module_current.next

        current.state = State.COMPLETED_PASSED
        await self._send_event(
            "test_case",
            current,
            EventStatus.FAIL,
            start_time=start_time,
            end_time=time.time(),
            elapsed=time.time() - start_time,
        )
        self._update_status(
            test_case_result, "PASS", time.time() - start_time, test_case_result.name
        )
        return test_case_result

    async def _dry_run_module(
        self,
        module_node: ModuleNode,
        test_case_result: TestCaseResult,
        testcase_id: str,
    ) -> bool:
        module_result = self._find_result(test_case_result.name, module_node.name)
        start_time = time.time()
        module_node.state = State.RUNNING
        await self._send_event(
            "module",
            module_node,
            EventStatus.RUNNING,
            parent_id=testcase_id,
            start_time=start_time,
        )
        self._update_status(module_result, "RUNNING", 0.0, test_case_result.name)

        keyword_current = module_node.keywords_head
        while keyword_current:
            keyword_result = self._find_result(
                test_case_result.name, module_node.name, keyword_current.id
            )
            keyword_current.state = State.RUNNING
            await self._send_event(
                "keyword",
                keyword_current,
                EventStatus.RUNNING,
                parent_id=module_node.id,
                start_time=start_time,
            )
            self._update_status(keyword_result, "RUNNING", 0.0, test_case_result.name)

            try:
                resolved_params = [
                    self.resolve_param(param) for param in keyword_current.params
                ]
                if isinstance(keyword_result, KeywordResult):
                    keyword_result.resolved_name = (
                        f"{keyword_current.name} ({', '.join(resolved_params)})"
                        if resolved_params
                        else keyword_current.name
                    )
                func_name = "_".join(keyword_current.name.split()).lower()
                if func_name not in self.keyword_map:
                    raise ValueError("Keyword not found")
            except ValueError as e:
                keyword_current.state = State.COMPLETED_FAILED
                await self._send_event(
                    "keyword",
                    keyword_current,
                    EventStatus.FAIL,
                    str(e),
                    parent_id=module_node.id,
                    start_time=start_time,
                    end_time=time.time(),
                    elapsed=time.time() - start_time,
                )
                self._update_status(keyword_result, "FAIL", 0.0, test_case_result.name)
                self._update_status(module_result, "FAIL", 0.0, test_case_result.name)
                return False

            keyword_current.state = State.COMPLETED_PASSED
            await self._send_event(
                "module",
                module_node,
                EventStatus.PASS,
                parent_id=testcase_id,
                start_time=start_time,
                end_time=time.time(),
                elapsed=time.time() - start_time,
            )
            self._update_status(keyword_result, "PASS", 0.0, test_case_result.name)
            keyword_current = keyword_current.next

        module_node.state = State.COMPLETED_PASSED
        await self._send_event(
            "module", module_node, EventStatus.PASS, parent_id=testcase_id
        )
        self._update_status(module_result, "PASS", 0.0, test_case_result.name)
        return True

    async def execute_test_case(self, test_case: str) -> TestCaseResult:
        return await self._process_test_case(test_case, dry_run=False)

    async def dry_run_test_case(self, test_case: str) -> TestCaseResult:
        return await self._process_test_case(test_case, dry_run=True)

    async def run_all(self) -> None:
        current = self.test_cases
        self.result_printer.start_run(len(self.result_printer.test_state))
        self.result_printer.start_live()
        while current:
            await self.execute_test_case(current.name)
            current = current.next
        self.result_printer.stop_live()

    async def dry_run_all(self) -> None:
        current = self.test_cases
        self.result_printer.start_run(len(self.result_printer.test_state))
        self.result_printer.start_live()
        while current:
            await self.dry_run_test_case(current.name)
            current = current.next
        self.result_printer.stop_live()

    def _resolve_candidate_params(self, candidate_args):
        kw_params = DataReader.get_keyword_params(list(candidate_args))
        positional_params = DataReader.get_positional_params(list(candidate_args))
        resolved_positional_params = [self.resolve_param(param) for param in positional_params]
        resolved_kw_params = {}
        for key, value in kw_params.items():
            if value.startswith("${") and value.endswith("}"):
                value = self.resolve_param(value)
            resolved_kw_params[key] = value
        return resolved_positional_params, resolved_kw_params


class PytestRunner(Runner):
    instance = None

    def __init__(
        self,
        session: Session,
        keyword_map: Dict,
        event_manager,
    ):
        self.session = session
        self.test_cases = self.session.test_cases if self.session and hasattr(self.session, "test_cases") else None
        self.modules = self.session.modules if self.session and hasattr(self.session, "modules") else {}
        self.elements = (
            self.session.elements if self.session and isinstance(self.session.elements, ElementData)
            else ElementData()
        )
        self.apis = self.session.apis if self.session and hasattr(self.session, "apis") and self.session.apis is not None else ApiData()
        self.keyword_map = keyword_map
        self.result_printer = NullResultPrinter()
        self.config_handler: ConfigHandler = self.session.config_handler
        self.config: Config = self.session.config
        self.event_manager = event_manager
        PytestRunner.instance = self

    def resolve_param(self, param: str) -> str:
        if not param.startswith("${") or not param.endswith("}"):
            return param
        var_name = param[2:-1].strip()
        value = self.elements.get_first(var_name)
        if value is None:
            pytest.fail(f"Variable '{var_name}' not found")
        return value

    def _execute_keyword(
        self,
        keyword: str,
        params: List[str],
        dry_run: bool = False,
        module_id: str = "unknown",
        testcase_id: str = "unknown",
    ) -> bool:
        keyword_id = str(uuid.uuid4())
        func_name = "_".join(keyword.split()).lower()

        if dry_run:
            return self._execute_keyword_dry_run(keyword, func_name, keyword_id, module_id)

        self._queue_event_running(keyword, keyword_id, module_id)
        method = self.keyword_map.get(func_name)
        if not method:
            self._queue_event_fail(keyword, keyword_id, module_id, f"Keyword not found: {keyword}")
            pytest.fail(f"Keyword not found: {keyword}")

        param_candidates = self._build_param_candidates_pytest(params, keyword_id, keyword, module_id)
        if param_candidates is None:
            return False

        return self._try_execute_with_fallback_pytest(
            method, param_candidates, keyword, keyword_id, module_id
        )

    def _execute_keyword_dry_run(self, keyword, func_name, keyword_id, module_id):
        if not self.keyword_map.get(func_name):
            queue_event_sync(
                Event(
                    entity_type="keyword",
                    entity_id=keyword_id,
                    name=keyword,
                    status=EventStatus.FAIL,
                    message=f"Keyword not found: {keyword}",
                    parent_id=module_id,
                    extra={"session_id": self.session.session_id},
                ),
                self.event_manager
            )
            pytest.fail(f"Keyword not found: {keyword}")
        queue_event_sync(
            Event(
                entity_type="keyword",
                entity_id=keyword_id,
                name=keyword,
                status=EventStatus.PASS,
                message="Keyword validated",
                parent_id=module_id,
                extra={"session_id": self.session.session_id},
            ),
            self.event_manager
        )
        return True

    def _queue_event_running(self, keyword, keyword_id, module_id):
        queue_event_sync(
            Event(
                entity_type="keyword",
                entity_id=keyword_id,
                name=keyword,
                status=EventStatus.RUNNING,
                parent_id=module_id,
                extra={"session_id": self.session.session_id},
            ),
            self.event_manager
        )

    def _queue_event_fail(self, keyword, keyword_id, module_id, message):
        queue_event_sync(
            Event(
                entity_type="keyword",
                entity_id=keyword_id,
                name=keyword,
                status=EventStatus.FAIL,
                message=message,
                parent_id=module_id,
                extra={"session_id": self.session.session_id},
            ),
            self.event_manager
        )

    def _build_param_candidates_pytest(self, params, keyword_id, keyword, module_id):
        param_candidates = []
        for param in params:
            if isinstance(param, str) and param.startswith("${") and param.endswith("}"):
                var_name = param[2:-1].strip()
                values = self.elements.get_element(var_name)
                if not values:
                    queue_event_sync(
                        Event(
                            entity_type="keyword",
                            entity_id=keyword_id,
                            name=keyword,
                            status=EventStatus.FAIL,
                            message=f"Element not found: {var_name}",
                            parent_id=module_id,
                            extra={"session_id": self.session.session_id},
                        ),
                        self.event_manager
                    )
                    pytest.fail(f"Element not found: {var_name}")
                    return None
                param_candidates.append(values)
            else:
                param_candidates.append([param])
        return param_candidates

    def _try_execute_with_fallback_pytest(self, method, param_candidates, keyword, keyword_id, module_id):
        MAX_ATTEMPTS = 20
        attempts = 0
        last_exc = None

        for candidate_args in product(*param_candidates):
            attempts += 1
            if attempts > MAX_ATTEMPTS:
                break
            try:
                resolved_positional_params, resolved_kw_params = self._resolve_candidate_params(candidate_args)
                method(*resolved_positional_params, **resolved_kw_params)
                self._queue_keyword_pass_event(keyword, keyword_id, module_id)
                return True
            except OpticsError as oe:
                if str(oe.code).startswith("E02") or oe.code == Code.X0201:
                    last_exc = oe
                    continue
                else:
                    self._handle_keyword_fail(keyword, keyword_id, module_id, f"Keyword '{keyword}' failed: {oe}")
                    return False
            except Exception as e:
                self._handle_keyword_fail(keyword, keyword_id, module_id, f"Keyword '{keyword}' failed: {e}")
                return False

        self._handle_keyword_fail(
            keyword,
            keyword_id,
            module_id,
            f"Keyword '{keyword}' failed after {attempts} attempts; last error: {last_exc}"
        )
        return False

    def _queue_keyword_pass_event(self, keyword, keyword_id, module_id):
        queue_event_sync(
            Event(
                entity_type="keyword",
                entity_id=keyword_id,
                name=keyword,
                status=EventStatus.PASS,
                parent_id=module_id,
                extra={"session_id": self.session.session_id},
            ),
            self.event_manager
        )

    def _handle_keyword_fail(self, keyword, keyword_id, module_id, msg):
        self._queue_event_fail(keyword, keyword_id, module_id, msg)
        pytest.fail(msg)

    def _resolve_candidate_params(self, candidate_args):
        kw_params = DataReader.get_keyword_params(list(candidate_args))
        positional_params = DataReader.get_positional_params(list(candidate_args))
        resolved_positional_params = [self.resolve_param(param) for param in positional_params]
        resolved_kw_params = {}
        for key, value in kw_params.items():
            if value.startswith("${") and value.endswith("}"):
                value = self.resolve_param(value)
            resolved_kw_params[key] = value
        return resolved_positional_params, resolved_kw_params

    def _process_module(
        self, module_name: str, dry_run: bool = False, testcase_id: str = "unknown"
    ) -> bool:
        if not isinstance(self.modules, ModuleData):
            raise TypeError("self.modules must be a ModuleData instance.")

        module_id = str(uuid.uuid4())
        queue_event_sync(
            Event(
                entity_type="module",
                entity_id=module_id,
                name=module_name,
                status=EventStatus.RUNNING,
                parent_id=testcase_id,
                extra={"session_id": self.session.session_id},
            ),
            self.event_manager
        )

        module_steps = self.modules.modules.get(module_name)
        if module_steps is None:
            queue_event_sync(
                Event(
                    entity_type="module",
                    entity_id=module_id,
                    name=module_name,
                    status=EventStatus.FAIL,
                    message=f"Module '{module_name}' not found",
                    parent_id=testcase_id,
                    extra={"session_id": self.session.session_id},
                ),
                self.event_manager
            )
            pytest.fail(f"Module '{module_name}' not found")

        for step in module_steps:
            if isinstance(step, tuple) and len(step) == 2:
                keyword, params = step
            else:
                continue
            if not self._execute_keyword(
                keyword,
                params,
                dry_run=dry_run,
                module_id=module_id,
                testcase_id=testcase_id,
            ):
                queue_event_sync(
                    Event(
                        entity_type="module",
                        entity_id=module_id,
                        name=module_name,
                        status=EventStatus.FAIL,
                        parent_id=testcase_id,
                        extra={"session_id": self.session.session_id},
                    ),
                    self.event_manager
                )
                return False
        queue_event_sync(
            Event(
                entity_type="module",
                entity_id=module_id,
                name=module_name,
                status=EventStatus.PASS,
                parent_id=testcase_id,
                extra={"session_id": self.session.session_id},
            ),
            self.event_manager
        )
        return True

    def execute_test_case_sync(
        self, test_case: str, dry_run: bool = False
    ) -> TestCaseResult:
        start_time = time.time()
        testcase_id = str(uuid.uuid4())
        result = TestCaseResult(
            id=testcase_id, name=test_case, elapsed="0.00s", status="NOT_RUN"
        )
        queue_event_sync(
            Event(
                entity_type="test_case",
                entity_id=testcase_id,
                name=test_case,
                status=EventStatus.RUNNING,
                extra={"session_id": self.session.session_id},
            ),
            self.event_manager
        )
        current = self.test_cases
        while current and current.name != test_case:
            current = current.next
        if not current:
            queue_event_sync(
                Event(
                    entity_type="test_case",
                    entity_id=testcase_id,
                    name=test_case,
                    status=EventStatus.FAIL,
                    message=f"Test case '{test_case}' not found",
                    extra={"session_id": self.session.session_id},
                ),
                self.event_manager
            )
            result.status = "FAIL"
            result.elapsed = f"{time.time() - start_time:.2f}s"
            return result

        result.status = "RUNNING"
        module_current = current.modules_head
        while module_current:
            if not self._process_module(
                module_name=module_current.name,
                dry_run=dry_run,
                testcase_id=testcase_id,
            ):
                queue_event_sync(
                    Event(
                        entity_type="test_case",
                        entity_id=testcase_id,
                        name=test_case,
                        status=EventStatus.FAIL,
                        extra={"session_id": self.session.session_id},
                    ),
                    self.event_manager
                )
                result.status = "FAIL"
                break
            module_current = module_current.next
        else:
            queue_event_sync(
                Event(
                    entity_type="test_case",
                    entity_id=testcase_id,
                    name=test_case,
                    status=EventStatus.PASS,
                    extra={"session_id": self.session.session_id},
                ),
                self.event_manager
            )
            result.status = "PASS"
        result.elapsed = f"{time.time() - start_time:.2f}s"
        return result

    async def execute_test_case(self, test_case: str) -> TestCaseResult:
        return self.execute_test_case_sync(test_case, dry_run=False)

    async def dry_run_test_case(self, test_case: str) -> TestCaseResult:
        return self.execute_test_case_sync(test_case, dry_run=True)

    async def run_all(self) -> None:
        test_cases = [node.name for node in self._iter_test_cases()]
        result = self._run_pytest(test_cases, dry_run=False)
        if not result:
            raise RuntimeError("Pytest execution failed")

    async def dry_run_all(self) -> None:
        test_cases = [node.name for node in self._iter_test_cases()]
        result = self._run_pytest(test_cases, dry_run=True)
        if not result:
            raise RuntimeError("Pytest execution failed")

    def _iter_test_cases(self):
        current = self.test_cases
        while current:
            yield current
            current = current.next

    def _run_pytest(self, test_cases: List[str], dry_run: bool = False) -> bool:
        """Run a test suite using pytest, generating test files and handling results.

        Args:
            test_cases: List of test case names to run.
            dry_run: If True, perform a dry run without executing keywords.

        Returns:
            bool: True if all tests passed, False otherwise.
        """
        temp_dir = tempfile.mkdtemp()
        test_file_path = f"{temp_dir}/test_generated_{int(time.time() * 1000)}.py"
        conftest_path = f"{temp_dir}/conftest.py"
        extra = {"test_cases": ", ".join(test_cases)}

        internal_logger.debug(f"Generating test file: {test_file_path}", extra=extra)

        with open(conftest_path, "w") as f:
            f.write("""
import pytest
from optics_framework.common.runner.test_runnner import PytestRunner

@pytest.fixture
def runner():
    return PytestRunner.instance
""")

        test_code = "import pytest\n\n" + "".join(
            f"def test_{tc.replace(' ', '_')}(runner):\n"
            f"    result = runner.execute_test_case_sync('{tc}', dry_run={dry_run})\n"
            f"    assert result.status == 'PASS', 'Test case failed with status: ' + result.status\n"
            for tc in test_cases
        )
        internal_logger.debug(f"Generated test code:\n{test_code}", extra=extra)
        with open(test_file_path, "w") as f:
            f.write(test_code)

        for module_name in list(sys.modules.keys()):
            if module_name.startswith("test_generated"):
                del sys.modules[module_name]

        junit_path = f"{self.session.config.execution_output_path}/junit_output.xml"
        result = pytest.main(
            [
                temp_dir,
                "-q",
                "--disable-warnings",
                f"--junitxml={junit_path}",
                "--no-cov",
            ]
        )
        shutil.rmtree(temp_dir)

        if result == 0:
            internal_logger.info("Pytest execution completed successfully")
            queue_event_sync(
                Event(
                    entity_type="execution",
                    entity_id="pytest",
                    name="Pytest",
                    status=EventStatus.PASS,
                    message="Pytest execution completed",
                    extra={"session_id": self.session.session_id},
                ),
                self.event_manager
            )
            return True
        else:
            internal_logger.error("Pytest execution failed")
            queue_event_sync(
                Event(
                    entity_type="execution",
                    entity_id="pytest",
                    name="Pytest",
                    status=EventStatus.FAIL,
                    message="Pytest execution failed",
                    extra={"session_id": self.session.session_id},
                ),
                self.event_manager
            )
            return False

class KeywordRunner(Runner):
    def __init__(self, keyword_map):
        self.keyword_map = keyword_map
