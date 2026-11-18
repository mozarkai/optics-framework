"""End-to-end smoke tests for the public ``Optics`` SDK facade."""
import os

import pytest
import yaml

from optics_framework.optics import Optics

# The bundled contact sample enables the easyocr text-detection engine in its
# config.yaml, so every setup()-driven test below instantiates it. Skip the
# module when easyocr isn't installed (mirrors the guarded skip in
# test_mcp_server.py); avoids pulling the multi-GB torch stack into CI.
pytest.importorskip("easyocr", reason="contact sample config enables the easyocr engine")

CONTACT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../../optics_framework/samples/contact/config.yaml')
MOCK_API_YAML_PATH = os.path.join(os.path.dirname(__file__), '../mock_servers/api.yaml')


def load_config(config_path):
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def _load_api_yaml_pointed_at(base_url):
    """Load the mock API collection and repoint its base_url at the live server."""
    with open(MOCK_API_YAML_PATH, 'r', encoding='utf-8') as f:
        api_yaml_dict = yaml.safe_load(f)
    api_yaml_dict['api']['collections']['mock']['base_url'] = base_url
    return api_yaml_dict


FIELDS_BEYOND_THE_SOURCES = {"ai_self_heal": True, "strict_element_match": True, "halt_duration": 0.5}


def test_setup_from_file_keeps_every_config_key(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({**load_config(CONTACT_CONFIG_PATH), **FIELDS_BEYOND_THE_SOURCES}), encoding="utf-8")
    optics = Optics()
    optics.setup_from_file(str(path))
    assert {key: getattr(optics.config, key) for key in FIELDS_BEYOND_THE_SOURCES} == FIELDS_BEYOND_THE_SOURCES
    optics.quit()


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


def test_date_evaluate_adds_day():
    optics = Optics()
    optics.setup(config=load_config(CONTACT_CONFIG_PATH))
    assert optics.date_evaluate('tomorrow', '2025-08-14', '+1 day') == '15 August'
    optics.quit()
