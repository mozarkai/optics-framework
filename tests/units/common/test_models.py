"""Popup rules as models.py parses and merges them."""
import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.common.models import merge_popup_rules, parse_popup_rules
from tests.units.popup_helpers import rule, rule_dict


class TestParsePopupRules:
    def test_a_rule_already_parsed(self):
        assert parse_popup_rules([rule()]) == [rule()]

    @pytest.mark.parametrize(("rules", "message"), [
        pytest.param(None, "popup_rules: expected a list of rules", id="no list"),
        pytest.param([42], "popup_rules.0: Input should be a valid dictionary or instance of PopupRule (got 42)",
                     id="a rule that is not a dict"),
        pytest.param([rule_dict(detect={})],
                     "popup_rules['Rate Us'].detect: detect needs text_contains, resource_id or xpath", id="no detect keys"),
        pytest.param([rule_dict(detect={"text_contains": ["  ", ""]})],
                     "popup_rules['Rate Us'].detect: detect needs text_contains, resource_id or xpath", id="blank texts"),
        pytest.param([rule_dict(detect={"xpath": "//a["})],
                     "popup_rules['Rate Us'].detect: xpath is not valid: Invalid expression", id="an XPath that is not valid"),
        pytest.param([rule_dict(action={"type": "tap"})],
                     "popup_rules['Rate Us'].action: action 'tap' needs a target", id="a tap with no target"),
        pytest.param([rule_dict(action={"type": "alert"})],
                     "popup_rules['Rate Us'].action: action 'alert' needs a target", id="an alert with no target"),
        pytest.param([rule_dict(action={"type": "module"})],
                     "popup_rules['Rate Us'].action: action 'module' needs a target", id="a module with no target"),
        pytest.param([rule_dict(action={"type": "none"})],
                     "popup_rules['Rate Us']: retry_step needs an action that dismisses the popup",
                     id="retry with no action"),
        pytest.param([rule_dict(check="sometimes")],
                     "popup_rules['Rate Us'].check: Input should be 'on_failure' or 'before_every_step' (got 'sometimes')",
                     id="an unknown check"),
        pytest.param([rule_dict(then="explode")],
                     "popup_rules['Rate Us'].then: Input should be 'retry_step', 'fail_step' or 'stop_run' (got 'explode')",
                     id="an unknown then"),
        pytest.param([rule_dict(max_per_step=0)],
                     "popup_rules['Rate Us'].max_per_step: Input should be greater than or equal to 1 (got 0)",
                     id="a per-step limit of zero"),
        pytest.param([rule_dict(), rule_dict(max_per_step=3)], "popup_rules: rule names must be unique", id="two rules of one name"),
    ])
    def test_parse_popup_rules_refuses(self, rules, message):
        with pytest.raises(OpticsError) as caught:
            parse_popup_rules(rules)
        assert (caught.value.code, caught.value.message) == (Code.E0501, message)

    def test_parse_popup_rules_names_where_the_rules_came_from(self):
        rules = [rule_dict(), rule_dict()]
        with pytest.raises(OpticsError) as caught:
            parse_popup_rules(rules, "add_popup_rules")
        assert caught.value.message == "add_popup_rules: rule names must be unique"


class TestMergePopupRules:
    @pytest.mark.parametrize(("old", "new", "merged"), [
        pytest.param([], [], [], id="no rules"),
        pytest.param([rule("A")], [], [rule("A")], id="no new rules"),
        pytest.param([rule("A")], [rule("B")], [rule("A"), rule("B")], id="a rule of a new name"),
        pytest.param([rule("A"), rule("B")], [rule("A", max_per_step=1)], [rule("A", max_per_step=1), rule("B")],
                     id="a rule of a name already there, in its place"),
    ])
    def test_merge_popup_rules(self, old, new, merged):
        assert merge_popup_rules(old, new) == merged
