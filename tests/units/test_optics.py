"""End-to-end smoke tests for the public ``Optics`` SDK facade.

These exercise the real facade against the bundled ``contact`` sample project and
the in-process mock API server (``mock_api_server`` fixture), so they double as a
wiring check that setup → keyword → teardown holds together.
"""
import csv
import os

import yaml

from optics_framework.optics import Optics

CONTACT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../../optics_framework/samples/contact/config.yaml')
ELEMENTS_CSV_PATH = os.path.join(os.path.dirname(__file__), '../../optics_framework/samples/contact/test_data/elements.csv')
MOCK_API_YAML_PATH = os.path.join(os.path.dirname(__file__), '../mock_servers/api.yaml')


def load_config(config_path):
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def load_elements(elements_path):
    with open(elements_path, 'r') as f:
        return {row['Element_Name']: row['Element_ID'] for row in csv.DictReader(f)}


def _load_api_yaml_pointed_at(base_url):
    """Load the mock API collection and repoint its base_url at the live server."""
    with open(MOCK_API_YAML_PATH, 'r', encoding='utf-8') as f:
        api_yaml_dict = yaml.safe_load(f)
    api_yaml_dict['api']['collections']['mock']['base_url'] = base_url
    return api_yaml_dict


def test_extract_config_data_forwards_save_captures():
    config_data = Optics()._extract_config_data(
        config={"driver_sources": [], "elements_sources": [], "save_captures": False},
        driver_sources=None,
        elements_sources=None,
        image_detection=None,
        text_detection=None,
        execution_output_path_param=None,
    )
    assert config_data["save_captures"] is False


def test_setup_and_element_round_trip():
    elements = load_elements(ELEMENTS_CSV_PATH)
    optics = Optics()
    optics.setup(config=load_config(CONTACT_CONFIG_PATH))
    for name, value in elements.items():
        optics.add_element(name, value)
    for name, value in elements.items():
        assert optics.get_element_value(name)[0] == value
    optics.quit()


def test_setup_from_file_populates_config():
    optics = Optics()
    optics.setup_from_file(CONTACT_CONFIG_PATH)
    assert optics.config is not None
    optics.quit()


def test_add_testcase_and_module():
    optics = Optics()
    optics.setup(config=load_config(CONTACT_CONFIG_PATH))
    optics.add_testcase({'name': 'testcase1'})
    optics.add_module('mod1', {'def': 'value'})
    optics.quit()


def test_context_manager_element_round_trip():
    with Optics() as optics:
        optics.setup(config=load_config(CONTACT_CONFIG_PATH))
        optics.add_element('foo', 'bar')
        assert optics.get_element_value('foo')[0] == 'bar'


def test_api_token_and_otp_flow(mock_api_server):
    optics = Optics()
    optics.setup(config=load_config(CONTACT_CONFIG_PATH))
    optics.add_api(_load_api_yaml_pointed_at(mock_api_server))
    optics.invoke_api("mock.token")
    optics.add_element("access_token", optics.get_element_value("access_token")[0])
    optics.add_element("userId", optics.get_element_value("userId")[0])
    optics.invoke_api("mock.sendotp")
    assert optics.get_element_value("txnType")[0] == "GEN"
    optics.quit()


def test_run_loop_executes_module():
    optics = Optics()
    optics.setup(config=load_config(CONTACT_CONFIG_PATH))
    optics.add_module('mod1', [('sleep', ['1'])])
    # Loops the module the requested number of times without raising.
    assert optics.run_loop('mod1', '1') is not None
    optics.quit()


def test_evaluate_stores_result():
    optics = Optics()
    optics.setup(config=load_config(CONTACT_CONFIG_PATH))
    assert optics.evaluate("result", "1+1") == 2
    optics.quit()


def test_date_evaluate_adds_day():
    optics = Optics()
    optics.setup(config=load_config(CONTACT_CONFIG_PATH))
    assert optics.date_evaluate('tomorrow', '2025-08-14', '+1 day') == '15 August'
    optics.quit()
