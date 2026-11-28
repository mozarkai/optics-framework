"""End-to-end smoke tests for the public ``Optics`` SDK facade."""
import os

import pytest
import yaml

from optics_framework.common.error import Code, OpticsError
from optics_framework.optics import Optics
from tests.units.popup_helpers import rule, rule_dict

MOCK_API_YAML_PATH = os.path.join(os.path.dirname(__file__), '../mock_servers/api.yaml')
APPIUM_ONLY = {"driver_sources": [{"appium": {"enabled": True, "capabilities": {"platformName": "Android"}}}], "elements_sources": []}


@pytest.fixture
def optics():
    optics = Optics()
    optics.setup(config=APPIUM_ONLY)
    yield optics
    optics.quit()


def _load_api_yaml_pointed_at(base_url):
    """Load the mock API collection and repoint its base_url at the live server."""
    with open(MOCK_API_YAML_PATH, 'r', encoding='utf-8') as f:
        api_yaml_dict = yaml.safe_load(f)
    api_yaml_dict['api']['collections']['mock']['base_url'] = base_url
    return api_yaml_dict


FIELDS_BEYOND_THE_SOURCES = {"ai_self_heal": True, "strict_element_match": True, "halt_duration": 0.5}


def test_setup_from_file_keeps_every_config_key(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({**APPIUM_ONLY, **FIELDS_BEYOND_THE_SOURCES}), encoding="utf-8")
    optics = Optics()
    optics.setup_from_file(str(path))
    assert {key: getattr(optics.config, key) for key in FIELDS_BEYOND_THE_SOURCES} == FIELDS_BEYOND_THE_SOURCES
    optics.quit()


def test_a_second_setup_ends_the_first_session(optics):
    first = optics.session_id
    optics.setup(config=APPIUM_ONLY)
    assert optics.session_id != first
    assert list(optics.session_manager.sessions) == [optics.session_id]


def test_api_token_and_otp_flow(mock_api_server, optics):
    optics.add_api(_load_api_yaml_pointed_at(mock_api_server))
    optics.invoke_api("mock.token")
    optics.add_element("access_token", optics.get_element_value("access_token")[0])
    optics.add_element("userId", optics.get_element_value("userId")[0])
    optics.invoke_api("mock.sendotp")
    assert optics.get_element_value("txnType")[0] == "GEN"


def test_date_evaluate_adds_day(optics):
    assert optics.date_evaluate('tomorrow', '2025-08-14', '+1 day') == '15 August'


def test_setup_with_a_popup_rule_that_cannot_work():
    optics, config = Optics(), {**APPIUM_ONLY, "popup_rules": [rule_dict(action={"type": "swipe", "target": "x"})]}
    with pytest.raises(OpticsError) as caught:
        optics.setup(config=config)
    assert (caught.value.code, caught.value.message) == (
        Code.E0501, "popup_rules['Rate Us'].action.type: Input should be 'tap', 'back', 'alert', 'module' or 'none' (got 'swipe')")


class TestAddPopupRules:
    def test_add_popup_rules(self, optics):
        optics.add_popup_rules([rule_dict("A")])
        optics.add_popup_rules([rule_dict("B", max_per_step=5), rule("A", max_per_step=1)])
        assert optics.session_manager.sessions[optics.session_id].config.popup_rules == [
            rule("A", max_per_step=1), rule("B", max_per_step=5)]

    def test_add_popup_rules_that_cannot_work(self, optics):
        rules = [rule_dict(action={"type": "tap"})]
        with pytest.raises(OpticsError) as caught:
            optics.add_popup_rules(rules)
        assert (caught.value.code, caught.value.message) == (
            Code.E0501, "add_popup_rules: popup_rules['Rate Us'].action: action 'tap' needs a target")

    def test_add_popup_rules_before_setup(self):
        optics, rules = Optics(), [rule_dict()]
        with pytest.raises(OpticsError) as caught:
            optics.add_popup_rules(rules)
        assert (caught.value.code, caught.value.message) == (Code.E0101, "Setup not complete. Call setup() first.")
