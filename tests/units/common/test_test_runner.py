"""Unit tests for the CSV/YAML execution core.

Source under test: ``optics_framework/common/runner/test_runnner.py`` — specifically
``TestRunner`` and its param-axis fallback ladder (documented in ``CLAUDE.md`` as
"fallback level 1"):

- ``TestRunner.resolve_param`` (returns the *first* value of a ``${var}`` list) vs
  ``TestRunner._build_param_candidates`` (returns the whole *list*) — the documented
  dry-run-vs-execute divergence. ``TestDivergence`` pins *both* so the divergence is
  explicit.
- ``TestRunner._try_execute_with_fallback`` — the Cartesian expansion of ``${var}``
  value lists, the ``MAX_ATTEMPTS = 20`` cap, and the rule that the ladder only
  advances on ``OpticsError`` codes in the element-not-found family / ``X0201``.
- ``TestRunner._execute_keyword`` name resolution
  (``func_name = "_".join(name.split()).lower()``) and the keyword-not-found path.

These exercise the *synchronous* fallback logic through the runner's own ``async``
methods; a full ``ExecutionEngine`` is deliberately not constructed. The runner is
built via ``__new__`` and wired with the minimal collaborators (real ``ElementData``,
a real ``NullResultPrinter``, and a tiny fake event manager) so the assertions stay
behaviour-focused rather than call-order-focused.
"""
import logging
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.events import EventStatus
from optics_framework.common.logging_config import LogCaptureBuffer
from optics_framework.common.models import (
    ElementData,
    KeywordNode,
    ModuleData,
    ModuleNode,
    State,
)
# Aliased on import: pytest would otherwise try to collect the ``Test``-prefixed
# class and emit PytestCollectionWarning.
from optics_framework.common.models import TestCaseNode as _TestCaseNode
from optics_framework.common.runner.printers import (
    NullResultPrinter,
    ModuleResult,
    KeywordResult,
    TerminalWidthProvider,
    TreeResultPrinter,
)
# Aliased on import: pytest would otherwise try to collect these ``Test``-prefixed
# classes and emit PytestCollectionWarning.
from optics_framework.common.runner.printers import TestCaseResult as _TestCaseResult
from optics_framework.common.runner import test_runnner as _test_runnner
from optics_framework.common.runner.test_runnner import TestRunner as _TestRunner


# --------------------------------------------------------------------------- #
# Fakes / builders                                                            #
# --------------------------------------------------------------------------- #

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
    """A callable that records every (args, kwargs) it is invoked with.

    ``fail_until`` — if the positional args don't equal this tuple, raise
    ``raise_code``; otherwise succeed. ``always_raise`` overrides and raises on
    every call.
    """

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


# --------------------------------------------------------------------------- #
# resolve_param — the "first value" resolver (dry-run form)                    #
# --------------------------------------------------------------------------- #

class TestResolveParam:
    def test_passthrough_for_non_variable(self):
        runner = _make_runner()
        assert runner.resolve_param("literal") == "literal"
        assert runner.resolve_param("com.example.app") == "com.example.app"

    def test_returns_first_of_fallback_list(self):
        elements = ElementData()
        elements.add_element("btn", "xpath//one")
        elements.add_element("btn", "xpath//two")
        runner = _make_runner(elements=elements)
        assert runner.resolve_param("${btn}") == "xpath//one"

    def test_strips_whitespace_inside_braces(self):
        elements = ElementData()
        elements.add_element("btn", "V")
        runner = _make_runner(elements=elements)
        assert runner.resolve_param("${ btn }") == "V"

    def test_missing_variable_raises_e0201(self):
        runner = _make_runner()
        with pytest.raises(OpticsError) as exc:
            runner.resolve_param("${nope}")
        assert exc.value.code == Code.E0201


# --------------------------------------------------------------------------- #
# _build_param_candidates — the "whole list" resolver (execute form)          #
# --------------------------------------------------------------------------- #

class TestBuildParamCandidates:
    async def test_variable_expands_to_full_list(self):
        elements = ElementData()
        elements.add_element("btn", "one")
        elements.add_element("btn", "two")
        runner = _make_runner(elements=elements)
        result = await runner._build_param_candidates(
            _kw_node(params=["${btn}"]), ["${btn}"], _mod_node(),
            _kw_result(), time.time(), _tc_result(), LogCaptureBuffer(),
        )
        assert result == [["one", "two"]]

    async def test_literal_param_wrapped_as_single_element_list(self):
        runner = _make_runner()
        result = await runner._build_param_candidates(
            _kw_node(params=["hello"]), ["hello"], _mod_node(),
            _kw_result(), time.time(), _tc_result(), LogCaptureBuffer(),
        )
        assert result == [["hello"]]

    async def test_mixed_params_preserve_order(self):
        elements = ElementData()
        elements.add_element("x", "x1")
        elements.add_element("x", "x2")
        runner = _make_runner(elements=elements)
        result = await runner._build_param_candidates(
            _kw_node(), ["lit", "${x}"], _mod_node(),
            _kw_result(), time.time(), _tc_result(), LogCaptureBuffer(),
        )
        assert result == [["lit"], ["x1", "x2"]]

    async def test_missing_variable_returns_none_and_marks_fail(self):
        runner = _make_runner()
        kw_result = _kw_result()
        kw_node = _kw_node(params=["${gone}"])
        result = await runner._build_param_candidates(
            kw_node, ["${gone}"], _mod_node(),
            kw_result, time.time(), _tc_result(), LogCaptureBuffer(),
        )
        assert result is None
        assert kw_result.status == "FAIL"
        assert kw_node.state == State.COMPLETED_FAILED


# --------------------------------------------------------------------------- #
# The documented divergence — pin BOTH resolvers on the same data             #
# --------------------------------------------------------------------------- #

class TestDivergence:
    """``resolve_param`` (dry-run) returns the first value; ``_build_param_candidates``
    (execute) returns the whole list. This is the documented dry-run-vs-execute
    divergence — pin both so any future convergence is a deliberate, visible change.
    """

    async def test_first_vs_list_on_identical_element(self):
        elements = ElementData()
        elements.add_element("target", "primary")
        elements.add_element("target", "secondary")
        runner = _make_runner(elements=elements)

        # dry-run form: first only
        assert runner.resolve_param("${target}") == "primary"

        # execute form: the whole ordered fallback list
        candidates = await runner._build_param_candidates(
            _kw_node(), ["${target}"], _mod_node(),
            _kw_result(), time.time(), _tc_result(), LogCaptureBuffer(),
        )
        assert candidates == [["primary", "secondary"]]


# --------------------------------------------------------------------------- #
# _try_execute_with_fallback — Cartesian expansion + success                   #
# --------------------------------------------------------------------------- #

class TestFallbackSuccess:
    async def test_single_candidate_success_returns_true(self):
        runner = _make_runner()
        method = _Recorder()
        assert await _run_fallback(runner, method, [["only"]]) is True
        assert method.calls == [(("only",), {})]

    async def test_advances_to_later_candidate_on_x0201(self):
        runner = _make_runner()
        # succeeds only when called with the second value
        method = _Recorder(fail_until=("good",), raise_code=Code.X0201)
        result = await _run_fallback(runner, method, [["bad", "good"]])
        assert result is True
        assert [c[0] for c in method.calls] == [("bad",), ("good",)]

    async def test_cartesian_product_order_across_two_params(self):
        runner = _make_runner()
        # winning combo is the last product() tuple
        method = _Recorder(fail_until=("a2", "b2"), raise_code=Code.X0201)
        result = await _run_fallback(runner, method, [["a1", "a2"], ["b1", "b2"]])
        assert result is True
        assert [c[0] for c in method.calls] == [
            ("a1", "b1"), ("a1", "b2"), ("a2", "b1"), ("a2", "b2"),
        ]

    async def test_keyword_params_are_resolved_and_split(self):
        elements = ElementData()
        elements.add_element("n", "3")
        runner = _make_runner(elements=elements)
        calls = []

        def method(element, repeat=None):  # a realistic keyword signature
            calls.append(((element,), {"repeat": repeat}))

        # "L1" is positional; "repeat=${n}" is a real kwarg (method HAS `repeat`)
        # so it resolves to repeat="3"; a `text=`-style locator would stay positional.
        result = await _run_fallback(runner, method, [["L1"], ["repeat=${n}"]])
        assert result is True
        assert calls == [(("L1",), {"repeat": "3"})]


# --------------------------------------------------------------------------- #
# _try_execute_with_fallback — MAX_ATTEMPTS cap                                #
# --------------------------------------------------------------------------- #

class TestMaxAttempts:
    async def test_caps_at_20_attempts_then_returns_none(self):
        runner = _make_runner()
        method = _Recorder(fail_until=("never",), raise_code=Code.X0201)
        # 25 candidate values, all raise X0201 -> loop breaks after MAX_ATTEMPTS
        candidates = [[f"v{i}" for i in range(25)]]
        result = await _run_fallback(runner, method, candidates)
        assert result is None
        assert len(method.calls) == 20

    async def test_exhausting_fewer_than_cap_returns_none(self):
        runner = _make_runner()
        method = _Recorder(fail_until=("never",), raise_code=Code.X0201)
        result = await _run_fallback(runner, method, [["a", "b", "c"]])
        assert result is None
        assert len(method.calls) == 3


# --------------------------------------------------------------------------- #
# _try_execute_with_fallback — advance rule (X0201 advances, others fatal)     #
# --------------------------------------------------------------------------- #

class TestFallbackAdvanceRule:
    async def test_x0201_advances_the_ladder(self):
        runner = _make_runner()
        method = _Recorder(fail_until=("win",), raise_code=Code.X0201)
        assert await _run_fallback(runner, method, [["lose", "win"]]) is True
        assert len(method.calls) == 2

    @pytest.mark.parametrize(
        "code", [Code.E0801, Code.E0501, Code.E0402, Code.E0105]
    )
    async def test_non_element_error_is_fatal_and_stops_immediately(self, code):
        runner = _make_runner()
        method = _Recorder(always_raise=OpticsError(code, "boom"))
        kw_node = _kw_node()
        result = await runner._try_execute_with_fallback(
            method, [["a", "b", "c"]], kw_node, _mod_node(),
            _kw_result(), time.time(), _tc_result(), LogCaptureBuffer(),
        )
        assert result is False
        # fatal on first candidate — never tries the rest
        assert len(method.calls) == 1
        assert kw_node.state == State.COMPLETED_FAILED

    async def test_generic_exception_is_fatal(self):
        runner = _make_runner()
        method = _Recorder(always_raise=ValueError("kaboom"))
        kw_node = _kw_node()
        result = await runner._try_execute_with_fallback(
            method, [["a", "b"]], kw_node, _mod_node(),
            _kw_result(), time.time(), _tc_result(), LogCaptureBuffer(),
        )
        assert result is False
        assert len(method.calls) == 1
        assert kw_node.state == State.COMPLETED_FAILED

    async def test_e0201_advances_the_ladder(self):
        """An E0201 (element-not-found family) advances to the next candidate,
        per CLAUDE.md 'fallback level 1' (mozarkai/optics-framework#386).
        """
        runner = _make_runner()
        method = _Recorder(fail_until=("good",), raise_code=Code.E0201)
        result = await _run_fallback(runner, method, [["bad", "good"]])
        assert result is True
        assert [c[0] for c in method.calls] == [("bad",), ("good",)]


# --------------------------------------------------------------------------- #
# _execute_keyword — name resolution + keyword-not-found (E0402-shaped) path   #
# --------------------------------------------------------------------------- #

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
    @pytest.mark.parametrize(
        "display_name, func_name",
        [
            ("Press Element", "press_element"),
            ("PRESS ELEMENT", "press_element"),
            ("Press   Element", "press_element"),   # collapses runs of whitespace
            ("Launch App", "launch_app"),
        ],
    )
    async def test_display_name_normalises_and_dispatches(self, display_name, func_name):
        called = []
        runner = _make_runner(keyword_map={func_name: lambda: called.append(True)})
        kw_node = _kw_node(name=display_name, node_id="k1")
        mod_node = _mod_node(name="mod")
        tc_result, kw_result = _wire_execute_context(runner, kw_node)

        ok = await runner._execute_keyword(kw_node, mod_node, tc_result, {})

        assert ok is True
        assert called == [True]
        assert kw_result.status == "PASS"
        assert kw_node.state == State.COMPLETED_PASSED

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


# --------------------------------------------------------------------------- #
# Module-step resolution — a test-case step must name a known module or a      #
# registered keyword, in dry-run AND batch mode                                #
# --------------------------------------------------------------------------- #

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

    def test_step_resolves_via_module_definition_or_keyword(self):
        modules = ModuleData()
        modules.add_module_definition("Use Var", [("Validate Element", [])])
        runner = _make_runner(keyword_map={"launch_app": lambda: None}, modules=modules)
        assert runner._step_resolves("Use Var") is True
        assert runner._step_resolves("Launch App") is True
        assert runner._step_resolves("Bogus Keyword Of Doom hello") is False

    async def test_unknown_step_fails_dry_run(self):
        runner = _make_runner()
        step = "Bogus Keyword Of Doom hello"
        tc_result, module_result = self._wire_module(runner, step)
        ok = await runner._dry_run_module(_mod_node(name=step), tc_result, "tc-node-1")
        assert ok is False
        assert module_result.status == "FAIL"

    async def test_unknown_step_fails_batch(self):
        runner = _make_runner(keyword_map={"known": lambda: None})
        step = "Bogus Keyword Of Doom hello"
        tc_result, module_result = self._wire_module(runner, step)
        ok = await runner._process_module(_mod_node(name=step), tc_result, {})
        assert ok is False
        assert module_result.status == "FAIL"

    async def test_unknown_step_fail_event_carries_reason(self):
        runner = _make_runner()
        step = "Bogus Keyword Of Doom hello"
        tc_result, _ = self._wire_module(runner, step)
        await runner._dry_run_module(_mod_node(name=step), tc_result, "tc-node-1")
        fails = [e for e in runner.event_manager.events if e.status == EventStatus.FAIL]
        assert any(
            e.entity_type == "module"
            and e.message == f"Unknown keyword or module: '{step}'"
            for e in fails
        )

    async def test_known_module_still_passes_dry_run(self):
        modules = ModuleData()
        modules.add_module_definition("Launch App", [("Launch App", [])])
        runner = _make_runner(keyword_map={"launch_app": lambda: None}, modules=modules)
        keyword_node = _kw_node(name="Launch App")
        kw_result = _kw_result(node_id=keyword_node.id, name=keyword_node.name)
        tc_result, module_result = self._wire_module(
            runner, "Launch App", keywords=[kw_result]
        )
        module_node = _mod_node(name="Launch App")
        module_node.add_keyword(keyword_node)
        ok = await runner._dry_run_module(module_node, tc_result, "tc-node-1")
        assert ok is True
        assert module_result.status == "PASS"


# --------------------------------------------------------------------------- #
# Dry-run param resolution — an unresolved ``${var}`` is a per-keyword FAIL,   #
# not an aborted run                                                           #
# --------------------------------------------------------------------------- #

class TestDryRunUnresolvedParam:
    async def test_missing_variable_marks_fail_instead_of_raising(self):
        modules = ModuleData()
        modules.add_module_definition("Use Var", [("Validate Element", ["${NoSuchVar}"])])
        runner = _make_runner(modules=modules)
        kw_result = _kw_result(node_id="kw-1", name="Validate Element")
        module_result = ModuleResult(
            name="Use Var", elapsed="0.00s", status="NOT_RUN", keywords=[kw_result],
        )
        tc_result = _TestCaseResult(
            id="tc-1", name="tc", elapsed="0.00s", status="NOT_RUN",
            modules=[module_result],
        )
        runner.result_printer.test_state = {"tc": tc_result}
        module_node = _mod_node(name="Use Var")
        module_node.add_keyword(_kw_node(name="Validate Element", params=["${NoSuchVar}"]))

        ok = await runner._dry_run_module(module_node, tc_result, "tc-node-1")

        assert ok is False
        assert kw_result.status == "FAIL"
        assert module_result.status == "FAIL"

    async def test_missing_variable_fail_event_reports_element(self):
        modules = ModuleData()
        modules.add_module_definition("Use Var", [("Validate Element", ["${NoSuchVar}"])])
        runner = _make_runner(modules=modules)
        kw_result = _kw_result(node_id="kw-1", name="Validate Element")
        module_result = ModuleResult(
            name="Use Var", elapsed="0.00s", status="NOT_RUN", keywords=[kw_result],
        )
        tc_result = _TestCaseResult(
            id="tc-1", name="tc", elapsed="0.00s", status="NOT_RUN",
            modules=[module_result],
        )
        runner.result_printer.test_state = {"tc": tc_result}
        module_node = _mod_node(name="Use Var")
        module_node.add_keyword(_kw_node(name="Validate Element", params=["${NoSuchVar}"]))

        await runner._dry_run_module(module_node, tc_result, "tc-node-1")

        fails = [e for e in runner.event_manager.events if e.status == EventStatus.FAIL]
        assert any(
            e.entity_type == "keyword" and e.message == "Element not found: NoSuchVar"
            for e in fails
        )

    async def test_unknown_keyword_reason_includes_did_you_mean(self):
        modules = ModuleData()
        modules.add_module_definition("Use KW", [("Launhc App", [])])  # typo
        runner = _make_runner(
            keyword_map={"launch_app": lambda: None}, modules=modules)
        kw_result = _kw_result(node_id="kw-1", name="Launhc App")
        module_result = ModuleResult(
            name="Use KW", elapsed="0.00s", status="NOT_RUN", keywords=[kw_result],
        )
        tc_result = _TestCaseResult(
            id="tc-1", name="tc", elapsed="0.00s", status="NOT_RUN",
            modules=[module_result],
        )
        runner.result_printer.test_state = {"tc": tc_result}
        module_node = _mod_node(name="Use KW")
        module_node.add_keyword(_kw_node(name="Launhc App"))

        await runner._dry_run_module(module_node, tc_result, "tc-node-1")

        fails = [e for e in runner.event_manager.events
                 if e.status == EventStatus.FAIL and e.entity_type == "keyword"]
        assert fails
        assert "Keyword not found: Launhc App" in fails[0].message
        assert "Did you mean 'Launch App'" in fails[0].message
        assert runner._last_failure_reason == fails[0].message

    def test_init_state_keeps_unresolved_param_visible(self):
        """``_initialize_test_state`` must not raise on unresolved ``${var}``;
        the raw reference stays visible in the resolved display name."""
        modules = ModuleData()
        modules.add_module_definition("Use Var", [("Validate Element", ["${NoSuchVar}"])])
        runner = _make_runner(modules=modules)
        module_node = _mod_node(name="Use Var")
        module_node.add_keyword(_kw_node(name="Validate Element", params=["${NoSuchVar}"]))
        tc_root = _TestCaseNode(name="tc")
        tc_root.add_module(module_node)
        runner.test_cases = tc_root
        runner._initialize_test_state()
        (tc_result,) = runner.result_printer.test_state.values()
        assert tc_result.modules[0].keywords[0].resolved_name == (
            "Validate Element (${NoSuchVar})"
        )


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #

@pytest.fixture
def internal_records():
    """Capture ``optics.internal`` records despite ``propagate = False``."""
    from optics_framework.common.logging_config import internal_logger

    captured = []

    class _ListHandler(logging.Handler):
        def emit(self, record):
            captured.append(record)

    handler = _ListHandler(level=logging.DEBUG)
    internal_logger.addHandler(handler)
    previous_level = internal_logger.level
    internal_logger.setLevel(logging.DEBUG)
    yield captured
    internal_logger.setLevel(previous_level)
    internal_logger.removeHandler(handler)


def _runner_with_session(session):
    runner = _make_runner()
    runner.session = session
    return runner


class TestEndOfRunDriverProbe:
    @pytest.mark.parametrize(
        "driver, expected",
        [
            (None, False),                                    # no driver at all
            (SimpleNamespace(page=None), False),              # Playwright, terminated
            (SimpleNamespace(page=object()), True),           # Playwright, live
            (SimpleNamespace(driver=None), False),            # Appium/Selenium, terminated
            (SimpleNamespace(driver=object()), True),         # Appium/Selenium, live
            (SimpleNamespace(page=None, driver=object()), False),
            (SimpleNamespace(), True),                        # unknown backend: fail open
        ],
    )
    def test_probe_reflects_driver_handle(self, driver, expected):
        runner = _runner_with_session(SimpleNamespace(driver=driver))
        assert runner._end_of_run_driver_open() is expected

    @pytest.mark.parametrize(
        "handle_value, expected",
        [(None, False), (object(), True)],
    )
    def test_probe_reads_handle_through_fallback_wrapper(self, handle_value, expected):
        """``session.driver`` is an ``InstanceFallback``; its ``__getattr__``
        hides real attribute values behind generated callables, so liveness
        must be read off ``active_instance`` (QA finding 1)."""
        driver = InstanceFallback([SimpleNamespace(page=handle_value)])
        runner = _runner_with_session(SimpleNamespace(driver=driver))
        assert runner._end_of_run_driver_open() is expected


class TestEndOfRunArtifacts:
    def test_torn_down_session_skips_capture_silently(
        self, monkeypatch, internal_records
    ):
        """The shipped playwright template's teardown closes the page before
        capture runs — the runner must skip with no console-facing WARNING or
        traceback panel at all, only an internal debug log (mozark QA issue 1)."""
        runner = _runner_with_session(
            SimpleNamespace(
                driver=InstanceFallback([SimpleNamespace(page=None)]),
                config=SimpleNamespace(execution_output_path="/tmp/out"),
                optics=MagicMock(),
            )
        )
        strategy_manager_calls = []
        monkeypatch.setattr(
            _test_runnner, "StrategyManager",
            lambda *a, **k: strategy_manager_calls.append(a),
        )

        runner._capture_end_of_run_artifacts()

        assert strategy_manager_calls == []      # engine layer never invoked
        warnings_ = [r for r in internal_records if r.levelno >= logging.WARNING]
        assert warnings_ == []
        debugs = [r for r in internal_records if r.levelno == logging.DEBUG]
        assert any("skipped" in r.getMessage() for r in debugs)

    def test_open_session_captures_screenshot_pagesource_and_detection(self, monkeypatch):
        runner = _runner_with_session(
            SimpleNamespace(
                driver=SimpleNamespace(page=object()),
                config=SimpleNamespace(execution_output_path="/tmp/out"),
                optics=MagicMock(),
                error_definitions=None,
            )
        )
        calls = []
        monkeypatch.setattr(_test_runnner, "StrategyManager", MagicMock())
        monkeypatch.setattr(
            _TestRunner, "_save_end_of_run_screenshot",
            lambda self, sm, out: calls.append("screenshot"),
        )
        monkeypatch.setattr(
            _TestRunner, "_save_end_of_run_pagesource",
            lambda self, sm, out: (calls.append("pagesource"), ("<html></html>", "ts"))[1],
        )
        monkeypatch.setattr(
            _TestRunner, "_run_end_of_run_detection",
            lambda self, src, ts, out: calls.append("detection"),
        )

        runner._capture_end_of_run_artifacts()

        assert calls == ["screenshot", "pagesource", "detection"]

    def test_capture_failure_while_alive_stays_a_single_line(
        self, monkeypatch, internal_records
    ):
        runner = _runner_with_session(
            SimpleNamespace(
                driver=SimpleNamespace(page=object()),
                config=SimpleNamespace(execution_output_path="/tmp/out"),
                optics=MagicMock(),
            )
        )
        monkeypatch.setattr(
            _test_runnner, "StrategyManager",
            MagicMock(side_effect=RuntimeError("boom")),
        )

        runner._capture_end_of_run_artifacts()

        warnings_ = [r for r in internal_records if r.levelno == logging.WARNING]
        assert len(warnings_) == 1
        assert warnings_[0].getMessage() == "End-of-run artifact capture failed: boom"
        assert warnings_[0].exc_info is None



class TestFailureReasonRecording:
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

    async def test_fatal_keyword_exception_records_reason_on_result(self):
        runner = _make_runner()
        method = _Recorder(always_raise=ValueError("kaboom"))
        kw_result = _kw_result()
        await runner._try_execute_with_fallback(
            method, [["a"]], _kw_node(), _mod_node(),
            kw_result, time.time(), _tc_result(), LogCaptureBuffer(),
        )
        assert kw_result.status == "FAIL"
        assert kw_result.reason == "Keyword 'Some Keyword' failed: kaboom"

    async def test_unknown_module_records_reason_in_batch_mode(self):
        runner = _make_runner(keyword_map={"known": lambda: None})
        step = "Bogus Keyword Of Doom hello"
        tc_result, module_result = self._wire_module(runner, step)
        await runner._process_module(_mod_node(name=step), tc_result, {})
        assert module_result.status == "FAIL"
        assert module_result.reason == f"Unknown keyword or module: '{step}'"

    async def test_unknown_module_records_reason_in_dry_run(self):
        runner = _make_runner()
        step = "Bogus Keyword Of Doom hello"
        tc_result, module_result = self._wire_module(runner, step)
        await runner._dry_run_module(_mod_node(name=step), tc_result, "tc-node-1")
        assert module_result.status == "FAIL"
        assert module_result.reason == f"Unknown keyword or module: '{step}'"

    async def test_dry_run_keyword_failure_records_reason_on_result(self):
        modules = ModuleData()
        modules.add_module_definition("Use KW", [("Launhc App", [])])
        runner = _make_runner(keyword_map={"launch_app": lambda: None}, modules=modules)
        kw_result = _kw_result(node_id="kw-1", name="Launhc App")
        tc_result, module_result = self._wire_module(
            runner, "Use KW", keywords=[kw_result]
        )
        module_node = _mod_node(name="Use KW")
        module_node.add_keyword(_kw_node(name="Launhc App"))
        await runner._dry_run_module(module_node, tc_result, "tc-node-1")
        assert kw_result.status == "FAIL"
        assert "Keyword not found: Launhc App" in kw_result.reason


class TestUnknownModuleSuggestion:
    """QA finding 3: unknown steps get a single difflib-based hint."""

    async def test_batch_reason_appends_closest_keyword_match(self):
        runner = _make_runner(keyword_map={"launch_app": lambda: None})
        tc_result, module_result = TestFailureReasonRecording._wire_module(
            runner, "Launch Ap"
        )
        await runner._process_module(_mod_node(name="Launch Ap"), tc_result, {})
        assert module_result.reason == (
            "Unknown keyword or module: 'Launch Ap'."
            " Did you mean 'Launch App'? (run `optics list` to see all keywords)"
        )

    async def test_no_match_appends_nothing(self):
        runner = _make_runner(keyword_map={"completely_different": lambda: None})
        step = "Bogus Keyword Of Doom hello"
        tc_result, module_result = TestFailureReasonRecording._wire_module(runner, step)
        await runner._process_module(_mod_node(name=step), tc_result, {})
        assert module_result.reason == f"Unknown keyword or module: '{step}'"


class TestFailureDetailsSection:
    @staticmethod
    def _printer():
        return TreeResultPrinter(TerminalWidthProvider())

    @staticmethod
    def _failed_state():
        keyword = KeywordResult(
            id="k1", name="Press Element", resolved_name="Press Element (btn)",
            elapsed="0.10s", status="FAIL",
            reason="Keyword 'Press Element' failed: boom",
        )
        module = ModuleResult(
            name="Open Example Pag", elapsed="0.10s", status="FAIL",
            reason="Unknown keyword or module: 'Open Example Pag'",
        )
        test_case = _TestCaseResult(
            id="t1", name="TC Broken", elapsed="0.20s", status="FAIL",
            modules=[module],
        )
        return keyword, test_case

    def test_lists_failed_modules_and_keywords_with_reasons(self):
        printer = self._printer()
        keyword, test_case = self._failed_state()
        test_case.modules[0].keywords = [keyword]
        printer.test_state = {"TC Broken": test_case}
        assert printer._failure_detail_lines() == [
            "TC Broken → step 'Open Example Pag'"
            " — Unknown keyword or module: 'Open Example Pag'",
            "TC Broken → step 'Press Element (btn)'"
            " — Keyword 'Press Element' failed: boom",
        ]

    def test_not_run_steps_without_reason_are_not_listed(self):
        printer = self._printer()
        _, test_case = self._failed_state()
        not_run = KeywordResult(
            id="k2", name="Close Browser", resolved_name="Close Browser",
            elapsed="0.00s", status="NOT_RUN", reason="",
        )
        test_case.modules[0].keywords = [not_run]
        printer.test_state = {"TC Broken": test_case}
        assert printer._failure_detail_lines() == [
            "TC Broken → step 'Open Example Pag'"
            " — Unknown keyword or module: 'Open Example Pag'",
        ]

    def test_panel_only_rendered_when_failures_exist(self):
        passing = ModuleResult(
            name="Launch App", elapsed="0.10s", status="PASS",
            keywords=[KeywordResult(
                id="k1", name="Launch App", resolved_name="Launch App",
                elapsed="0.10s", status="PASS", reason="",
            )],
        )
        passed_tc = _TestCaseResult(
            id="t1", name="TC OK", elapsed="0.10s", status="PASS",
            modules=[passing],
        )

        clean_printer = self._printer()
        clean_printer.test_state = {"TC OK": passed_tc}
        assert all(
            getattr(r, "title", None) != "Failure details"
            for r in clean_printer._render_tree().renderables
        )
        assert clean_printer._failure_detail_lines() == []

        keyword, failed_tc = self._failed_state()
        failed_tc.modules[0].keywords = [keyword]
        failing_printer = self._printer()
        failing_printer.test_state = {"TC Broken": failed_tc}
        group = failing_printer._render_tree()
        titles = [getattr(r, "title", None) for r in group.renderables]
        assert "Failure details" in titles


# --------------------------------------------------------------------------- #
# Signature-aware param split — a 'text='/'css=' locator is the positional      #
# element, never a keyword argument (regression: playwright sample TypeError)   #
# --------------------------------------------------------------------------- #

class TestSignatureAwareParamSplit:
    @staticmethod
    def _validate_like(element, timeout="10", rule="all", event_name=None):
        pass

    def test_equals_locator_dispatched_positionally(self):
        runner = _make_runner()
        pos, kw = runner._resolve_candidate_params(
            self._validate_like, ("text=Example Domain",))
        assert pos == ["text=Example Domain"]
        assert kw == {}

    def test_genuine_keyword_arg_still_routed(self):
        runner = _make_runner()
        pos, kw = runner._resolve_candidate_params(
            self._validate_like, ("text=OK", "event_name=tap"))
        assert pos == ["text=OK"]
        assert kw == {"event_name": "tap"}
