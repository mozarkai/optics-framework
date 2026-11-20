import io
import time
from types import SimpleNamespace

import pytest
from rich.console import Console

from optics_framework.common import test_context
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.events import EventStatus
from optics_framework.common.logging_config import LogCaptureBuffer
from optics_framework.common.popup_guard import PopupRun
from optics_framework.common.models import (
    ElementData,
    KeywordNode,
    ModuleData,
    ModuleNode,
    State,
)
from optics_framework.common.runner.printers import (
    NullResultPrinter,
    ModuleResult,
    KeywordResult,
    TerminalWidthProvider,
    TreeResultPrinter,
)
# Aliased so pytest does not collect them.
from optics_framework.common.runner.printers import TestCaseResult as _TestCaseResult
from optics_framework.common.runner import printers
from optics_framework.common.runner.test_runnner import TestRunner as _TestRunner


class _FakeEventManager:
    """Records published events and never issues a retry/add command."""

    def __init__(self):
        self.events = []

    async def publish_event(self, event):
        self.events.append(event)

    async def get_command(self):
        return None


def _make_runner(elements=None, keyword_map=None, modules=None):
    """A ``TestRunner`` with just the collaborators the fallback path touches."""
    runner = _TestRunner.__new__(_TestRunner)
    runner.elements = elements if elements is not None else ElementData()
    runner.keyword_map = keyword_map or {}
    runner.modules = modules if modules is not None else ModuleData()
    runner.session_id = "sess-1"
    runner.result_printer = NullResultPrinter()
    runner.event_manager = _FakeEventManager()
    runner.config = SimpleNamespace(halt_duration=0.0)
    runner.popup_run = PopupRun()
    return runner


def _kw_node(name="Some Keyword", params=None, node_id="kw-1"):
    return KeywordNode(id=node_id, name=name, params=params or [])


def _mod_node(name="mod", node_id="mod-1"):
    return ModuleNode(id=node_id, name=name)


def _kw_result(node_id="kw-1", name="Some Keyword"):
    return KeywordResult(
        id=node_id, name=name, resolved_name=name,
        elapsed="0.00s", status="NOT_RUN", reason="",
    )


def _tc_result(name="tc"):
    return _TestCaseResult(id="tc-1", name=name, elapsed="0.00s", status="NOT_RUN")


async def _run_fallback(runner, method, param_candidates, keyword_node=None):
    """Drive ``_try_execute_with_fallback`` with fresh throwaway result objects."""
    keyword_node = keyword_node or _kw_node()
    return await runner._try_execute_with_fallback(
        method,
        param_candidates,
        keyword_node,
        _mod_node(),
        _kw_result(),
        time.time(),
        _tc_result(),
        LogCaptureBuffer(),
    )


class _Recorder:
    """Raises ``raise_code`` until called with ``fail_until``; ``always_raise`` raises every time."""

    def __init__(self, fail_until=None, raise_code=Code.X0201, always_raise=None):
        self.calls = []
        self.fail_until = fail_until
        self.raise_code = raise_code
        self.always_raise = always_raise

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.always_raise is not None:
            raise self.always_raise
        if self.fail_until is not None and args != self.fail_until:
            raise OpticsError(self.raise_code, f"miss: {args}")
        return None


@pytest.mark.parametrize(("values", "code", "tried", "result"), [
    (3, Code.X0201, 3, None), (25, Code.E0201, 20, None), (3, Code.E0105, 1, False), (3, Code.E0106, 1, False),
])
async def test_only_element_not_found_advances_the_ladder_up_to_twenty_values(values, code, tried, result):
    method = _Recorder(fail_until=("never",), raise_code=code)
    assert await _run_fallback(_make_runner(), method, [[f"v{i}" for i in range(values)]]) is result
    assert len(method.calls) == tried


def _wire_execute_context(runner, keyword_node, module_name="mod"):
    """Populate ``test_state`` so ``_find_result`` can locate the keyword result."""
    kw_result = _kw_result(node_id=keyword_node.id, name=keyword_node.name)
    module_result = ModuleResult(
        name=module_name, elapsed="0.00s", status="NOT_RUN", keywords=[kw_result],
    )
    tc_result = _TestCaseResult(
        id="tc-1", name="tc", elapsed="0.00s", status="NOT_RUN",
        modules=[module_result],
    )
    runner.result_printer.test_state = {"tc": tc_result}
    return tc_result, kw_result


class TestExecuteKeywordNameResolution:
    async def test_unknown_keyword_fails_without_dispatch(self):
        runner = _make_runner(keyword_map={"known": lambda: None})
        kw_node = _kw_node(name="Totally Unknown Keyword", node_id="k1")
        mod_node = _mod_node(name="mod")
        tc_result, kw_result = _wire_execute_context(runner, kw_node)

        ok = await runner._execute_keyword(kw_node, mod_node, tc_result, {})

        assert ok is False
        assert kw_result.status == "FAIL"
        assert kw_node.state == State.COMPLETED_FAILED

    async def test_missing_variable_fails_before_dispatch(self):
        called = []
        runner = _make_runner(keyword_map={"press_element": lambda *a: called.append(a)})
        kw_node = _kw_node(name="Press Element", params=["${missing}"], node_id="k1")
        mod_node = _mod_node(name="mod")
        tc_result, kw_result = _wire_execute_context(runner, kw_node)

        ok = await runner._execute_keyword(kw_node, mod_node, tc_result, {})

        assert ok is False
        assert called == []                 # never dispatched — element unresolved
        assert kw_result.status == "FAIL"
        assert kw_node.state == State.COMPLETED_FAILED


class TestModuleStepResolution:
    @staticmethod
    def _wire_module(runner, module_name, keywords=None):
        module_result = ModuleResult(
            name=module_name, elapsed="0.00s", status="NOT_RUN",
            keywords=keywords or [],
        )
        tc_result = _TestCaseResult(
            id="tc-1", name="tc", elapsed="0.00s", status="NOT_RUN",
            modules=[module_result],
        )
        runner.result_printer.test_state = {"tc": tc_result}
        return tc_result, module_result

    @pytest.mark.parametrize("method,arg", [("_dry_run_module", "tc-node-1"), ("_process_module", {})])
    async def test_unknown_step_fails(self, method, arg):
        runner = _make_runner(keyword_map={"known": lambda: None})
        step = "Bogus Keyword Of Doom hello"
        tc_result, module_result = self._wire_module(runner, step)
        assert await getattr(runner, method)(_mod_node(name=step), tc_result, arg) is False
        assert module_result.status == "FAIL"
        assert module_result.reason == f"'{step}' is not a keyword or a module."

    def test_dry_run_checks_the_keyword_before_its_variables(self):
        runner = _make_runner(keyword_map={"press_element": lambda *a: None})
        with pytest.raises(OpticsError, match="E0402"):
            runner._dry_run_keyword(_kw_node(name="Pres Element", params=["${sign_up}"]), None)


class TestFailuresPanel:
    @staticmethod
    def _rendered(monkeypatch, test_state):
        console = Console(record=True, width=200, file=io.StringIO())
        monkeypatch.setattr(printers, "get_console", lambda: console)
        printer = TreeResultPrinter(TerminalWidthProvider())
        printer.start_live()
        for result in test_state.values():
            printer.print_tree_log(result)
        printer.stop_live()
        return console.export_text()

    def test_shows_where_each_step_failed_with_its_reason_and_hint(self, monkeypatch):
        keyword = KeywordResult(
            id="k1", name="Press Element", resolved_name="Press Element (btn)", elapsed="0.10s", status="FAIL",
            reason="login_button is not on the screen.", hint="Check that the app shows the screen you expect.",
        )
        module = ModuleResult(name="Login", elapsed="0.10s", status="FAIL", keywords=[keyword])
        passing = ModuleResult(name="Launch App", elapsed="0.10s", status="PASS")
        out = self._rendered(monkeypatch, {
            "TC OK": _TestCaseResult(id="t0", name="TC OK", elapsed="0.10s", status="PASS", modules=[passing]),
            "TC Broken": _TestCaseResult(id="t1", name="TC Broken", elapsed="0.20s", status="FAIL", modules=[module]),
        })

        assert "Failures" in out
        assert "TC Broken › Login › Press Element (btn)" in out
        assert "login_button is not on the screen." in out
        assert "Check that the app shows the screen you expect." in out
        assert "TC OK ›" not in out

    def test_a_passing_run_has_no_failures_panel(self, monkeypatch):
        passing = ModuleResult(name="Launch App", elapsed="0.10s", status="PASS")
        out = self._rendered(monkeypatch, {"TC OK": _TestCaseResult(id="t0", name="TC OK", elapsed="0.10s", status="PASS", modules=[passing])})
        assert "Failures" not in out


class TestPopupEvents:
    """A keyword's popup inspections leave ``popup`` events, whether the keyword passes or fails."""

    @staticmethod
    def _keyword_recording(runner, *, then=None):
        def keyword(*_):
            test_context.popup_run.get().events.append({"type": "popup", "verdict": "handled", "rule": "Rate Us"})
            if then is not None:
                raise OpticsError(then, message="a popup stopped the step")
        return keyword

    async def _run(self, runner, params=None):
        kw_node = _kw_node(name="Press Element", params=params or [], node_id="k1")
        tc_result, kw_result = _wire_execute_context(runner, kw_node)
        token = test_context.popup_run.set(runner.popup_run)
        try:
            ok = await runner._execute_keyword(kw_node, _mod_node(name="mod"), tc_result, {})
        finally:
            test_context.popup_run.reset(token)
        return ok, kw_result


    @pytest.mark.parametrize("code", [Code.E0407, Code.E0408])
    async def test_a_popup_that_stops_the_step_fails_it_at_once_and_still_publishes_the_event(self, code):
        elements = ElementData()
        elements.add_element("card", "//first")
        elements.add_element("card", "//second")
        tried = []
        runner = _make_runner(elements=elements)
        recording = self._keyword_recording(runner, then=code)
        runner.keyword_map["press_element"] = lambda element: (tried.append(element), recording())[1]
        ok, kw_result = await self._run(runner, params=["${card}"])
        assert ok is not True
        assert kw_result.status == "FAIL"
        assert tried == ["//first"], "the second locator would meet the same popup"
        failed = [e for e in runner.event_manager.events if e.entity_type == "keyword" and e.status == EventStatus.FAIL]
        assert failed
        assert failed[-1].code == code
        assert runner.event_manager.events[-1].entity_type == "popup"
