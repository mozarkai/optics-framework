import json
import pytest
from optics_framework.api.flow_control import FlowControl
from optics_framework.common.models import ElementData, ApiData
from unittest.mock import MagicMock

# ---- Dummy Classes for All Test Types ----
class DummyApiDef:
    def __init__(self, endpoint, method='GET', headers=None, body=None, extract=None, jsonpath_assertions=None):
        self.endpoint = endpoint
        self.request = type('Req', (), {'method': method, 'headers': headers or {}, 'body': body})()
        self.expected_result = type('Exp', (), {'extract': extract or {}, 'jsonpath_assertions': jsonpath_assertions or []})()

class DummyApiCollection:
    def __init__(self, name, base_url, apis, global_headers=None):
        self.name = name
        self.base_url = base_url
        self.apis = apis
        self.global_headers = global_headers or {}

class DummySession:
    def __init__(self):
        self.elements = ElementData()
        self.modules = type('M', (), {'modules': {}, 'get_module_definition': lambda self, x: []})()
        self.apis = ApiData()
        self.apis.collections = {}

        self.config_handler = MagicMock() # Add config_handler attribute

# ---- Fixtures ----
@pytest.fixture
def flow_control():
    session = DummySession()
    return FlowControl(session, {
        'dummy_keyword': lambda x=None: f"called:{x}",
        'add': lambda a, b: int(a) + int(b),
        'concat': lambda a, b: f"{a}{b}",
    })

# ---- read_data Tests ----
def test_read_data_csv_relative(tmp_path, flow_control, monkeypatch):
    csv_content = 'a,b\n1,2\n3,4\n5,6'
    project_path = tmp_path
    csv_file = tmp_path / 'test.csv'
    csv_file.write_text(csv_content)
    flow_control.session.config_handler.config.project_path = str(project_path)
    result = flow_control.read_data('my_elem', 'test.csv', "a == '3';select=b")
    assert result == ['4']
    assert flow_control.session.elements.get_element('my_elem') == '4'

def test_read_data_json_relative(tmp_path, flow_control, monkeypatch):
    json_content = json.dumps([
        {"foo": "bar", "num": 1},
        {"foo": "baz", "num": 2}
    ])
    project_path = tmp_path
    json_file = tmp_path / 'test.json'
    json_file.write_text(json_content)
    flow_control.session.config_handler.config.project_path = str(project_path)
    result = flow_control.read_data('my_elem', 'test.json', "foo == 'baz';select=num")
    assert result == ['2']
    assert flow_control.session.elements.get_element('my_elem') == '2'

def test_read_data_env_csv(flow_control, monkeypatch):
    csv_content = 'x,y\n7,8\n9,10'
    monkeypatch.setenv('MYCSV', csv_content)
    result = flow_control.read_data('my_elem', 'ENV:MYCSV', "x == '9';select=y")
    assert result == ['10']
    assert flow_control.session.elements.get_element('my_elem') == '10'

def test_read_data_env_json(flow_control, monkeypatch):
    json_content = json.dumps([
        {"alpha": "beta", "val": 42},
        {"alpha": "gamma", "val": 99}
    ])
    monkeypatch.setenv('MYJSON', json_content)
    result = flow_control.read_data('my_elem', 'ENV:MYJSON', "alpha == 'gamma';select=val")
    assert result == ['99']
    assert flow_control.session.elements.get_element('my_elem') == '99'

def test_read_data_2d_list(flow_control):
    data = [
        ['col1', 'col2'],
        ['a', 'b'],
        ['c', 'd']
    ]
    result = flow_control.read_data('my_elem', data, "col1 == 'c';select=col2")
    assert result == ['d']
    assert flow_control.session.elements.get_element('my_elem') == 'd'

def test_read_data_json_single_object(flow_control, tmp_path, monkeypatch):
    json_content = json.dumps({"serialId": "123", "foo": "bar"})
    project_path = tmp_path
    json_file = tmp_path / 'single.json'
    json_file.write_text(json_content)
    flow_control.session.config_handler.config.project_path = str(project_path)
    result = flow_control.read_data('my_elem', 'single.json', "select=serialId")
    assert result == ['123']
    assert flow_control.session.elements.get_element('my_elem') == '123'

def test_read_data_env_scalar_string(flow_control, monkeypatch):
    monkeypatch.setenv('SCALAR_STR', 'simplevalue')
    result = flow_control.read_data('elem_scalar', 'ENV:SCALAR_STR')
    assert result == ['simplevalue']
    assert flow_control.session.elements.get_element('elem_scalar') == 'simplevalue'

def test_read_data_env_scalar_int(flow_control, monkeypatch):
    monkeypatch.setenv('SCALAR_INT', '12345')
    result = flow_control.read_data('elem_scalar_int', 'ENV:SCALAR_INT')
    assert result == ['12345']
    assert flow_control.session.elements.get_element('elem_scalar_int') == '12345'

def test_read_data_env_scalar_float(flow_control, monkeypatch):
    monkeypatch.setenv('SCALAR_FLOAT', '3.14159')
    result = flow_control.read_data('elem_scalar_float', 'ENV:SCALAR_FLOAT')
    assert result == ['3.14159']
    assert flow_control.session.elements.get_element('elem_scalar_float') == '3.14159'

def test_read_data_csv_with_variable_in_query(tmp_path, flow_control, monkeypatch):
    csv_content = 'device_serial,app_package,app_activity\nRZ8RC1KK88R,com.csam.icici.bank.imobileuat,com.csam.icici.bank.imobile.IMOBILE\nRZ8T10TADVR,com.csam.icici.bank.imobileuat,com.csam.icici.bank.imobile.IMOBILE'
    project_path = tmp_path
    csv_file = tmp_path / 'devices.csv'
    csv_file.write_text(csv_content)
    flow_control.session.config_handler.config.project_path = str(project_path)
    # Set element_1 in session elements
    flow_control.session.elements.add_element('element_1', 'RZ8RC1KK88R')
    # Use variable in query
    result = flow_control.read_data('result_elem', 'devices.csv', "device_serial == '${element_1}';select=app_package")
    assert result == ['com.csam.icici.bank.imobileuat']
    assert flow_control.session.elements.get_element('result_elem') == 'com.csam.icici.bank.imobileuat'

# ---- invoke_api Tests ----
def test_invoke_api_extract(monkeypatch, flow_control):
    api_def = DummyApiDef(endpoint='/foo', extract={'result': 'data.value'})
    api_collection = DummyApiCollection('testcol', 'http://dummy', {'bar': api_def})
    flow_control.session.apis.collections['testcol'] = api_collection
    class DummyResp:
        status_code = 200
        reason = 'OK'
        headers = {'Content-Type': 'application/json'}
        content = b'{"data": {"value": "42"}}'
        text = '{"data": {"value": "42"}}'
        def json(self):
            return {"data": {"value": "42"}}
        @property
        def elapsed(self):
            class E:
                def total_seconds(self):
                    return 0.01
            return E()
    monkeypatch.setattr('requests.request', lambda *a, **kw: DummyResp())
    flow_control.invoke_api('testcol.bar')
    assert flow_control.session.elements.get_element('result') == '42'

def test_invoke_api_jsonpath_assertion_pass(monkeypatch, flow_control):
    api_def = DummyApiDef(endpoint='/foo', extract={'result': 'data.value'},
                         jsonpath_assertions=[{'path': '$.data.value', 'condition': '$ == "42"'}])
    api_collection = DummyApiCollection('testcol', 'http://dummy', {'bar': api_def})
    flow_control.session.apis.collections['testcol'] = api_collection
    class DummyResp:
        status_code = 200
        reason = 'OK'
        headers = {'Content-Type': 'application/json'}
        content = b'{"data": {"value": "42"}}'
        text = '{"data": {"value": "42"}}'
        def json(self):
            return {"data": {"value": "42"}}
        @property
        def elapsed(self):
            class E:
                def total_seconds(self):
                    return 0.01
            return E()
    monkeypatch.setattr('requests.request', lambda *a, **kw: DummyResp())
    flow_control.invoke_api('testcol.bar')
    assert flow_control.session.elements.get_element('result') == '42'

def test_invoke_api_jsonpath_assertion_fail(monkeypatch, flow_control):
    api_def = DummyApiDef(endpoint='/foo', extract={'result': 'data.value'},
                         jsonpath_assertions=[{'path': '$.data.value', 'condition': '$ == "99"'}])
    api_collection = DummyApiCollection('testcol', 'http://dummy', {'bar': api_def})
    flow_control.session.apis.collections['testcol'] = api_collection
    class DummyResp:
        status_code = 200
        reason = 'OK'
        headers = {'Content-Type': 'application/json'}
        content = b'{"data": {"value": "42"}}'
        text = '{"data": {"value": "42"}}'
        def json(self):
            return {"data": {"value": "42"}}
        @property
        def elapsed(self):
            class E:
                def total_seconds(self):
                    return 0.01
            return E()
    monkeypatch.setattr('requests.request', lambda *a, **kw: DummyResp())
    with pytest.raises(AssertionError):
        flow_control.invoke_api('testcol.bar')

def test_invoke_api_no_extract(monkeypatch, flow_control):
    api_def = DummyApiDef(endpoint='/foo')
    api_collection = DummyApiCollection('testcol', 'http://dummy', {'bar': api_def})
    flow_control.session.apis.collections['testcol'] = api_collection
    class DummyResp:
        status_code = 200
        reason = 'OK'
        headers = {'Content-Type': 'application/json'}
        content = b'{"foo": "bar"}'
        text = '{"foo": "bar"}'
        def json(self):
            return {"foo": "bar"}
        @property
        def elapsed(self):
            class E:
                def total_seconds(self):
                    return 0.01
            return E()
    monkeypatch.setattr('requests.request', lambda *a, **kw: DummyResp())
    flow_control.invoke_api('testcol.bar')
    # Should not raise, nothing extracted

def test_invoke_api_non_json_response(monkeypatch, flow_control):
    api_def = DummyApiDef(endpoint='/foo', extract={'foo': 'foo'})
    api_collection = DummyApiCollection('testcol', 'http://dummy', {'bar': api_def})
    flow_control.session.apis.collections['testcol'] = api_collection
    class DummyResp:
        status_code = 200
        reason = 'OK'
        headers = {'Content-Type': 'text/plain'}
        content = b'not json'
        text = 'not json'
        def json(self):
            raise json.JSONDecodeError('Expecting value', 'not json', 0)
        @property
        def elapsed(self):
            class E:
                def total_seconds(self):
                    return 0.01
            return E()
    monkeypatch.setattr('requests.request', lambda *a, **kw: DummyResp())
    flow_control.invoke_api('testcol.bar')
    # Should not raise, just logs warning

# ---- run_loop and condition Tests ----
def test_condition_module_true(flow_control, monkeypatch):
    # Setup dummy module that returns non-empty result
    def fake_execute_module(target):
        if target == 'modTrue':
            return ['success']
        if target == 'modA':
            return ['success']
        return []
    flow_control.execute_module = fake_execute_module
    # Add dummy module to session.modules.modules
    flow_control.session.modules.modules['modTrue'] = True
    result = flow_control.condition('modTrue', 'modA', 'modElse')
    assert result == ['success']

def test_condition_module_false(flow_control, monkeypatch):
    # Setup dummy module that returns empty result
    def fake_execute_module(target):
        if target == 'modFalse':
            return []
        if target == 'modA':
            return ['ran:modA']
        if target == 'modElse':
            return ['ran:modA']
        return []
    flow_control.execute_module = fake_execute_module
    flow_control.session.modules.modules['modFalse'] = True
    flow_control.session.modules.modules['modA'] = True
    result = flow_control.condition('modFalse', 'modA', 'modElse')
    assert result == ['ran:modA']

def test_condition_module_true_else(flow_control, monkeypatch):
    # Only else should be executed if module condition is false
    def fake_execute_module(target):
        if target == 'modFalse':
            return []
        if target == 'modElse':
            return ['ran:modElse']
        return []
    flow_control.execute_module = fake_execute_module
    flow_control.session.modules.modules['modFalse'] = True
    result = flow_control.condition('modFalse', 'modElse')
    assert result == ['ran:modElse']

def test_condition_module_true_no_else(flow_control, monkeypatch):
    # Should return result if module condition is true and no else
    def fake_execute_module(target):
        if target == 'modTrue':
            return ['success']
        return []
    flow_control.execute_module = fake_execute_module
    flow_control.session.modules.modules['modTrue'] = True
    result = flow_control.condition('modTrue', 'modA')
    assert result == ['success']

def test_condition_module_false_no_else(flow_control, monkeypatch):
    # Should return None if module condition is false and no else
    def fake_execute_module(target):
        if target == 'modFalse':
            return []
        return []
    flow_control.execute_module = fake_execute_module
    flow_control.session.modules.modules['modFalse'] = True
    result = flow_control.condition('modFalse', 'modA')
    assert result is None
def test_run_loop_by_count(flow_control, monkeypatch):
    calls = []
    def fake_execute_module(target):
        calls.append(target)
        return [f"ran:{target}"]
    flow_control.execute_module = fake_execute_module
    result = flow_control.run_loop('mod1', '3')
    assert result == [["ran:mod1"]] * 3
    assert calls == ['mod1', 'mod1', 'mod1']

def test_run_loop_with_variables(flow_control, monkeypatch):
    results = []
    def fake_execute_module(target):
        val1 = flow_control.session.elements.get_element('foo')
        val2 = flow_control.session.elements.get_element('bar')
        results.append((val1, val2))
        return [f"{val1}-{val2}"]
    flow_control.execute_module = fake_execute_module
    out = flow_control.run_loop('mod2', '${foo}', '[1,2]', '${bar}', '[3,4]')
    assert results == [(1, 3), (2, 4)]
    assert out == [["1-3"], ["2-4"]]

def test_condition_first_true(flow_control, monkeypatch):
    called = []
    def fake_execute_module(target):
        called.append(target)
        return [f"ran:{target}"]
    flow_control.execute_module = fake_execute_module
    flow_control.session.elements.add_element('x', 'yes')
    flow_control.session.elements.add_element('y', 'no')
    result = flow_control.condition('${x} == "yes"', 'modA', '${y} == "yes"', 'modB', 'modElse')
    assert result == ["ran:modA"]
    assert called == ['modA']

def test_condition_else_taken(flow_control, monkeypatch):
    called = []
    def fake_execute_module(target):
        called.append(target)
        return [f"ran:{target}"]
    flow_control.execute_module = fake_execute_module
    flow_control.session.elements.add_element('x', 'no')
    flow_control.session.elements.add_element('y', 'no')
    result = flow_control.condition('${x} == "yes"', 'modA', '${y} == "yes"', 'modB', 'modElse')
    assert result == ["ran:modElse"]
    assert called == ['modElse']

def test_condition_no_else(flow_control, monkeypatch):
    called = []
    def fake_execute_module(target):
        called.append(target)
        return [f"ran:{target}"]
    flow_control.execute_module = fake_execute_module
    flow_control.session.elements.add_element('x', 'no')
    flow_control.session.elements.add_element('y', 'no')
    result = flow_control.condition('${x} == "yes"', 'modA', '${y} == "yes"', 'modB')
    assert result is None
    assert called == []

def test_condition_invalid(flow_control):
    with pytest.raises(ValueError):
        flow_control.condition()
    with pytest.raises(ValueError):
        flow_control.condition('only_one')

def test_run_loop_invalid_args(flow_control):
    with pytest.raises(ValueError):
        flow_control.run_loop('mod', '${foo}', '[1,2]', '${bar}')
    with pytest.raises(ValueError):
        flow_control.run_loop('mod', '${foo}', 'notalist')
