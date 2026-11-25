"""Popup rules: the model, parsing, the popup_rules.json reader and how a rule list reaches the session."""
import json
from types import SimpleNamespace

import pytest

from optics_framework.common.config_handler import Config
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.models import PopupRule, merge_popup_rules, parse_popup_rules
from optics_framework.common.runner.data_reader import read_popup_rules
from optics_framework.optics import Optics
from tests.units.popup_helpers import rule_dict


@pytest.mark.parametrize("bad, why", [
    (rule_dict(detect={}), "detect needs"),
    (rule_dict(detect={"text_contains": ["  ", ""]}), "detect needs"),
    (rule_dict(detect={"xpath": "//a["}), "xpath is not valid"),
    (rule_dict(action={"type": "tap"}), "needs a target"),
    (rule_dict(action={"type": "alert"}), "needs a target"),
    (rule_dict(action={"type": "module"}), "needs a target"),
    (rule_dict(action={"type": "none"}), "retry_step needs an action"),
    (rule_dict(check="sometimes"), "check"),
    (rule_dict(then="explode"), "then"),
    (rule_dict(max_per_step=0), "max_per_step"),
])
def test_a_rule_that_cannot_work_is_refused(bad, why):
    with pytest.raises(OpticsError, match=why) as caught:
        parse_popup_rules([bad])
    assert caught.value.code == Code.E0501


def test_rule_names_must_be_unique():
    duplicates = [rule_dict(), rule_dict(max_per_step=3)]
    with pytest.raises(OpticsError, match="unique"):
        parse_popup_rules(duplicates)


def test_a_new_rule_replaces_the_one_of_the_same_name_in_place():
    old = parse_popup_rules([rule_dict("A"), rule_dict("B", max_per_step=3)])
    new = parse_popup_rules([rule_dict("B", max_per_step=1), rule_dict("C", max_per_step=4)])
    merged = merge_popup_rules(old, new)
    assert [(r.name, r.max_per_step) for r in merged] == [("A", 2), ("B", 1), ("C", 4)]


@pytest.mark.parametrize("content", ["{not json", json.dumps({"name": "x"}), json.dumps([{"name": "x"}])])
def test_an_unreadable_rules_file_is_an_error_not_no_rules(tmp_path, content):
    path = tmp_path / "popup_rules.json"
    path.write_text(content)
    file = str(path)
    with pytest.raises(OpticsError) as caught:
        read_popup_rules(file)
    assert caught.value.code == Code.E0501
    assert str(path) in caught.value.message


def _optics(config):
    sessions = {"s1": SimpleNamespace(config=config)}
    return SimpleNamespace(session_id="s1", session_manager=SimpleNamespace(sessions=sessions))


def test_add_popup_rules_merges_onto_the_session_config():
    config = Config(popup_rules=[rule_dict("A")])
    Optics.add_popup_rules(_optics(config), [rule_dict("B", max_per_step=5), PopupRule(**rule_dict("A", max_per_step=1))])
    assert [(r.name, r.max_per_step) for r in config.popup_rules] == [("A", 1), ("B", 5)]


def test_a_bad_rule_in_the_setup_config_names_the_rule_field_and_value():
    optics = Optics()
    config = {
        "driver_sources": [{"appium": {"enabled": True}}],
        "elements_sources": [{"appium_find_element": {"enabled": True}}],
        "popup_rules": [rule_dict(action={"type": "swipe", "target": "x"})],
    }
    with pytest.raises(OpticsError) as caught:
        optics.setup(config=config)
    assert caught.value.code == Code.E0501
    assert "popup_rules['Rate Us'].action.type" in caught.value.message
    assert "'swipe'" in caught.value.message
