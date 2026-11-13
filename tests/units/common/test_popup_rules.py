"""Popup rules: the model, parsing, the popup_rules.json reader and how a rule list reaches the session."""
import json
from collections import Counter
from types import SimpleNamespace

import pytest

from optics_framework.common.config_handler import Config
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.popup_guard import PopupRun
from optics_framework.common.models import PopupRule, merge_popup_rules, parse_popup_rules
from optics_framework.common.runner.data_reader import read_popup_rules
from optics_framework.helper.execute import BaseRunner, find_popup_rules
from optics_framework.optics import Optics
from tests.units.popup_helpers import rule_dict


def test_a_rule_takes_the_documented_defaults():
    r = PopupRule(**rule_dict())
    assert (r.check, r.then, r.sensitive, r.max_per_step, r.max_per_run) == ("on_failure", "retry_step", False, 2, 5)


@pytest.mark.parametrize("bad, why", [
    (rule_dict(detect={}), "detect needs"),
    (rule_dict(action={"type": "tap"}), "needs a target"),
    (rule_dict(action={"type": "alert"}), "needs a target"),
    (rule_dict(action={"type": "none"}), "retry_step needs an action"),
    (rule_dict(check="sometimes"), "check"),
    (rule_dict(then="explode"), "then"),
    (rule_dict(max_per_step=0), "max_per_step"),
    ({"name": "x", "detect": {"xpath": "//a"}}, "priority"),
])
def test_a_rule_that_cannot_work_is_refused(bad, why):
    with pytest.raises(OpticsError, match=why) as caught:
        parse_popup_rules([bad])
    assert caught.value.code == Code.E0501


def test_a_sensitive_rule_never_acts_whatever_it_says():
    r = PopupRule(**rule_dict(sensitive=True, then="retry_step"))
    assert (r.action.type, r.then) == ("none", "stop_run")


def test_a_fail_or_stop_rule_may_take_no_action():
    assert PopupRule(**rule_dict(action={"type": "none"}, then="fail_step")).action.type == "none"


def test_the_error_names_the_rule_and_the_field():
    with pytest.raises(OpticsError, match=r"rule 2 is invalid .*priority"):
        parse_popup_rules([rule_dict(), {"name": "x", "detect": {"xpath": "//a"}}], "rules.json")


def test_rule_names_must_be_unique():
    with pytest.raises(OpticsError, match="unique"):
        parse_popup_rules([rule_dict(), rule_dict(priority=3)])


@pytest.mark.parametrize("raw", [{"name": "x"}, "nope", None])
def test_rules_must_be_a_list(raw):
    with pytest.raises(OpticsError, match="expected a list"):
        parse_popup_rules(raw)


def test_a_new_rule_replaces_the_one_of_the_same_name_in_place():
    old = parse_popup_rules([rule_dict("A"), rule_dict("B", priority=20)])
    new = parse_popup_rules([rule_dict("B", priority=1), rule_dict("C", priority=30)])
    merged = merge_popup_rules(old, new)
    assert [(r.name, r.priority) for r in merged] == [("A", 10), ("B", 1), ("C", 30)]


def test_the_session_config_takes_rules_and_permissions():
    c = Config(popup_rules=[rule_dict()], permissions={"camera": "allow", "notifications": "deny"})
    assert c.popup_rules[0].name == "Rate Us" and c.permissions["notifications"] == "deny"
    assert Config().popup_rules == [] and Config().permissions == {}


def test_a_permission_is_allow_or_deny():
    with pytest.raises(ValueError):
        Config(permissions={"camera": "maybe"})


def test_read_popup_rules_reads_a_file(tmp_path):
    path = tmp_path / "popup_rules.json"
    path.write_text(json.dumps([rule_dict()]))
    assert [r.name for r in read_popup_rules(str(path))] == ["Rate Us"]


@pytest.mark.parametrize("content", ["{not json", json.dumps({"name": "x"}), json.dumps([{"name": "x"}])])
def test_an_unreadable_rules_file_is_an_error_not_no_rules(tmp_path, content):
    path = tmp_path / "popup_rules.json"
    path.write_text(content)
    with pytest.raises(OpticsError) as caught:
        read_popup_rules(str(path))
    assert caught.value.code == Code.E0501 and str(path) in caught.value.message


def test_a_missing_rules_file_is_an_error(tmp_path):
    with pytest.raises(OpticsError, match="cannot read"):
        read_popup_rules(str(tmp_path / "popup_rules.json"))


def test_find_popup_rules_looks_under_the_folder_by_name(tmp_path):
    (tmp_path / "test_data").mkdir()
    (tmp_path / "test_data" / "popup_rules.json").write_text("[]")
    (tmp_path / "other.json").write_text("[]")
    assert find_popup_rules(str(tmp_path)) == [str(tmp_path / "test_data" / "popup_rules.json")]
    assert find_popup_rules(str(tmp_path / "test_data" / "nowhere")) == []


def test_a_job_folder_rule_replaces_a_same_named_one_from_config_yaml(tmp_path):
    (tmp_path / "popup_rules.json").write_text(json.dumps([rule_dict("Rate Us", priority=1), rule_dict("What's New", priority=2)]))
    runner = SimpleNamespace(folder_path=str(tmp_path), config=Config(popup_rules=[rule_dict("Rate Us", priority=99)]))
    BaseRunner._load_popup_rules(runner)
    assert sorted((r.name, r.priority) for r in runner.config.popup_rules) == [("Rate Us", 1), ("What's New", 2)]


def test_an_invalid_rules_file_stops_the_run_naming_the_file(tmp_path, capsys):
    (tmp_path / "popup_rules.json").write_text("[{}]")
    runner = SimpleNamespace(folder_path=str(tmp_path), config=Config())
    with pytest.raises(SystemExit):
        BaseRunner._load_popup_rules(runner)
    assert "popup_rules.json" in capsys.readouterr().err.replace("\n", "")


def _optics(config):
    sessions = {"s1": SimpleNamespace(config=config)}
    return SimpleNamespace(session_id="s1", session_manager=SimpleNamespace(sessions=sessions))


def test_add_popup_rules_merges_onto_the_session_config():
    config = Config(popup_rules=[rule_dict("A")])
    Optics.add_popup_rules(_optics(config), [rule_dict("B", priority=5), PopupRule(**rule_dict("A", priority=1))])
    assert [(r.name, r.priority) for r in config.popup_rules] == [("A", 1), ("B", 5)]


def test_add_popup_rules_needs_a_session():
    stub = SimpleNamespace(session_id=None, session_manager=SimpleNamespace(sessions={}))
    with pytest.raises(OpticsError) as caught:
        Optics.add_popup_rules(stub, [rule_dict()])
    assert caught.value.code == Code.E0101


def test_add_popup_rules_leaves_the_rules_alone_when_one_is_invalid():
    config = Config(popup_rules=[rule_dict("A")])
    with pytest.raises(OpticsError):
        Optics.add_popup_rules(_optics(config), [rule_dict("B"), {"name": "bad"}])
    assert [r.name for r in config.popup_rules] == ["A"]


def _with_run(run):
    optics = Optics.__new__(Optics)
    optics.action_keyword = SimpleNamespace(_popup_run=run) if run else None
    return optics


def test_the_library_reads_the_events_and_summary_without_clearing_them():
    run = PopupRun()
    run.events.append({"rule": "Rate Us", "verdict": "handled"})
    run.counts["Rate Us"] = Counter({"handled": 1})
    stub = _with_run(run)
    assert stub.get_popup_events() == [{"rule": "Rate Us", "verdict": "handled"}]
    assert stub.get_popup_events() and run.events, "reading leaves them in place"
    assert stub.get_popup_summary() == ["Popup 'Rate Us': handled 1, limit 0, stopped 0"]


def test_reading_popup_results_needs_a_session():
    with pytest.raises(OpticsError) as caught:
        _with_run(None).get_popup_events()
    assert caught.value.code == Code.E0101
