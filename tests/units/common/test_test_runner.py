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
import time
from types import SimpleNamespace

import pytest

from optics_framework.common.error import OpticsError, Code
from optics_framework.common.logging_config import LogCaptureBuffer
from optics_framework.common.models import (
    ElementData,
    KeywordNode,
    ModuleNode,
    State,
)
from optics_framework.common.runner.printers import (
    NullResultPrinter,
    ModuleResult,
    KeywordResult,
)
# Aliased on import: pytest would otherwise try to collect these ``Test``-prefixed
# classes and emit PytestCollectionWarning.
from optics_framework.common.runner.printers import TestCaseResult as _TestCaseResult
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


def _make_runner(elements=None, keyword_map=None):
    """A ``TestRunner`` with just the collaborators the fallback path touches."""
    runner = _TestRunner.__new__(_TestRunner)
    runner.elements = elements if elements is not None else ElementData()
    runner.keyword_map = keyword_map or {}
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
        method = _Recorder()
        # "L1" is positional; "repeat=${n}" becomes kwarg repeat=3 after resolution
        result = await _run_fallback(runner, method, [["L1"], ["repeat=${n}"]])
        assert result is True
        assert method.calls == [(("L1",), {"repeat": "3"})]


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
