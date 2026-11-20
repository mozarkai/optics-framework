import asyncio
import json
import re
import xml.etree.ElementTree as ET  # nosec B405

import pytest

from optics_framework.api.action_keyword import ActionKeyword
from optics_framework.api.verifier import Verifier
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.runner.event_stream import OutputMode
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

    _pop_last_heal_info = ActionKeyword._pop_last_heal_info

    def __init__(self):
        self.calls = []
        self.heals = {}
        self._last_heal_info = None

    def get_text(self, element, index="0"):
        self.calls.append(("get_text", element))
        if element not in SCREEN:
            raise OpticsError(Code.E0201, message=f"No element {element}")
        internal_logger.info(f"Text of element: {SCREEN[element]}")
        return SCREEN[element]

    def press_element(self, element, repeat="1", offset_x="0", offset_y="0", event_name=None):
        self.calls.append(("press_element", element))
        self._last_heal_info = self.heals.get(element)

    def enter_text(self, element, text, event_name=None):
        self.calls.append(("enter_text", element, text))
        internal_logger.info(f"Entering text '{text}' into element '{element}'")

    def sleep(self, duration):
        self.calls.append(("sleep", duration))

    def get_page_source(self):
        nodes = "".join(f'<node text="{text}"/>' for text in SCREEN.values())
        return f"<hierarchy>{nodes}</hierarchy>", "20260101000000"

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
    def run(modules, tests, elements=ELEMENTS, dry=False, runner="test_runner", output=None):
        (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
        (tmp_path / "elements.csv").write_text(
            "Element_Name,Element_ID\n" + "".join(f"{k},{v}\n" for k, v in elements.items()), encoding="utf-8")
        width = max(len(step) for steps in modules.values() for step in steps) - 1
        rows = [",".join(["module_name", "module_step"] + [f"param_{i}" for i in range(1, width + 1)])]
        for name, steps in modules.items():
            for keyword, *params in steps:
                cells = [name, keyword, *[f'"{p}"' if "," in p or '"' in p else p for p in
                                          (q.replace('"', '""') for q in params)]]
                rows.append(",".join(cells + [""] * (width - len(params))))
        (tmp_path / "modules.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        (tmp_path / "test_cases.csv").write_text(
            "test_case,test_step\n" + "".join(f"{tc},{m}\n" for tc, mods in tests.items() for m in mods),
            encoding="utf-8")
        runner_cls = DryRunRunner if dry else ExecuteRunner
        args = RunnerArgs(folder_path=str(tmp_path), runner=runner, use_printer=False, output=output)
        return asyncio.run(runner_cls(args).execute())
    return run


def _reasons(results, test_case):
    return [k.reason for m in results[test_case].modules for k in m.keywords if k.status == "FAIL"]


def _logs(tmp_path):
    return "".join(p.read_text() for p in (tmp_path / "execution_output").glob("*.log"))


def test_the_pytest_runner_keeps_a_value_across_test_cases_too(run, device):
    run({"Store": [("Get Text", "${price_label}", "store_as=price")],
         "Use": [("Press Element", "${price}")]},
        {"TC1": ["Store"], "TC2": ["Use"]}, runner="pytest")

    assert device.calls[-1] == ("press_element", "$ 29.99")


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


def test_one_sessions_secret_does_not_mask_another_sessions_logs(run, tmp_path):
    run({"M": [("Get Text", "#user", "store_as=user_api_key")]}, {"TC": ["M"]})
    run({"M": [("Get Text", "#user", "store_as=user")]}, {"TC": ["M"]})

    assert _logs(tmp_path).count("Text of element: bob@example.com") == 1


def test_the_report_shows_what_each_step_stored_with_secrets_masked(run):
    results = run({"M": [("Get Text", "${price_label}", "store_as=price"),
                         ("Get Text", "#user", "store_as=user_api_key")]}, {"TC": ["M"]})

    stored = [k.stored for k in results["TC"].modules[0].keywords]
    assert stored == [{"price": "$ 29.99"}, {"user_api_key": "****"}]


def test_a_dry_run_resolves_stored_names_and_catches_the_two_mistakes(run, device):
    results = run({"Good": [("Get Text", "${price_label}", "store_as=price"), ("Press Element", "(//row)[${price}]")],
                   "Typo": [("Press Element", "(//row)[${pirce}]")],
                   "NoName": [("Get Text", "${price_label}", "store_as=")]},
                  {"TC1": ["Good"], "TC2": ["Typo"], "TC3": ["NoName"]}, dry=True)

    assert results["TC1"].status == "PASS"
    assert "'${pirce}'" in _reasons(results, "TC2")[0]
    assert "store_as needs a name" in _reasons(results, "TC3")[0]
    assert device.calls == []


def test_a_dry_run_counts_names_that_evaluate_and_read_data_define(run, tmp_path):
    (tmp_path / "people.csv").write_text("name,city\nalice,Pune\n")
    results = run({"M": [("Evaluate", "${total}", "1 + 1"), ("Read Data", "${city}", (tmp_path / "people.csv").as_posix(), "select=city"),
                         ("Press Element", "//row[@n='${total}' and @city='${city}']")]}, {"TC": ["M"]}, dry=True)

    assert results["TC"].status == "PASS"


def test_the_report_shows_a_heal_with_steps_that_name_the_suite_elements(run, device, tmp_path):
    device.heals["#title"] = {
        "summary": "AI self-heal recovered 'press_element' after 2 steps",
        "suggested_steps": [{"keyword": "scroll", "params": ["down"]}, {"keyword": "press_element", "params": ["#title"]}],
    }
    run({"M": [("Press Element", "${title_label}")]}, {"TC": ["M"]})

    report = ET.parse(tmp_path / "execution_output" / "junit_output.xml").getroot()
    [healed] = (kw for kw in report.iter("kw") if kw.get("name") == "Press Element")
    assert (healed.get("status"), healed.get("healed")) == ("PASS", "true")
    assert healed.find("heal").get("summary") == "AI self-heal recovered 'press_element' after 2 steps"
    assert [(s.get("keyword"), [a.text for a in s.iter("arg")]) for s in healed.iter("step")] == [
        ("scroll", ["down"]), ("press_element", ["${title_label}"])]



def _progress(out, mark):
    return [line for line in out.splitlines() if mark(line)]


def test_a_piped_run_prints_one_line_per_step_and_each_reason_once(run, device, capsys):
    device.heals["#title"] = {"summary": "AI self-heal recovered 'press_element' after 1 step", "suggested_steps": []}
    run({"Ok": [("Press Element", "${title_label}")], "Broken": [("Get Text", "#missing")]},
        {"Café": ["Ok"], "Broken case": ["Broken"]}, output=OutputMode.PLAIN)

    lines = _progress(capsys.readouterr().out, lambda line: re.match(r"\d\d:\d\d:\d\d (RUNNING|PASS|HEALED|FAIL) ", line))
    assert [re.sub(r" \(\d+\.\ds\)", "", line[9:]) for line in lines] == [
        'RUNNING  test case "Café"',
        'RUNNING    module "Ok"',
        'RUNNING      keyword "Press Element"',
        "HEALED       keyword \"Press Element\": AI self-heal recovered 'press_element' after 1 step",
        'PASS       module "Ok"',
        'PASS     test case "Café"',
        'RUNNING  test case "Broken case"',
        'RUNNING    module "Broken"',
        'RUNNING      keyword "Get Text"',
        'FAIL         keyword "Get Text": No element #missing [E0201]',
        'FAIL       module "Broken" [E0201]',
        'FAIL     test case "Broken case" [E0201]',
        'FAIL     run "Execution": Some test cases failed',
    ]


def test_a_jsonl_run_prints_each_event_as_one_ascii_json_line(run, device, capsys):
    device.heals["#title"] = {"summary": "healed", "suggested_steps": []}
    run({"Ok": [("Press Element", "${title_label}")]}, {"Café": ["Ok"]}, output=OutputMode.JSONL)

    lines = _progress(capsys.readouterr().out, lambda line: line.startswith("{"))
    events = [json.loads(line) for line in lines]
    assert all(line.isascii() for line in lines)
    assert [(e["entity_type"], e["name"], e["status"]) for e in events] == [
        ("test_case", "Café", "RUNNING"), ("module", "Ok", "RUNNING"), ("keyword", "Press Element", "RUNNING"),
        ("keyword", "Press Element", "PASS"), ("module", "Ok", "PASS"), ("test_case", "Café", "PASS"),
    ]
    assert events[3]["extra"]["healed"] == "true"


def test_the_pytest_runner_prints_its_steps_and_one_run_failure(run, device, capsys):
    with pytest.raises(OpticsError):
        run({"Ok": [("Press Element", "${title_label}")], "Broken": [("Get Text", "#missing")]},
            {"TC": ["Ok"], "TC2": ["Broken"]}, runner="pytest", output=OutputMode.PLAIN)
    out = capsys.readouterr().out
    assert 'PASS         keyword "Press Element"' in out
    assert len(re.findall(r"FAIL +run ", out)) == 1


@pytest.fixture
def error_screen(device, monkeypatch, tmp_path):
    monkeypatch.setitem(SCREEN, "#toast", "Payment failed, try again")
    monkeypatch.setattr(OpticsBuilder, "get_element_source", lambda self: InstanceFallback([device]))
    monkeypatch.setattr(OpticsBuilder, "get_driver", lambda self: InstanceFallback([device]))
    (tmp_path / "error_definitions.csv").write_text(
        "error_code,match_string,description,severity\nPAY_FAIL,payment failed,Payment was declined,high\n",
        encoding="utf-8")


def test_a_piped_run_says_it_ended_on_an_error_screen(run, error_screen, tmp_path, capsys):
    run({"Ok": [("Press Element", "${title_label}")]}, {"Café": ["Ok"]}, output=OutputMode.PLAIN)

    lines = _progress(capsys.readouterr().out, lambda line: re.match(r"\d\d:\d\d:\d\d (PASS|FAIL) ", line))
    assert [re.sub(r" \(\d+\.\ds\)", "", line[9:]) for line in lines] == [
        'PASS         keyword "Press Element"',
        'PASS       module "Ok"',
        'PASS     test case "Café"',
        'FAIL     test case "on-screen-error-detection": PAY_FAIL | high | payment failed | Payment was declined',
        'FAIL     run "Execution": Some test cases failed',
    ]
    suite = ET.parse(tmp_path / "execution_output" / "junit_output.xml").getroot().find("testsuite")
    assert [case.get("name") for case in suite.iter("testcase")] == ["Café", "on-screen-error-detection"]
    assert (suite.get("tests"), suite.get("failures")) == ("2", "1")


def test_a_jsonl_run_reports_the_error_screen_as_a_failed_test_case(run, error_screen, capsys):
    run({"Ok": [("Press Element", "${title_label}")]}, {"Café": ["Ok"]}, output=OutputMode.JSONL)

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    detection = [e for e in events if e["name"] == "on-screen-error-detection"]
    assert [(e["entity_type"], e["status"], e["message"]) for e in detection] == [
        ("test_case", "FAIL", "PAY_FAIL | high | payment failed | Payment was declined")]
