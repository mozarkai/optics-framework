
import os
import yaml
import csv
import pytest
from optics_framework.optics import Optics
from tests.mock_servers.single_server import run_single_server


CONTACT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../../optics_framework/samples/contact/config.yaml')
ELEMENTS_CSV_PATH = os.path.join(os.path.dirname(__file__), '../../optics_framework/samples/contact/elements.csv')


def load_config(config_path):
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def load_elements(elements_path):
    elements = {}
    with open(elements_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            elements[row['Element_Name']] = row['Element_ID']
    return elements

@pytest.fixture(scope="module")
def live_servers():
    server_instance, thread = run_single_server()
    yield
    server_instance.should_exit = True
    thread.join()


def test_setup_and_elements():
    elements = load_elements(ELEMENTS_CSV_PATH)
    config = load_config(CONTACT_CONFIG_PATH)
    optics = Optics()
    optics.setup(config=config)
    for name, value in elements.items():
        optics.add_element(name, value)
    for name in elements.keys():
        assert optics.get_element_value(name) == elements[name]
    optics.quit()

def test_setup_from_file():
    optics = Optics()
    optics.setup_from_file(CONTACT_CONFIG_PATH)
    optics.quit()

def test_add_api_and_invoke(live_servers):
    MOCK_API_YAML_PATH = os.path.join(os.path.dirname(__file__), '../mock_servers/api.yaml')
    optics = Optics()
    optics.setup(config=load_config(CONTACT_CONFIG_PATH))
    with open(MOCK_API_YAML_PATH, 'r', encoding='utf-8') as f:
        api_yaml_dict = yaml.safe_load(f)
    optics.add_api(api_yaml_dict)
    optics.invoke_api("mock.token")
    access_token = optics.get_element_value("access_token")
    user_id = optics.get_element_value("userId")
    optics.add_element("access_token", access_token)
    optics.add_element("userId", user_id)
    optics.invoke_api("mock.sendotp")
    txn_type = optics.get_element_value("txnType")
    assert txn_type == "GEN", f"Expected txnType 'GEN', got {txn_type}"
    optics.quit()

def test_add_testcase_and_module():
    optics = Optics()
    optics.setup(config=load_config(CONTACT_CONFIG_PATH))
    optics.add_testcase({'name': 'testcase1'})
    optics.add_module('mod1', {'def': 'value'})
    optics.quit()



def test_flow_control_methods():
    optics = Optics()
    optics.setup(config=load_config(CONTACT_CONFIG_PATH))
    # Add dummy module for flow control with a registered keyword
    optics.add_module('mod1', [('sleep', ['1'])])
    optics.run_loop('mod1', '1')
    # Use a valid condition expression
    optics.condition('True', 'mod1')
    optics.evaluate("result", "1+1")
    # Explicit correct argument order: input date, operation, value
    # Use variable name as first argument, input date as second, operation and value as third
    result_date = optics.date_evaluate('tomorrow', '2025-08-14', '+1 day')
    print(f"date_evaluate result: {result_date}")
    from datetime import datetime
    try:
        datetime.strptime(result_date, "%d %B")
    except Exception:
        assert False, f"date_evaluate returned invalid date string: {result_date}"
    optics.quit()

def test_context_manager():
    config = load_config(CONTACT_CONFIG_PATH)
    with Optics() as optics:
        optics.setup(config=config)
        optics.add_element('foo', 'bar')
        assert optics.get_element_value('foo') == 'bar'

def test_mock_api(live_servers):
    MOCK_API_YAML_PATH = os.path.join(os.path.dirname(__file__), '../mock_servers/api.yaml')
    optics = Optics()
    optics.setup(config=load_config(CONTACT_CONFIG_PATH))  # Minimal setup for API-only test
    with open(MOCK_API_YAML_PATH, 'r', encoding='utf-8') as f:
        api_yaml_dict = yaml.safe_load(f)
    optics.add_api(api_yaml_dict)
    optics.invoke_api("mock.token")
    access_token = optics.get_element_value("access_token")
    user_id = optics.get_element_value("userId")
    print("Access token from mock API:", access_token)
    print("User ID from mock API:", user_id)
    optics.add_element("access_token", access_token)
    optics.add_element("userId", user_id)
    optics.invoke_api("mock.sendotp")
    txn_type = optics.get_element_value("txnType")
    print("OTP txnType from mock API:", txn_type)
    optics.quit()
