import json
from types import SimpleNamespace

import pytest

from optics_framework.api.flow_control import FlowControl
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.expression import MAX_CHARS
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
    def __init__(self, output_dir):
        self.elements = ElementData()
        self.modules = _Modules()
        self.apis = ApiData()
        self.apis.collections = {}
        self.config_handler = SimpleNamespace(
            config=SimpleNamespace(
                execution_output_path=str(output_dir), project_path=None
            )
        )


@pytest.fixture
def flow_control(tmp_path):
    session = _Session(tmp_path)
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


class TestEvaluate:
    @pytest.mark.parametrize("expr", ['__import__("os")', "open('x')", "(1).__class__"])
    def test_rejects_unsafe_expression(self, flow_control, expr):
        with pytest.raises(OpticsError):
            flow_control.evaluate("${result}", expr)


class TestReadData:
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
    def test_jsonpath_assertion_fails(self, flow_control, monkeypatch, fake_response):
        _api(flow_control, monkeypatch, fake_response,
             jsonpath=[{"path": "$.data.value", "condition": '$ == "99"'}],
             resp=fake_response(json_data={"data": {"value": "42"}}))
        with pytest.raises(AssertionError):
            flow_control.invoke_api("testcol.bar")

    def test_non_json_response_raises(self, flow_control, monkeypatch, fake_response):
        _api(flow_control, monkeypatch, fake_response,
             extract={"foo": "foo"},
             resp=fake_response(json_data=None, text="not json", content_type="text/plain"))
        with pytest.raises(OpticsError, match="API response is not valid JSON"):
            flow_control.invoke_api("testcol.bar")


class TestExpressionCondition:
    def test_no_else_returns_none(self, flow_control):
        run = _module_runner({"modA": ["a"]})
        _register(flow_control, run)
        flow_control.session.elements.add_element("x", "no")
        assert flow_control.condition('${x} == "yes"', "modA") is None
        assert run.calls == []

    def test_a_variable_inside_quotes_is_refused(self, flow_control):
        run = _module_runner({"modA": ["ran:modA"], "modElse": ["ran:modElse"]})
        _register(flow_control, run)
        flow_control.session.elements.add_element("x", "foo")
        with pytest.raises(OpticsError) as excinfo:
            flow_control.condition("'${x}' == 'foo'", "modA", "modElse")
        assert excinfo.value.code == Code.E0403
        assert run.calls == []

    @pytest.mark.parametrize("cond", [
        "${count} == 3", "${count} < 10", "${ratio} > 0.5", "${count} in [1, 2, 3]", '${code} == "007"', "${code} != 7",
        '${count} == "3"', '${count} in ["2", "3"]',
    ])
    def test_a_stored_number_compares_as_a_number(self, flow_control, cond):
        run = _module_runner({"modA": ["ran:modA"]})
        _register(flow_control, run)
        flow_control.evaluate("${count}", "1 + 2")
        flow_control.session.elements.add_element("ratio", "0.75")
        flow_control.session.elements.add_element("code", "007")
        assert flow_control.condition(cond, "modA") == ["ran:modA"]

    @pytest.mark.parametrize("terms, reason", [(MAX_CHARS // 2 + 1, "longer than"), (40_000, "nested too deeply")])
    def test_a_huge_condition_is_refused_by_the_evaluator(self, flow_control, terms, reason):
        run = _module_runner({"modA": ["ran:modA"]})
        _register(flow_control, run)
        flow_control.session.elements.add_element("x", "1")
        with pytest.raises(OpticsError) as excinfo:
            flow_control.condition("${x} == " + "1+" * terms + "1", "modA")
        assert excinfo.value.code == Code.E0406
        assert reason in excinfo.value.message
        assert run.calls == []


@pytest.mark.parametrize("cond, els, result", [
    (["c"], ["E"], ["T"]),
    (RuntimeError("boom"), ["E"], ["E"]),
    pytest.param([], ["E"], ["E"], marks=pytest.mark.xfail(reason=_MODULE_CONDITION_BUG, strict=True)),
    pytest.param([], None, None, marks=pytest.mark.xfail(reason=_MODULE_CONDITION_BUG, strict=True)),
], ids=["truthy runs target", "raising runs else", "falsy runs else", "falsy without else"])
def test_a_module_condition_runs_the_target_only_when_the_module_holds(flow_control, cond, els, result):
    modules = {"cond": cond, "target": ["T"], **({"els": els} if els else {})}
    run = _module_runner(modules)
    _register(flow_control, run, *modules)
    assert flow_control.condition(*modules) == result
    assert ("target" in run.calls) == (result == ["T"])


class TestRunLoop:
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
