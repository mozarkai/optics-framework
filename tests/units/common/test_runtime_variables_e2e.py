"""Runtime variables, run end to end: a suite on disk goes through `optics execute`'s own
runner, flow control and logging; only the device is a fake."""
import asyncio

import pytest

from optics_framework.api.action_keyword import ActionKeyword
from optics_framework.api.verifier import Verifier
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.helper.execute import DryRunRunner, ExecuteRunner, RunnerArgs

CONFIG = """\
driver_sources:
  - appium:
      enabled: true
      url: "http://127.0.0.1:9/wd/hub"
      capabilities: {platformName: Android}
elements_sources:
  - appium_find_element: {enabled: true}
file_log: true
log_level: DEBUG
"""
ELEMENTS = {"price_label": "#price", "title_label": "#title"}
SCREEN = {"#price": "$ 29.99", "#title": "Backpack", "#user": "bob@example.com", "(//row)[1]": "first", "(//row)[2]": "second"}


class Device:
    """Stands in for ActionKeyword and Verifier: answers from SCREEN and records what it was asked."""

    def __init__(self):
        self.calls = []

    def get_text(self, element, index="0"):
        self.calls.append(("get_text", element))
        if element not in SCREEN:
            raise OpticsError(Code.E0201, message=f"No element {element}")
        internal_logger.info(f"Text of element: {SCREEN[element]}")
        return SCREEN[element]

    def press_element(self, element, repeat="1", offset_x="0", offset_y="0", event_name=None):
        self.calls.append(("press_element", element))

    def enter_text(self, element, text, event_name=None):
        self.calls.append(("enter_text", element, text))
        internal_logger.info(f"Entering text '{text}' into element '{element}'")

    def sleep(self, duration):
        self.calls.append(("sleep", duration))

    def validate_element(self, element, timeout="10", rule="all", event_name=None):
        self.calls.append(("validate_element", element))
        if element not in SCREEN.values() and element not in SCREEN:
            raise OpticsError(Code.E0201, message=f"Not on screen: {element}")


@pytest.fixture
def device(monkeypatch):
    fake = Device()
    monkeypatch.setattr(OpticsBuilder, "build", lambda self, cls: fake if cls in (ActionKeyword, Verifier) else object())
    return fake


@pytest.fixture
def run(tmp_path, device):
    def run(modules, tests, elements=ELEMENTS, dry=False, runner="test_runner"):
        (tmp_path / "config.yaml").write_text(CONFIG)
        (tmp_path / "elements.csv").write_text(
            "Element_Name,Element_ID\n" + "".join(f"{k},{v}\n" for k, v in elements.items()))
        width = max(len(step) for steps in modules.values() for step in steps) - 1
        rows = [",".join(["module_name", "module_step"] + [f"param_{i}" for i in range(1, width + 1)])]
        for name, steps in modules.items():
            for keyword, *params in steps:
                cells = [name, keyword, *[f'"{p}"' if "," in p or '"' in p else p for p in
                                          (q.replace('"', '""') for q in params)]]
                rows.append(",".join(cells + [""] * (width - len(params))))
        (tmp_path / "modules.csv").write_text("\n".join(rows) + "\n")
        (tmp_path / "test_cases.csv").write_text(
            "test_case,test_step\n" + "".join(f"{tc},{m}\n" for tc, mods in tests.items() for m in mods))
        runner_cls = DryRunRunner if dry else ExecuteRunner
        args = RunnerArgs(folder_path=str(tmp_path), runner=runner, use_printer=False)
        return asyncio.run(runner_cls(args).execute())
    return run


def _reasons(results, test_case):
    return [k.reason for m in results[test_case].modules for k in m.keywords if k.status == "FAIL"]


def _logs(tmp_path):
    return "".join(p.read_text() for p in (tmp_path / "execution_output").glob("*.log"))


def test_a_stored_value_is_used_inside_a_later_locator(run, device):
    results = run({"M": [("Get Text", "${price_label}", "store_as=price"),
                         ("Press Element", '//row[@text="${price}"]')]}, {"TC": ["M"]})

    assert results["TC"].status == "PASS"
    assert ("press_element", '//row[@text="$ 29.99"]') in device.calls


def test_the_store_cell_may_come_first_and_name_the_variable_in_braces(run, device):
    results = run({"M": [("Get Text", "store_as=${p}", "${price_label}"), ("Press Element", "${p}")]}, {"TC": ["M"]})

    assert results["TC"].status == "PASS"
    assert ("get_text", "#price") in device.calls
    assert ("press_element", "$ 29.99") in device.calls


def test_a_later_store_replaces_the_earlier_value(run, device):
    run({"M": [("Get Text", "${price_label}", "store_as=v"), ("Get Text", "${title_label}", "store_as=v"),
               ("Press Element", "${v}")]}, {"TC": ["M"]})

    assert device.calls[-1] == ("press_element", "Backpack")


def test_storing_what_a_keyword_did_not_return_fails_the_step(run):
    results = run({"M": [("Sleep", "1", "store_as=nothing")]}, {"TC": ["M"]})

    assert results["TC"].status == "FAIL"
    assert "returned no value" in _reasons(results, "TC")[0]


def test_a_store_cell_without_a_name_fails_the_step(run):
    results = run({"M": [("Get Text", "${price_label}", "store_as=")]}, {"TC": ["M"]})

    assert "store_as needs a name" in _reasons(results, "TC")[0]


def test_a_value_reaches_later_test_cases(run, device):
    results = run({"Store": [("Get Text", "${price_label}", "store_as=price")],
                   "Use": [("Press Element", "${price}")]},
                  {"TC1": ["Store"], "TC2": ["Use"]})

    assert results["TC2"].status == "PASS"
    assert device.calls[-1] == ("press_element", "$ 29.99")


def test_the_pytest_runner_keeps_a_value_across_test_cases_too(run, device):
    run({"Store": [("Get Text", "${price_label}", "store_as=price")],
         "Use": [("Press Element", "${price}")]},
        {"TC1": ["Store"], "TC2": ["Use"]}, runner="pytest")

    assert device.calls[-1] == ("press_element", "$ 29.99")


def test_an_unknown_name_inside_a_param_fails_before_the_device_is_touched(run, device):
    results = run({"M": [("Press Element", '//row[@text="${never_stored}"]')]}, {"TC": ["M"]})

    assert "'${never_stored}'" in _reasons(results, "TC")[0]
    assert "is not defined" in _reasons(results, "TC")[0]
    assert device.calls == []


def test_run_loop_hands_each_index_into_the_body_locator(run, device):
    results = run({"Body": [("Get Text", "(//row)[${i}]", "store_as=row")],
                   "Loop": [("Run Loop", "Body", "${i}", "1|2"), ("Press Element", "${row}")]},
                  {"TC": ["Loop"]})

    assert results["TC"].status == "PASS"
    assert [c for c in device.calls if c[0] == "get_text"] == [("get_text", "(//row)[1]"), ("get_text", "(//row)[2]")]
    assert device.calls[-1] == ("press_element", "second")


def test_evaluate_and_condition_substitute_a_stored_value_themselves(run, device):
    results = run({"Hit": [("Press Element", "#hit")],
                   "M": [("Get Text", "${price_label}", "store_as=price"),
                         ("Evaluate", "${matches}", '"${price}" == "$ 29.99"'),
                         ("Condition", "${matches} == 'True'", "Hit")]},
                  {"TC": ["M"]})

    assert results["TC"].status == "PASS"
    assert ("press_element", "#hit") in device.calls


def test_an_unknown_name_in_an_evaluate_expression_gets_evaluates_own_error(run):
    results = run({"M": [("Evaluate", "${r}", "${never_stored} + 1")]}, {"TC": ["M"]})

    assert "not found in elements" in _reasons(results, "TC")[0]


def test_a_secret_read_from_the_screen_is_masked_in_every_log_line(run, device, tmp_path):
    run({"M": [("Get Text", "#user", "store_as=user_api_key"),
               ("Press Element", '//field[@text="${user_api_key}"]')]}, {"TC": ["M"]})

    assert ("press_element", '//field[@text="bob@example.com"]') in device.calls
    assert "bob@example.com" not in _logs(tmp_path)
    assert "Text of element: ****" in _logs(tmp_path)


@pytest.mark.parametrize("name", ["password1", "login_otp2", "OTPCode", "myAPIToken"])
def test_a_secret_name_with_digits_or_an_acronym_is_masked(run, tmp_path, name):
    run({"M": [("Get Text", "#user", f"store_as={name}")]}, {"TC": ["M"]})

    assert "bob@example.com" not in _logs(tmp_path)


def test_an_elements_file_value_is_not_masked_whatever_its_name(run, tmp_path):
    run({"M": [("Get Text", "${pin_tab}"), ("Sleep", "${otp_length}")]}, {"TC": ["M"]},
        elements={"pin_tab": "#title", "otp_length": "6"})

    assert "Text of element: Backpack" in _logs(tmp_path)
    assert "****" not in _logs(tmp_path)


def test_a_short_value_or_a_name_that_only_mentions_api_stays_readable(run, device, tmp_path):
    SCREEN["#code"] = "42"
    run({"M": [("Get Text", "#code", "store_as=pin"), ("Get Text", "${price_label}", "store_as=api_bal")]}, {"TC": ["M"]})
    del SCREEN["#code"]

    assert "Text of element: 42" in _logs(tmp_path)
    assert "Text of element: $ 29.99" in _logs(tmp_path)


def test_one_sessions_secret_does_not_mask_another_sessions_logs(run, tmp_path):
    run({"M": [("Get Text", "#user", "store_as=user_api_key")]}, {"TC": ["M"]})
    run({"M": [("Get Text", "#user", "store_as=user")]}, {"TC": ["M"]})

    assert _logs(tmp_path).count("Text of element: bob@example.com") == 1


def test_the_report_shows_what_each_step_stored_with_secrets_masked(run):
    results = run({"M": [("Get Text", "${price_label}", "store_as=price"),
                         ("Get Text", "#user", "store_as=user_api_key")]}, {"TC": ["M"]})

    stored = [k.stored for k in results["TC"].modules[0].keywords]
    assert stored == [{"price": "$ 29.99"}, {"user_api_key": "****"}]


def test_a_value_under_a_plain_name_stays_readable(run, tmp_path):
    run({"M": [("Get Text", "${title_label}", "store_as=title")]}, {"TC": ["M"]})

    assert "Text of element: Backpack" in _logs(tmp_path)


def test_a_dry_run_resolves_stored_names_and_catches_the_two_mistakes(run, device):
    results = run({"Good": [("Get Text", "${price_label}", "store_as=price"), ("Press Element", "(//row)[${price}]")],
                   "Typo": [("Press Element", "(//row)[${pirce}]")],
                   "NoName": [("Get Text", "${price_label}", "store_as=")]},
                  {"TC1": ["Good"], "TC2": ["Typo"], "TC3": ["NoName"]}, dry=True)

    assert results["TC1"].status == "PASS"
    assert "'${pirce}'" in _reasons(results, "TC2")[0]
    assert "store_as needs a name" in _reasons(results, "TC3")[0]
    assert device.calls == []


def test_a_read_data_query_quotes_a_variable_itself(run, device, tmp_path):
    (tmp_path / "people.csv").write_text("name,city\nalice,Pune\nbob,Delhi\n")
    results = run({"M": [("Read Data", "${city}", (tmp_path / "people.csv").as_posix(), "name == ${user};select=city"),
                         ("Press Element", "${city}")]}, {"TC": ["M"]},
                  elements={**ELEMENTS, "user": "alice"})

    assert results["TC"].status == "PASS"
    assert ("press_element", "Pune") in device.calls


def test_a_value_set_in_suite_setup_or_by_evaluate_reaches_later_test_cases(run, device):
    results = run({"Setup": [("Get Text", "${title_label}", "store_as=product")],
                   "Count": [("Evaluate", "${total}", "1 + 1")],
                   "Use": [("Press Element", "${product}"), ("Press Element", "${total}")]},
                  {"Suite Setup": ["Setup"], "TC1": ["Count"], "TC2": ["Use"]})

    assert results["TC2"].status == "PASS"
    assert device.calls[-2:] == [("press_element", "Backpack"), ("press_element", "2")]


def test_a_doubled_dollar_passes_a_literal_placeholder(run, device):
    results = run({"M": [("Press Element", "//*[@text='$${literal}']")]}, {"TC": ["M"]})

    assert results["TC"].status == "PASS"
    assert ("press_element", "//*[@text='${literal}']") in device.calls


def test_store_as_may_have_spaces_around_the_equals_sign(run, device):
    run({"M": [("Get Text", "${price_label}", "store_as = price"), ("Press Element", "${price}")]}, {"TC": ["M"]})

    assert device.calls[-1] == ("press_element", "$ 29.99")


def test_date_evaluate_takes_a_stored_number_inside_its_expression(run, device):
    results = run({"M": [("Date Evaluate", "${due}", "2025-04-25", "+${days} days", "%d %B"), ("Press Element", "${due}")]},
                  {"TC": ["M"]}, elements={**ELEMENTS, "days": "2"})

    assert results["TC"].status == "PASS"
    assert device.calls[-1] == ("press_element", "27 April")


def test_a_dry_run_counts_names_that_evaluate_and_read_data_define(run, tmp_path):
    (tmp_path / "people.csv").write_text("name,city\nalice,Pune\n")
    results = run({"M": [("Evaluate", "${total}", "1 + 1"), ("Read Data", "${city}", (tmp_path / "people.csv").as_posix(), "select=city"),
                         ("Press Element", "//row[@n='${total}' and @city='${city}']")]}, {"TC": ["M"]}, dry=True)

    assert results["TC"].status == "PASS"
