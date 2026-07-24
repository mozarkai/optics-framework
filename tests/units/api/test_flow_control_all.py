"""Tests for FlowControl keywords: evaluate, read_data, invoke_api, condition,
run_loop, and date_evaluate.

The condition suite is written against the *documented* module-condition contract
(a module condition is true iff the module runs and returns a non-empty result).
The current implementation decides truth by whether the module *raised* instead —
tracked as a deferred behaviour fix — so the cases that expose it are marked
``xfail(strict=True)``: they fail today and will flip to XPASS (failing the run,
prompting removal of the marker) once the fix lands.
"""
import json
from unittest.mock import MagicMock

import pytest

from optics_framework.api.flow_control import FlowControl
from optics_framework.common.error import OpticsError
from optics_framework.common.models import (
    ApiCollection,
    ApiData,
    ApiDefinition,
    ElementData,
    ExpectedResultDefinition,
    RequestDefinition,
)

_MODULE_CONDITION_BUG = (
    "Module-condition truth is decided by exception-presence, not bool(result) "
    "(mozarkai/optics-framework#385). Remove this marker when the fix lands."
)


class _Modules:
    """Minimal stand-in for the session's module registry."""

    def __init__(self):
        self.modules = {}

    def get_module_definition(self, name):
        return self.modules.get(name)


class _Session:
    def __init__(self):
        self.elements = ElementData()
        self.modules = _Modules()
        self.apis = ApiData()
        self.apis.collections = {}
        self.config_handler = MagicMock()


@pytest.fixture
def flow_control():
    session = _Session()
    return FlowControl(session, {
        "add": lambda a, b: int(a) + int(b),
        "concat": lambda a, b: f"{a}{b}",
    })


def _module_runner(behaviors):
    """Build a fake execute_module from {name: result-list | Exception} and record calls."""
    calls = []

    def run(name):
        calls.append(name)
        outcome = behaviors[name]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    run.calls = calls
    return run


def _register(flow_control, run, *module_names):
    flow_control.execute_module = run
    for name in module_names:
        flow_control.session.modules.modules[name] = object()


# --------------------------------------------------------------------------- #
# evaluate                                                                     #
# --------------------------------------------------------------------------- #

class TestEvaluate:
    def test_bare_name_resolves_to_scalar(self, flow_control):
        flow_control.session.elements.add_element("count", "15")
        assert flow_control.evaluate("${result}", "count") == "15"
        assert flow_control.session.elements.get_first("result") == "15"

    def test_arithmetic_expression(self, flow_control):
        flow_control.session.elements.add_element("a", "5")
        assert flow_control.evaluate("${result}", "${a} + 3") == 8
        assert flow_control.session.elements.get_first("result") == "8"

    def test_ignores_element_with_empty_value_list(self, flow_control):
        flow_control.session.elements.elements["empty"] = []
        flow_control.session.elements.add_element("a", "5")
        assert flow_control.evaluate("${result}", "${a}") == 5

    def test_missing_variable_raises(self, flow_control):
        with pytest.raises(OpticsError):
            flow_control.evaluate("${result}", "${nope}")

    @pytest.mark.parametrize("expr", ['__import__("os")', "open('x')", "(1).__class__"])
    def test_rejects_unsafe_expression(self, flow_control, expr):
        with pytest.raises(OpticsError):
            flow_control.evaluate("${result}", expr)


# --------------------------------------------------------------------------- #
# read_data                                                                    #
# --------------------------------------------------------------------------- #

class TestReadData:
    def test_csv_file_with_filter(self, tmp_path, flow_control):
        (tmp_path / "test.csv").write_text("a,b\n1,2\n3,4\n5,6")
        flow_control.session.config_handler.config.project_path = str(tmp_path)
        result = flow_control.read_data("my_elem", "test.csv", "a == '3';select=b")
        assert result == ["4"]
        assert flow_control.session.elements.get_first("my_elem") == "4"

    def test_json_file_with_filter(self, tmp_path, flow_control):
        (tmp_path / "test.json").write_text(
            json.dumps([{"foo": "bar", "num": 1}, {"foo": "baz", "num": 2}])
        )
        flow_control.session.config_handler.config.project_path = str(tmp_path)
        assert flow_control.read_data("e", "test.json", "foo == 'baz';select=num") == ["2"]

    def test_json_single_object(self, tmp_path, flow_control):
        (tmp_path / "s.json").write_text(json.dumps({"serialId": "123", "foo": "bar"}))
        flow_control.session.config_handler.config.project_path = str(tmp_path)
        assert flow_control.read_data("e", "s.json", "select=serialId") == ["123"]

    def test_2d_list(self, flow_control):
        data = [["col1", "col2"], ["a", "b"], ["c", "d"]]
        assert flow_control.read_data("e", data, "col1 == 'c';select=col2") == ["d"]

    def test_variable_resolved_in_query(self, tmp_path, flow_control):
        (tmp_path / "d.csv").write_text("serial,pkg\nAAA,com.a\nBBB,com.b")
        flow_control.session.config_handler.config.project_path = str(tmp_path)
        flow_control.session.elements.add_element("target", "BBB")
        assert flow_control.read_data("e", "d.csv", "serial == '${target}';select=pkg") == ["com.b"]

    @pytest.mark.parametrize(
        "env_value, expected",
        [
            ("simplevalue", ["simplevalue"]),
            ("12345", ["12345"]),
            ("3.14159", ["3.14159"]),
        ],
    )
    def test_env_scalar(self, flow_control, monkeypatch, env_value, expected):
        monkeypatch.setenv("SCALAR", env_value)
        assert flow_control.read_data("e", "ENV:SCALAR") == expected

    def test_env_csv_with_filter(self, flow_control, monkeypatch):
        monkeypatch.setenv("MYCSV", "x,y\n7,8\n9,10")
        assert flow_control.read_data("e", "ENV:MYCSV", "x == '9';select=y") == ["10"]


# --------------------------------------------------------------------------- #
# invoke_api                                                                   #
# --------------------------------------------------------------------------- #

def _api(flow_control, monkeypatch, fake_response, *, extract=None, jsonpath=None, resp):
    api_def = ApiDefinition(
        name="bar",
        endpoint="/foo",
        request=RequestDefinition(method="GET"),
        expected_result=ExpectedResultDefinition(extract=extract, jsonpath_assertions=jsonpath),
    )
    collection = ApiCollection(name="testcol", base_url="http://dummy", apis={"bar": api_def})
    flow_control.session.apis.collections["testcol"] = collection
    monkeypatch.setattr("requests.request", lambda *a, **kw: resp)


class TestInvokeApi:
    def test_extracts_value(self, flow_control, monkeypatch, fake_response):
        _api(flow_control, monkeypatch, fake_response,
             extract={"result": "data.value"},
             resp=fake_response(json_data={"data": {"value": "42"}}))
        flow_control.invoke_api("testcol.bar")
        assert flow_control.session.elements.get_first("result") == "42"

    def test_jsonpath_assertion_passes(self, flow_control, monkeypatch, fake_response):
        _api(flow_control, monkeypatch, fake_response,
             extract={"result": "data.value"},
             jsonpath=[{"path": "$.data.value", "condition": '$ == "42"'}],
             resp=fake_response(json_data={"data": {"value": "42"}}))
        flow_control.invoke_api("testcol.bar")
        assert flow_control.session.elements.get_first("result") == "42"

    def test_jsonpath_assertion_fails(self, flow_control, monkeypatch, fake_response):
        _api(flow_control, monkeypatch, fake_response,
             jsonpath=[{"path": "$.data.value", "condition": '$ == "99"'}],
             resp=fake_response(json_data={"data": {"value": "42"}}))
        with pytest.raises(AssertionError):
            flow_control.invoke_api("testcol.bar")

    def test_no_extract_is_noop(self, flow_control, monkeypatch, fake_response):
        _api(flow_control, monkeypatch, fake_response,
             resp=fake_response(json_data={"foo": "bar"}))
        flow_control.invoke_api("testcol.bar")  # must not raise

    def test_non_json_response_raises(self, flow_control, monkeypatch, fake_response):
        _api(flow_control, monkeypatch, fake_response,
             extract={"foo": "foo"},
             resp=fake_response(json_data=None, text="not json", content_type="text/plain"))
        with pytest.raises(OpticsError, match="API response is not valid JSON"):
            flow_control.invoke_api("testcol.bar")


# --------------------------------------------------------------------------- #
# condition — expression path (fully correct today)                           #
# --------------------------------------------------------------------------- #

class TestExpressionCondition:
    def test_first_true_runs_its_target(self, flow_control):
        run = _module_runner({"modA": ["ran:modA"], "modB": ["ran:modB"], "modElse": ["ran:modElse"]})
        _register(flow_control, run)
        flow_control.session.elements.add_element("x", "yes")
        result = flow_control.condition('${x} == "yes"', "modA", '${x} == "no"', "modB", "modElse")
        assert result == ["ran:modA"]
        assert run.calls == ["modA"]

    def test_else_taken_when_all_false(self, flow_control):
        run = _module_runner({"modA": ["a"], "modElse": ["ran:modElse"]})
        _register(flow_control, run)
        flow_control.session.elements.add_element("x", "no")
        result = flow_control.condition('${x} == "yes"', "modA", "modElse")
        assert result == ["ran:modElse"]
        assert run.calls == ["modElse"]

    def test_no_else_returns_none(self, flow_control):
        run = _module_runner({"modA": ["a"]})
        _register(flow_control, run)
        flow_control.session.elements.add_element("x", "no")
        assert flow_control.condition('${x} == "yes"', "modA") is None
        assert run.calls == []

    def test_invert_prefix_flips_expression(self, flow_control):
        run = _module_runner({"modA": ["ran:modA"]})
        _register(flow_control, run)
        flow_control.session.elements.add_element("x", "no")
        # `!(x == yes)` is true when x != yes, so modA runs.
        assert flow_control.condition('!${x} == "yes"', "modA") == ["ran:modA"]


# --------------------------------------------------------------------------- #
# condition — module path (behaviour partly buggy; see module docstring)       #
# --------------------------------------------------------------------------- #

class TestModuleCondition:
    def test_truthy_module_runs_target(self, flow_control):
        run = _module_runner({"cond": ["c"], "target": ["T"], "els": ["E"]})
        _register(flow_control, run, "cond", "target", "els")
        assert flow_control.condition("cond", "target", "els") == ["T"]
        assert run.calls == ["cond", "target"]

    def test_raising_module_runs_else(self, flow_control):
        run = _module_runner({"cond": RuntimeError("boom"), "target": ["T"], "els": ["E"]})
        _register(flow_control, run, "cond", "target", "els")
        assert flow_control.condition("cond", "target", "els") == ["E"]
        assert "target" not in run.calls

    @pytest.mark.xfail(reason=_MODULE_CONDITION_BUG, strict=True)
    def test_falsy_module_skips_target_runs_else(self, flow_control):
        run = _module_runner({"cond": [], "target": ["T"], "els": ["E"]})
        _register(flow_control, run, "cond", "target", "els")
        assert flow_control.condition("cond", "target", "els") == ["E"]
        assert "target" not in run.calls

    @pytest.mark.xfail(reason=_MODULE_CONDITION_BUG, strict=True)
    def test_falsy_module_no_else_returns_none(self, flow_control):
        run = _module_runner({"cond": [], "target": ["T"]})
        _register(flow_control, run, "cond", "target")
        assert flow_control.condition("cond", "target") is None
        assert "target" not in run.calls


# --------------------------------------------------------------------------- #
# run_loop                                                                     #
# --------------------------------------------------------------------------- #

class TestRunLoop:
    def test_by_count(self, flow_control):
        run = _module_runner({"mod1": ["ran:mod1"]})
        flow_control.execute_module = run
        assert flow_control.run_loop("mod1", "3") == [["ran:mod1"]] * 3
        assert run.calls == ["mod1", "mod1", "mod1"]

    def test_with_variables(self, flow_control):
        seen = []

        def run(target):
            seen.append((
                flow_control.session.elements.get_first("foo"),
                flow_control.session.elements.get_first("bar"),
            ))
            return [f"{seen[-1][0]}-{seen[-1][1]}"]

        flow_control.execute_module = run
        out = flow_control.run_loop("mod2", "${foo}", '["1","2"]', "${bar}", '["3","4"]')
        assert seen == [("1", "3"), ("2", "4")]
        assert out == [["1-3"], ["2-4"]]

    @pytest.mark.parametrize(
        "args",
        [
            ("mod", "${foo}", "[1,2]", "${bar}"),  # dangling variable, no iterable
            ("mod", "${foo}", "notalist"),          # iterable is not a list
        ],
    )
    def test_invalid_args_raise(self, flow_control, args):
        with pytest.raises(OpticsError):
            flow_control.run_loop(*args)


def test_condition_requires_at_least_one_pair(flow_control):
    with pytest.raises(OpticsError):
        flow_control.condition()
    with pytest.raises(OpticsError):
        flow_control.condition("only_one")


# --------------------------------------------------------------------------- #
# date_evaluate                                                                #
# --------------------------------------------------------------------------- #

class TestDateEvaluate:
    @pytest.mark.parametrize(
        "input_date, expression, out_fmt, expected",
        [
            ("2025-08-14", "+1 day", "%d %B", "15 August"),
            ("2025-08-14", "-2 days", "%d %B", "12 August"),
            ("2025-08-14", "today", "%Y-%m-%d", "2025-08-14"),
            ("08/14/2025", "+1 day", "%Y-%m-%d", "2025-08-15"),
        ],
    )
    def test_valid_expressions(self, flow_control, input_date, expression, out_fmt, expected):
        result = flow_control.date_evaluate("${d}", input_date, expression, out_fmt)
        assert result == expected
        assert flow_control.session.elements.get_first("d") == expected

    @pytest.mark.parametrize(
        "input_date, expression",
        [
            ("not-a-date", "+1 day"),   # undetectable input format
            ("2025-08-14", "+1 week"),  # unsupported unit
            ("2025-08-14", "sideways"),  # unsupported expression
        ],
    )
    def test_invalid_inputs_raise(self, flow_control, input_date, expression):
        with pytest.raises(OpticsError):
            flow_control.date_evaluate("${d}", input_date, expression)
