import json
import re
import os
import time
import uuid
import asyncio
import contextlib
import contextvars
import tempfile
import shutil
import sys
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, NoReturn, Optional, Any
import pytest
from optics_framework.common.session_manager import Session
from optics_framework.common.error import ERROR_REGISTRY, OpticsError, Code, explain, undefined_element
from optics_framework.common.runner.keyword_register import keyword_not_found, suggest_keyword
from optics_framework.common.config_handler import Config, ConfigHandler
from optics_framework.common.logging_config import (
    SENSITIVE_PREFIX,
    mask_secret_values,
    mask_secrets,
    use_secrets,
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
    CaseKind,
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
    EntityType,
    EventStatus,
    CommandType,
    Event,
    redact_event,
)
from optics_framework.common.runner.step_params import bind_params, step_arguments, step_candidates
from optics_framework.common.popup_guard import PopupRun, Verdict
from optics_framework.common.strategies import StrategyManager
from optics_framework.common import fallback, utils
from optics_framework.common.Junit_eventhandler import get_junit_handler_registry, run_plan
from optics_framework.common.session_liveness import is_dead_session_error
from optics_framework.common.error_detection import (
    ON_SCREEN_ERROR_CASE,
    describe_detected_error,
    extract_visible_text,
    detect_errors_in_text,
)


class Runner:
    test_case: TestCaseNode
    result_printer: IResultPrinter
    keyword_map: Dict[str, Callable[..., Any]]
    apis: ApiData
    elements: ElementData

    def _clear_failure(self) -> None:
        self._last_failure_reason: Optional[str] = None
        self._last_failure_code: Optional[str] = None
        self._last_failure_status = EventStatus.FAIL
        self._last_failure_hint: Optional[str] = None

    def _note_failure(self, reason: str, code: str, status: EventStatus = EventStatus.FAIL, hint: Optional[str] = None) -> None:
        """Record the latest failure reason, and log it where logs are kept."""
        self._last_failure_reason = reason
        self._last_failure_code = code
        self._last_failure_status = status
        spec = ERROR_REGISTRY.get(code)
        self._last_failure_hint = hint or (spec.hint if spec else None)
        # With no handler, Python writes the bare reason to stderr, a second copy of what the output shows.
        if execution_logger.hasHandlers():
            execution_logger.error(reason)

    def _unknown_step_reason(self, name: str) -> str:
        """Reason for a step that resolves to neither a module nor a keyword."""
        return f"'{name}' is not a keyword or a module.{suggest_keyword(name, self.keyword_map)}"

    def _hint_extra(self, status: EventStatus, reason: Optional[str]) -> dict[str, str]:
        """The next step for the failure this event reports, when the runner knows one."""
        failed = status in (EventStatus.FAIL, EventStatus.ERROR)
        if failed and reason and reason == self._last_failure_reason and self._last_failure_hint:
            return {"hint": self._last_failure_hint}
        return {}

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


# A stopped run leaves its test case open, so the report marks it E0706.
_STOPS = (KeyboardInterrupt, SystemExit)


def queue_event_sync(event: Event, event_manager) -> None:
    """Queue an event synchronously for pytest."""
    event = redact_event(event)
    internal_logger.debug(f"Queueing event (sync): {event.model_dump()}")
    for _, subscriber in event_manager.subscribers.items():
        try:
            _run_to_completion(subscriber.on_event(event))
        except RuntimeError as e:
            internal_logger.warning(f"Failed to process event synchronously: {e}")


def _run_to_completion(coro) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    # pytest.main runs inside the batch's event loop, which cannot be re-entered.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(contextvars.copy_context().run, asyncio.run, coro).result()


def _plan_report(session_id: str, test_cases: Optional[TestCaseNode]) -> None:
    handler = get_junit_handler_registry().get_handler(session_id)
    if handler is not None:
        handler.plan_run(session_id, run_plan(test_cases))


class SetupGuard:
    """After a failed suite setup nothing but the suite teardown runs; after a failed test setup, not its test.

    A run a popup stopped is the same: only the suite teardown, which may close the app, still runs."""

    def __init__(self) -> None:
        self._blocked: Dict[CaseKind, str] = {}

    def reason(self, kind: CaseKind) -> Optional[str]:
        return self._blocked.get(kind)

    def stop(self, reason: str) -> None:
        self._blocked.update(dict.fromkeys((CaseKind.SUITE_SETUP, CaseKind.SETUP, CaseKind.TEST, CaseKind.TEARDOWN), reason))

    def record(self, kind: CaseKind, passed: bool, failure: Optional[str]) -> None:
        if kind is CaseKind.SETUP:
            self._blocked.pop(CaseKind.TEST, None)
        if passed:
            return
        if kind is CaseKind.SUITE_SETUP:
            reason = f"Suite setup failed: {failure}"
            self._blocked.update(dict.fromkeys((CaseKind.SETUP, CaseKind.TEST, CaseKind.TEARDOWN), reason))
        elif kind is CaseKind.SETUP:
            self._blocked[CaseKind.TEST] = f"Setup failed: {failure}"


def pop_heal(keyword_map: Dict[str, Callable[..., Any]]) -> dict[str, Any] | None:
    """Read and clear the AI self-heal record, wherever in the keyword's run it healed."""
    owners = {getattr(method, "__self__", None) for method in keyword_map.values()}
    heals = [owner._pop_last_heal_info() for owner in owners if hasattr(owner, "_pop_last_heal_info")]
    return next((heal for heal in heals if heal), None)


def heal_extra(heal: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, str]:
    # Masked before json.dumps, whose escapes would hide a secret from the masking at publish.
    return {
        "healed": "true",
        "heal_summary": heal["summary"],
        "heal_steps": json.dumps(mask_secret_values(steps)),
    }


def popup_event(popup: dict, session_id: str, keyword: str, keyword_id: str, module_id: str, **context: Any) -> Event:
    """The ``popup`` event for one inspection the guard recorded; its fields travel in ``args``, since ``extra`` holds strings only."""
    verdict = popup["verdict"]
    return Event(
        entity_type=EntityType.POPUP,
        entity_id=keyword_id,
        name=keyword,
        status=EventStatus.PASS if verdict in (Verdict.HANDLED, Verdict.NONE) else EventStatus.FAIL,
        message=popup.get("popup_text") or "",
        code=popup.get("code"),
        parent_id=module_id,
        extra={"session_id": session_id},
        args={**popup, "session_id": session_id, "test_case": test_context.current_test_case.get(), **context},
    )


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
        self._failed_steps_elements: List[str] = []
        self.result_printer = result_printer
        self.config = session.config
        self.event_manager = event_manager
        self._clear_failure()
        self.popup_run = PopupRun()
        if hasattr(self.modules, "modules"):
            execution_logger.debug(
                "Initialized test_state: %s with %d modules",
                self.modules.modules.keys(),
                len(self.modules.modules)
            )
        self._initialize_test_state()

    def _safe_resolve_param(self, param: str) -> str:
        try:
            return self.resolve_param(param)
        except (ValueError, OpticsError):
            return param

    def _initialize_test_state(self) -> None:
        def _init_keywords(module_node):
            keywords = []
            current_keyword = module_node.keywords_head
            while current_keyword:
                resolved_params = [
                    self._safe_resolve_param(param) for param in current_keyword.params
                ]
                resolved_name = mask_secrets(
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
        internal_logger.debug(
            "Initialized test_state: %s with %d modules",
            test_state.keys(),
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
        var_name = utils.whole_var_name(param)
        if var_name is None:
            return param
        resolved_value = self.elements.get_first(var_name)
        if resolved_value is None:
            raise undefined_element(var_name)
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
    ) -> TestCaseResult | ModuleResult | KeywordResult:
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

    def _step_resolves(self, module_name: str) -> bool:
        definition = (
            self.modules.get_module_definition(module_name)
            if self.modules is not None
            else None
        )
        if definition:
            return True
        func_name = "_".join(module_name.split()).lower()
        return func_name in self.keyword_map

    def _update_status(
        self,
        result: TestCaseResult | ModuleResult | KeywordResult,
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
        entity_type: EntityType,
        node: Node,
        status: EventStatus,
        reason: Optional[str] = None,
        parent_id: Optional[str] = None,
        args: Optional[List[Any]] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        elapsed: Optional[float] = None,
        logs: Optional[List[logging.LogRecord]] = None,
        stored: dict[str, str] | None = None,
        code: Optional[str] = None,
        healed: dict[str, str] | None = None,
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
            code=code,
            parent_id=parent_id,
            extra={
                "session_id": self.session_id,
                **({"kind": node.kind.value} if isinstance(node, TestCaseNode) else {}),
                **({"stored": json.dumps(stored)} if stored else {}),
                **(healed or {}),
                **self._hint_extra(status, reason),
            },
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
        should_retry = False
        try:
            keyword_result = self._find_result(
                test_case_result.name, module_node.name, keyword_node.id
            )
            internal_logger.debug(
                "Executing keyword: %s (id: %s)", keyword_node.name, keyword_node.id
            )
            start_time = time.time()

            keyword_node.state = State.RUNNING
            await self._send_event(
                EntityType.KEYWORD,
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

            params, store_as = utils.split_store_as(keyword_node.params)
            param_candidates = await self._build_param_candidates(keyword_node, method, params, module_node, keyword_result, start_time, test_case_result, capture_handler)
            if param_candidates is None:
                return False

            result = await self._try_execute_with_fallback(
                method, param_candidates, keyword_node, module_node, keyword_result, start_time, test_case_result, capture_handler,
                store_as=store_as,
            )
            if result is not None:
                return result

            await asyncio.sleep(self.config.halt_duration)
            should_retry = await self._process_commands(keyword_node, module_node)
        finally:
            execution_logger.removeHandler(capture_handler)
            await self._publish_popup_events(keyword_node, module_node)

        if should_retry:
            return await self._execute_keyword(
                keyword_node, module_node, test_case_result, extra
            )
        return False

    async def _publish_popup_events(self, keyword_node: KeywordNode, module_node: ModuleNode) -> None:
        popups = self.popup_run.drain()
        if not popups:
            return
        step, keyword = 1, module_node.keywords_head
        while keyword and keyword.id != keyword_node.id:
            step, keyword = step + 1, keyword.next
        for popup in popups:
            await queue_event(
                popup_event(popup, self.session_id, keyword_node.name, keyword_node.id, module_node.id,
                            module=module_node.name, step_index=step),
                self.event_manager,
            )

    async def _handle_keyword_not_found(self, keyword_node, module_node, keyword_result, start_time, test_case_result):
        keyword_node.state = State.COMPLETED_FAILED
        reason = keyword_not_found(keyword_node.name, self.keyword_map).summary
        self._note_failure(reason, Code.E0402)
        keyword_result.reason = reason
        keyword_result.hint = self._last_failure_hint or ""
        await self._send_event(
            EntityType.KEYWORD,
            keyword_node,
            EventStatus.FAIL,
            reason=reason,
            code=Code.E0402,
            parent_id=module_node.id,
            start_time=start_time,
            end_time=time.time(),
            elapsed=time.time() - start_time,
        )
        self._update_status(keyword_result, "FAIL", time.time() - start_time, test_case_result.name)

    async def _build_param_candidates(self, keyword_node, method, params, module_node, keyword_result, start_time, test_case_result, capture_handler):
        try:
            return step_candidates(method, params, self.elements)
        except OpticsError as error:
            await self._handle_element_not_found(keyword_node, module_node, keyword_result, start_time, test_case_result, error, capture_handler)
            return None

    async def _handle_element_not_found(self, keyword_node, module_node, keyword_result, start_time, test_case_result, error, capture_handler):
        keyword_node.state = State.COMPLETED_FAILED
        reason = error.summary
        self._note_failure(reason, Code.E0201, hint=error.own_hint)
        keyword_result.reason = reason
        keyword_result.hint = self._last_failure_hint or ""
        await self._send_event(
            EntityType.KEYWORD,
            keyword_node,
            EventStatus.FAIL,
            reason,
            code=Code.E0201,
            parent_id=module_node.id,
            start_time=start_time,
            end_time=time.time(),
            elapsed=time.time() - start_time,
            logs=capture_handler.records,
        )
        self._update_status(keyword_result, "FAIL", time.time() - start_time, test_case_result.name)

    async def _try_execute_with_fallback(
        self, method, param_candidates, keyword_node, module_node, keyword_result, start_time, test_case_result, capture_handler,
        store_as=None,
    ):
        def attempt(candidate_args):
            positional, keywords = step_arguments(method, candidate_args, self.elements)
            bind_params(keyword_node.name, method, positional, keywords)
            return utils.call_and_store(self.elements, method, positional, keywords, store_as)[1]

        pop_heal(self.keyword_map)
        try:
            stored = fallback.run(param_candidates, attempt, f"keyword '{keyword_node.name}'")
        except Exception as exc:
            await self._handle_keyword_failure(keyword_node, module_node, keyword_result, start_time, test_case_result, exc, capture_handler)
            return None if isinstance(exc, fallback.FallbackExhausted) else False
        finally:
            heal = pop_heal(self.keyword_map)
        keyword_result.stored = stored
        keyword_node.state = State.COMPLETED_PASSED
        await self._send_event(
            EntityType.KEYWORD,
            keyword_node,
            EventStatus.PASS,
            parent_id=module_node.id,
            start_time=start_time,
            end_time=time.time(),
            elapsed=time.time() - start_time,
            logs=capture_handler.records,
            stored=stored,
            healed=heal_extra(heal, self._steps_with_refs(heal, keyword_node.params)) if heal else None,
        )
        self._update_status(keyword_result, "PASS", time.time() - start_time, test_case_result.name)
        return True

    def _steps_with_refs(self, heal: dict[str, Any], params: List[str]) -> list[dict[str, Any]]:
        """The heal's steps with each value of the keyword's ``${name}`` params written back as ``${name}``."""
        refs = {}
        for name in filter(None, map(utils.whole_var_name, params)):
            for value in self.elements.get_element(name) or []:
                refs.setdefault(utils.without_prefix(value, SENSITIVE_PREFIX), f"${{{name}}}")
        return [
            {**step, "params": [refs.get(param, param) for param in step.get("params", [])]}
            for step in heal.get("suggested_steps", [])
        ]

    async def _handle_keyword_failure(self, keyword_node, module_node, keyword_result, start_time, test_case_result, exc, capture_handler):
        keyword_node.state = State.COMPLETED_FAILED
        explanation = explain(exc)
        reason = explanation.summary
        status = EventStatus.ERROR if is_dead_session_error(exc) else EventStatus.FAIL
        code = exc.code if isinstance(exc, OpticsError) else Code.X0401
        self._note_failure(reason, code, status, explanation.hint)
        execution_logger.debug("Failure detail for %s: %s", keyword_node.name, " <- ".join(explanation.detail))
        keyword_result.reason = reason
        keyword_result.hint = self._last_failure_hint or ""
        await self._send_event(
            EntityType.KEYWORD,
            keyword_node,
            status,
            reason,
            code=code,
            parent_id=module_node.id,
            start_time=start_time,
            end_time=time.time(),
            elapsed=time.time() - start_time,
            logs=capture_handler.records,
        )
        self._update_status(keyword_result, "FAIL", time.time() - start_time, test_case_result.name)
        self._save_failure_elements(test_case_result.name, keyword_result.resolved_name)

    def _save_failure_elements(self, test_case: str, step: str) -> None:
        """Keep the screen's compact element list for failed_steps_elements.json, to fix a failed step's locators."""
        get_elements = self.keyword_map.get("get_interactive_elements")
        if get_elements is None or self._capture_dir() is None:
            return
        try:
            record = {"test_case": test_case, "step": step, "elements": get_elements(compact=True)}
            self._failed_steps_elements.append(json.dumps(mask_secret_values(record), indent=2, ensure_ascii=False))
        except Exception as e:
            internal_logger.warning("Element list for the failed step skipped: %s", e)

    def _write_failed_steps_elements(self) -> None:
        """Write the run's failed-step element lists once, as one JSON array."""
        output_dir = self.config.capture_dir
        if not self._failed_steps_elements or output_dir is None:
            return
        try:
            with open(os.path.join(output_dir, "failed_steps_elements.json"), "w", encoding="utf-8") as f:
                f.write("[\n" + ",\n".join(self._failed_steps_elements) + "\n]\n")
        except OSError as e:
            internal_logger.warning("failed_steps_elements.json not written: %s", e)

    def _capture_dir(self) -> Optional[str]:
        """Where a failure's captures go, or None when captures are off or the driver is closed."""
        output_dir = self.config.capture_dir
        if not output_dir or not self._screen_readable():
            return None
        return output_dir

    def _screen_readable(self) -> bool:
        return self._end_of_run_driver_open() and bool(self.session.optics.get_element_source().instances)

    def _end_of_run_driver_open(self) -> bool:
        """Best-effort check that the driver session is still alive.

        Drivers expose their live handle as ``page`` (Playwright) or ``driver``
        (Appium/Selenium); a present-but-None handle means the run's own teardown
        already terminated it, so capture attempts would only raise. Handles must
        be read off the wrapper's ``active_instance`` -- ``InstanceFallback``
        routes attribute access through ``__getattr__``, which hides the real
        value behind a generated callable.
        """
        driver_fallback = getattr(self.session, "driver", None)
        if driver_fallback is None:
            return False
        active_driver = getattr(driver_fallback, "active_instance", None)
        driver = active_driver if active_driver is not None else driver_fallback
        return all(
            getattr(driver, attr) is not None
            for attr in ("page", "driver")
            if hasattr(driver, attr)
        )

    async def _capture_end_of_run_artifacts(self) -> None:
        if not self._screen_readable():
            internal_logger.debug(
                "End-of-run artifact capture skipped: driver session unavailable."
            )
            return
        try:
            config = self.session.config
            strategy_manager = self._strategy_manager()
            if config.capture_dir is not None:
                self._save_end_of_run_screenshot(strategy_manager, config.capture_dir)
            page_source, timestamp = self._save_end_of_run_pagesource(strategy_manager, config.capture_dir)
            await self._run_end_of_run_detection(page_source, timestamp, config.execution_output_path)
        except Exception as e:
            internal_logger.warning("End-of-run artifact capture failed: %s", e)

    def _strategy_manager(self) -> StrategyManager:
        return StrategyManager(
            self.session.optics.get_element_source(),
            self.session.optics.get_text_detection(),
            self.session.optics.get_image_detection(),
        )

    def _capture_failure_artifacts(self, test_case: str) -> None:
        """Save the screenshot and page source a failed test case ended on, both named after it."""
        output_dir = self._capture_dir()
        if output_dir is None:
            return
        try:
            strategy_manager = self._strategy_manager()
            label = f"failure_{test_case}"
            time_stamp = utils.capture_time_stamp()
            utils.save_screenshot(strategy_manager.capture_screenshot(), label, output_dir, time_stamp)
            if strategy_manager.pagesource_strategies:
                page_source, _ = strategy_manager.capture_pagesource()
                utils.save_page_source_file(mask_secrets(page_source), label, output_dir, time_stamp)
        except Exception as e:
            internal_logger.warning("Failure capture for '%s' skipped: %s", test_case, e)

    def _save_end_of_run_screenshot(self, strategy_manager: StrategyManager, output_dir: str) -> None:
        try:
            screenshot = strategy_manager.capture_screenshot()
            utils.save_screenshot(screenshot, "end_of_run", output_dir)
            internal_logger.debug("End-of-run screenshot saved.")
        except Exception as e:
            internal_logger.warning("End-of-run screenshot skipped: %s", e)

    def _save_end_of_run_pagesource(
        self, strategy_manager: StrategyManager, output_dir: str | None
    ) -> tuple[Optional[str], Optional[str]]:
        """The final page source for error detection, saved masked only when captures are on."""
        if not strategy_manager.pagesource_strategies:
            return None, None
        try:
            page_source, timestamp = strategy_manager.capture_pagesource()
            if output_dir is None:
                return page_source, timestamp
            save = utils.save_page_source_html if utils.is_html_source(page_source) else utils.save_page_source
            save(mask_secrets(page_source), timestamp, output_dir)
            internal_logger.debug("End-of-run page source saved.")
            return page_source, timestamp
        except Exception as e:
            internal_logger.warning("End-of-run page source skipped: %s", e)
            return None, None

    async def _run_end_of_run_detection(
        self, page_source: Optional[str], timestamp: Optional[str], output_dir: str
    ) -> None:
        try:
            error_defs = getattr(self.session, "error_definitions", None)
            if not (error_defs and error_defs.get_all() and page_source):
                return
            searchable = extract_visible_text(page_source)
            matched = detect_errors_in_text(searchable, error_defs.get_all(), context_label="end_of_run")
            if not matched:
                internal_logger.debug("No user-defined errors detected in end-of-run page source.")
                return
            for m in matched:
                execution_logger.warning(
                    "ON-SCREEN ERROR DETECTED — code: %s | matched_on: %s | match_string: '%s' | severity: %s | %s",
                    m["error_code"], m["matched_on"], m.get("match_string", ""),
                    m.get("severity", ""), m.get("description", ""),
                )
            out_path = os.path.join(output_dir, f"detected_errors_{self.session_id}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({"timestamp": timestamp, "detected": matched}, f, indent=2)
            internal_logger.debug("Detected errors written to %s", out_path)
            detection = TestCaseResult(id=str(uuid.uuid4()), name=ON_SCREEN_ERROR_CASE, elapsed="0.00s", status="FAIL")
            self.result_printer.test_state[ON_SCREEN_ERROR_CASE] = detection
            self.result_printer.print_tree_log(detection)
            handler = get_junit_handler_registry().get_handler(self.session_id)
            if handler:
                handler.add_detected_errors(self.session_id, matched)
            await self._send_event(
                EntityType.TEST_CASE, TestCaseNode(name=ON_SCREEN_ERROR_CASE, id=detection.id), EventStatus.FAIL,
                reason="; ".join(describe_detected_error(m) for m in matched),
            )
        except Exception as e:
            internal_logger.warning("Error detection scan failed: %s", e)

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
            EntityType.MODULE,
            module_node,
            EventStatus.RUNNING,
            parent_id=test_case_result.id,
            start_time=start_time,
        )
        self._update_status(
            module_result, "RUNNING", time.time() - start_time, test_case_result.name
        )

        if not self._step_resolves(module_node.name):
            module_node.state = State.COMPLETED_FAILED
            reason = self._unknown_step_reason(module_node.name)
            self._note_failure(reason, Code.E0402)
            module_result.reason = reason
            module_result.hint = self._last_failure_hint or ""
            await self._send_event(
                EntityType.MODULE,
                module_node,
                EventStatus.FAIL,
                reason=reason,
                code=Code.E0402,
                parent_id=test_case_result.id,
                start_time=start_time,
                end_time=time.time(),
                elapsed=time.time() - start_time,
            )
            self._update_status(
                module_result, "FAIL", time.time() - start_time, test_case_result.name
            )
            return False

        current = module_node.keywords_head
        while current:
            extra["keyword"] = current.name
            if not await self._execute_keyword(
                current, module_node, test_case_result, extra
            ):
                module_node.state = State.COMPLETED_FAILED
                await self._send_event(
                    EntityType.MODULE,
                    module_node,
                    self._last_failure_status,
                    reason=self._last_failure_reason,
                    code=self._last_failure_code,
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
            EntityType.MODULE,
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
        self._clear_failure()
        use_secrets(self.elements.secrets)
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
                EntityType.TEST_CASE,
                TestCaseNode(name=test_case, id=testcase_id),
                EventStatus.FAIL,
                reason=f"Test case not found: {test_case}",
                code=Code.E0702,
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
            EntityType.TEST_CASE, current, EventStatus.RUNNING, start_time=start_time
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
                        EntityType.TEST_CASE,
                        current,
                        EventStatus.FAIL,
                        reason=self._last_failure_reason,
                        code=self._last_failure_code,
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
                    self._capture_failure_artifacts(current.name)
                    await self._send_event(
                        EntityType.TEST_CASE,
                        current,
                        self._last_failure_status,
                        reason=self._last_failure_reason,
                        code=self._last_failure_code,
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
            EntityType.TEST_CASE,
            current,
            EventStatus.PASS,
            start_time=start_time,
            end_time=time.time(),
            elapsed=time.time() - start_time,
        )
        self._update_status(
            test_case_result, "PASS", time.time() - start_time, test_case_result.name
        )
        return test_case_result

    def _dry_run_keyword(self, keyword_node: KeywordNode, keyword_result: Any) -> None:
        """Resolve a keyword's params without running it; raise what a run would fail on before acting."""
        written, store_as = utils.split_store_as(keyword_node.params)
        method = self.keyword_map.get("_".join(keyword_node.name.split()).lower())
        params = utils.substitute_inline(method, written, self.elements)
        raw = utils.raw_param_indices(method)
        resolved_params = [param if i in raw else self.resolve_param(param) for i, param in enumerate(params)]
        # Nothing runs here, so define the names a run would set.
        if store_as is not None:
            utils.require_store_name(store_as)
            self.elements.store(store_as, f"<{store_as}>")
        if getattr(method, "_defines_variable", False) and params:
            defined = utils.whole_var_name(str(params[0])) or str(params[0]).strip()
            self.elements.remove_element(defined)
            self.elements.add_element(defined, f"<{defined}>")
        if isinstance(keyword_result, KeywordResult):
            keyword_result.resolved_name = mask_secrets(
                f"{keyword_node.name} ({', '.join(resolved_params)})" if resolved_params else keyword_node.name
            )
        if method is None:
            raise keyword_not_found(keyword_node.name, self.keyword_map)
        self._check_params(keyword_node.name, method, written)

    def _check_params(self, name: str, method: Callable, params: List[str]) -> None:
        """Bind the params as a run would, so a step that cannot run fails in dry_run too."""
        positional, keywords = step_arguments(method, params, self.elements)
        bind_params(name, method, positional, keywords)

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
            EntityType.MODULE,
            module_node,
            EventStatus.RUNNING,
            parent_id=testcase_id,
            start_time=start_time,
        )
        self._update_status(module_result, "RUNNING", 0.0, test_case_result.name)

        if not self._step_resolves(module_node.name):
            module_node.state = State.COMPLETED_FAILED
            reason = self._unknown_step_reason(module_node.name)
            self._note_failure(reason, Code.E0402)
            module_result.reason = reason
            module_result.hint = self._last_failure_hint or ""
            await self._send_event(
                EntityType.MODULE,
                module_node,
                EventStatus.FAIL,
                reason=reason,
                code=Code.E0402,
                parent_id=testcase_id,
                start_time=start_time,
                end_time=time.time(),
                elapsed=time.time() - start_time,
            )
            self._update_status(module_result, "FAIL", 0.0, test_case_result.name)
            return False

        keyword_current = module_node.keywords_head
        while keyword_current:
            keyword_result = self._find_result(
                test_case_result.name, module_node.name, keyword_current.id
            )
            keyword_current.state = State.RUNNING
            await self._send_event(
                EntityType.KEYWORD,
                keyword_current,
                EventStatus.RUNNING,
                parent_id=module_node.id,
                start_time=start_time,
            )
            self._update_status(keyword_result, "RUNNING", 0.0, test_case_result.name)

            try:
                self._dry_run_keyword(keyword_current, keyword_result)
            except (ValueError, OpticsError) as e:
                explanation = explain(e)
                reason = explanation.summary
                code = e.code if isinstance(e, OpticsError) else Code.E0403
                self._note_failure(reason, code, hint=explanation.hint)
                keyword_result.reason = reason
                keyword_result.hint = self._last_failure_hint or ""
                keyword_current.state = State.COMPLETED_FAILED
                await self._send_event(
                    EntityType.KEYWORD,
                    keyword_current,
                    EventStatus.FAIL,
                    reason,
                    code=code,
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
                EntityType.KEYWORD,
                keyword_current,
                EventStatus.PASS,
                parent_id=module_node.id,
                start_time=start_time,
                end_time=time.time(),
                elapsed=time.time() - start_time,
            )
            self._update_status(keyword_result, "PASS", 0.0, test_case_result.name)
            keyword_current = keyword_current.next

        module_node.state = State.COMPLETED_PASSED
        await self._send_event(
            EntityType.MODULE, module_node, EventStatus.PASS, parent_id=testcase_id
        )
        self._update_status(module_result, "PASS", 0.0, test_case_result.name)
        return True

    async def execute_test_case(self, test_case: str) -> TestCaseResult:
        return await self._process_test_case(test_case, dry_run=False)

    async def dry_run_test_case(self, test_case: str) -> TestCaseResult:
        return await self._process_test_case(test_case, dry_run=True)

    async def run_all(self) -> None:
        self._start_run()
        current = self.test_cases
        captured = False
        guard = SetupGuard()
        popup_binding = test_context.popup_run.set(self.popup_run)
        try:
            while current:
                # The suite teardown may close the app, so the final screen is read before it.
                if current.kind is CaseKind.SUITE_TEARDOWN:
                    await self._capture_end_of_run_artifacts()
                    captured = True
                skip = guard.reason(current.kind)
                if skip is not None:
                    await self._skip_test_case(current, skip)
                else:
                    result = await self.execute_test_case(current.name)
                    guard.record(current.kind, result.status == "PASS", self._last_failure_reason)
                    if self.popup_run.stop:
                        guard.stop(f"Run stopped by a popup: {self.popup_run.stop}")
                current = current.next
            if not captured:
                await self._capture_end_of_run_artifacts()
        finally:
            test_context.popup_run.reset(popup_binding)
            self._write_failed_steps_elements()
            for line in self.popup_run.summary_lines():
                execution_logger.info(line)
        self.result_printer.stop_live()

    async def _skip_test_case(self, node: TestCaseNode, reason: str) -> None:
        node.state = State.SKIPPED
        await self._send_event(EntityType.TEST_CASE, node, EventStatus.NOT_RUN, reason=reason)
        result = self.result_printer.test_state.get(node.name)
        if result is not None:
            self._update_status(result, "SKIPPED", 0.0, node.name)

    def _start_run(self) -> None:
        _plan_report(self.session_id, self.test_cases)
        self.result_printer.start_run(len(self.result_printer.test_state))
        self.result_printer.start_live()

    async def dry_run_all(self) -> None:
        self._start_run()
        current = self.test_cases
        while current:
            await self.dry_run_test_case(current.name)
            current = current.next
        self.result_printer.stop_live()


def _fail(reason: str) -> NoReturn:
    """Fail the pytest case with the reason alone: its frames add nothing and would print their arguments unmasked."""
    pytest.fail(mask_secrets(reason), pytrace=False)


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
        self._guard = SetupGuard()
        self._clear_failure()
        self._stop: Optional[BaseException] = None
        self.popup_run = PopupRun()
        PytestRunner.instance = self

    def resolve_param(self, param: str) -> str:
        var_name = utils.whole_var_name(param)
        if var_name is None:
            return param
        value = self.elements.get_first(var_name)
        if value is None:
            _fail(f"Variable '{var_name}' not found")
        return value

    def _execute_keyword(
        self,
        keyword: str,
        params: List[str],
        dry_run: bool = False,
        module_id: str = "unknown",
        testcase_id: str = "unknown",
        module_name: Optional[str] = None,
        step_index: Optional[int] = None,
    ) -> bool:
        keyword_id = str(uuid.uuid4())
        func_name = "_".join(keyword.split()).lower()

        if dry_run:
            return self._execute_keyword_dry_run(keyword, func_name, keyword_id, module_id)

        self._queue_event_running(keyword, keyword_id, module_id)
        method = self.keyword_map.get(func_name)
        if not method:
            self._handle_keyword_fail(keyword, keyword_id, module_id, keyword_not_found(keyword, self.keyword_map))

        params, store_as = utils.split_store_as(params)
        param_candidates = self._build_param_candidates_pytest(method, params, keyword_id, keyword, module_id)
        try:
            return self._try_execute_with_fallback_pytest(
                method, param_candidates, keyword, keyword_id, module_id, store_as=store_as
            )
        finally:
            for popup in self.popup_run.drain():
                queue_event_sync(
                    popup_event(popup, self.session.session_id, keyword, keyword_id, module_id, module=module_name, step_index=step_index),
                    self.event_manager,
                )

    def _execute_keyword_dry_run(self, keyword, func_name, keyword_id, module_id):
        if not self.keyword_map.get(func_name):
            self._handle_keyword_fail(keyword, keyword_id, module_id, keyword_not_found(keyword, self.keyword_map))
        queue_event_sync(
            Event(
                entity_type=EntityType.KEYWORD,
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
                entity_type=EntityType.KEYWORD,
                entity_id=keyword_id,
                name=keyword,
                status=EventStatus.RUNNING,
                parent_id=module_id,
                extra={"session_id": self.session.session_id},
            ),
            self.event_manager
        )

    def _queue_event_fail(self, keyword, keyword_id, module_id, message, code, hint=None):
        self._note_failure(message, code, hint=hint)
        queue_event_sync(
            Event(
                entity_type=EntityType.KEYWORD,
                entity_id=keyword_id,
                name=keyword,
                status=EventStatus.FAIL,
                message=message,
                code=code,
                parent_id=module_id,
                extra={"session_id": self.session.session_id, **self._hint_extra(EventStatus.FAIL, message)},
            ),
            self.event_manager
        )

    def _build_param_candidates_pytest(self, method, params, keyword_id, keyword, module_id):
        try:
            return step_candidates(method, params, self.elements)
        except OpticsError as error:
            failure = error
        self._handle_keyword_fail(keyword, keyword_id, module_id, failure)

    def _try_execute_with_fallback_pytest(self, method, param_candidates, keyword, keyword_id, module_id, store_as=None):
        def attempt(candidate_args):
            positional, keywords = step_arguments(method, candidate_args, self.elements)
            bind_params(keyword, method, positional, keywords)
            return utils.call_and_store(self.elements, method, positional, keywords, store_as)[1]

        try:
            stored = fallback.run(param_candidates, attempt, f"keyword '{keyword}'")
        except Exception as exc:
            failure = exc
        else:
            self._queue_keyword_pass_event(keyword, keyword_id, module_id, stored)
            return True
        # Outside the except block, so pytest does not print the unmasked error as the failure's context.
        self._handle_keyword_fail(keyword, keyword_id, module_id, failure)

    def _queue_keyword_pass_event(self, keyword, keyword_id, module_id, stored=None):
        queue_event_sync(
            Event(
                entity_type=EntityType.KEYWORD,
                entity_id=keyword_id,
                name=keyword,
                status=EventStatus.PASS,
                parent_id=module_id,
                extra={"session_id": self.session.session_id, **({"stored": json.dumps(stored)} if stored else {})},
            ),
            self.event_manager
        )

    def _handle_keyword_fail(self, keyword, keyword_id, module_id, error: BaseException):
        explanation = explain(error)
        self._queue_event_fail(keyword, keyword_id, module_id, explanation.summary, explanation.code or Code.X0401, explanation.hint)
        _fail(explanation.summary)

    def _process_module(
        self, module_name: str, dry_run: bool = False, testcase_id: str = "unknown"
    ) -> bool:
        if not isinstance(self.modules, ModuleData):
            raise TypeError("self.modules must be a ModuleData instance.")

        module_id = str(uuid.uuid4())
        queue_event_sync(
            Event(
                entity_type=EntityType.MODULE,
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
            reason = self._unknown_step_reason(module_name)
            self._note_failure(reason, Code.E0402)
            queue_event_sync(
                Event(
                    entity_type=EntityType.MODULE,
                    entity_id=module_id,
                    name=module_name,
                    status=EventStatus.FAIL,
                    message=reason,
                    code=Code.E0402,
                    parent_id=testcase_id,
                    extra={"session_id": self.session.session_id},
                ),
                self.event_manager
            )
            _fail(reason)

        steps = [step for step in module_steps if isinstance(step, tuple) and len(step) == 2]
        passed = stopped = False
        try:
            passed = all(
                self._execute_keyword(
                    keyword, params, dry_run=dry_run, module_id=module_id, testcase_id=testcase_id,
                    module_name=module_name, step_index=index,
                )
                for index, (keyword, params) in enumerate(steps, 1)
            )
        except _STOPS:
            stopped = True
            raise
        finally:
            if not stopped:
                self._queue_module_event(module_id, module_name, testcase_id, passed)
        return passed

    def _queue_module_event(self, module_id, module_name, testcase_id, passed):
        queue_event_sync(
            Event(
                entity_type=EntityType.MODULE,
                entity_id=module_id,
                name=module_name,
                status=EventStatus.PASS if passed else EventStatus.FAIL,
                code=None if passed else self._last_failure_code,
                parent_id=testcase_id,
                extra={"session_id": self.session.session_id},
            ),
            self.event_manager
        )

    def execute_test_case_sync(
        self, test_case: str, dry_run: bool = False
    ) -> TestCaseResult:
        with test_context.case_scope(test_case, self.popup_run):
            return self._execute_case(test_case, dry_run)

    def _execute_case(self, test_case: str, dry_run: bool) -> TestCaseResult:
        start_time = time.time()
        use_secrets(self.elements.secrets)
        testcase_id = str(uuid.uuid4())
        result = TestCaseResult(
            id=testcase_id, name=test_case, elapsed="0.00s", status="NOT_RUN"
        )
        node = next((case for case in self._iter_test_cases() if case.name == test_case), None)
        kind = node.kind if node else CaseKind.TEST
        skip = None if dry_run else self._guard.reason(kind)
        if skip is not None:
            self._queue_test_case_event(testcase_id, test_case, kind, EventStatus.NOT_RUN, skip)
            pytest.skip(skip)
        self._queue_test_case_event(testcase_id, test_case, kind, EventStatus.RUNNING)
        self._clear_failure()
        passed = stopped = False
        try:
            passed = self._run_modules(node, test_case, testcase_id, dry_run)
        except _STOPS as stop:
            stopped = True
            self._stop = stop
            if isinstance(stop, SystemExit):
                # pytest records a SystemExit as a failed test and runs the next one.
                pytest.exit("The run was stopped.", returncode=1)
            raise
        finally:
            if not stopped:
                self._end_test_case(result, testcase_id, test_case, kind, passed, dry_run, start_time)
        return result

    def _end_test_case(self, result, testcase_id, test_case, kind, passed, dry_run, start_time):
        if not dry_run:
            self._guard.record(kind, passed, self._last_failure_reason)
        status = EventStatus.PASS if passed else EventStatus.FAIL
        self._queue_test_case_event(
            testcase_id, test_case, kind, status, self._last_failure_reason, self._last_failure_code
        )
        if self.popup_run.stop:
            self._guard.stop(f"Run stopped by a popup: {self.popup_run.stop}")
        result.status = status.value
        result.elapsed = f"{time.time() - start_time:.2f}s"

    def _run_modules(self, node: Optional[TestCaseNode], test_case: str, testcase_id: str, dry_run: bool) -> bool:
        if node is None:
            self._note_failure(f"Test case not found: {test_case}", Code.E0702)
            return False
        module = node.modules_head
        while module:
            if not self._process_module(module_name=module.name, dry_run=dry_run, testcase_id=testcase_id):
                return False
            module = module.next
        return True

    def _queue_test_case_event(self, testcase_id, name, kind, status, message=None, code=None):
        queue_event_sync(
            Event(
                entity_type=EntityType.TEST_CASE,
                entity_id=testcase_id,
                name=name,
                status=status,
                message=message or "",
                code=code,
                extra={"session_id": self.session.session_id, "kind": kind.value, **self._hint_extra(status, message)},
            ),
            self.event_manager
        )

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
        self._guard = SetupGuard()
        self._stop = None
        self.popup_run = PopupRun()
        _plan_report(self.session.session_id, self.test_cases)
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
            f"def test_{i}_{re.sub(r'\W', '_', tc)}(runner):\n"
            f"    result = runner.execute_test_case_sync({tc!r}, dry_run={dry_run})\n"
            f"    assert result.status == 'PASS', 'Test case failed with status: ' + result.status\n"
            for i, tc in enumerate(test_cases)
        )
        internal_logger.debug(f"Generated test code:\n{test_code}", extra=extra)
        with open(test_file_path, "w") as f:
            f.write(test_code)

        for module_name in list(sys.modules):  # NOSONAR the loop deletes from sys.modules
            if module_name.startswith("test_generated"):
                del sys.modules[module_name]

        junit_path = f"{self.session.config.execution_output_path}/junit_output.xml"
        # stdout carries the run's progress lines, bound before pytest starts: pytest
        # reports on stderr and captures at sys level, so neither one lands in it.
        with contextlib.redirect_stdout(sys.stderr):
            result = pytest.main(
                [
                    temp_dir,
                    "-q",
                    "--capture=sys",
                    "--disable-warnings",
                    f"--junitxml={junit_path}",
                    "--no-cov",
                ]
            )
        shutil.rmtree(temp_dir)
        for line in self.popup_run.summary_lines():
            execution_logger.info(line)
        if self._stop is not None:
            raise self._stop

        if result == 0:
            internal_logger.info("Pytest execution completed successfully")
            queue_event_sync(
                Event(
                    entity_type=EntityType.EXECUTION,
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
                    entity_type=EntityType.EXECUTION,
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
