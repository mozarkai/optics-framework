"""optics dry_run on a real project folder: which test cases run, in what order, and how a bad selection exits."""
import subprocess  # nosec B404
import sys
import xml.etree.ElementTree as ET  # nosec B405

import pytest

pytest.importorskip("playwright", reason="the project uses the playwright driver")

pytestmark = pytest.mark.black_box

TEST_CASES = """test_case,test_step
Suite Setup,Wait
Setup,Wait
Login Test,Wait
Checkout Test,Wait
Refund Check,Wait
Teardown,Wait
Suite Teardown,Wait
"""
CONFIG = """driver_sources:
  - playwright:
      enabled: true
      capabilities:
        headless: true
elements_sources:
  - playwright_find_element:
      enabled: true
"""


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "shop_suite"
    folder.mkdir()
    (folder / "test_cases.csv").write_text(TEST_CASES)
    (folder / "modules.csv").write_text("module_name,module_step,param_1\nWait,Sleep,0\n")
    (folder / "config.yaml").write_text(CONFIG)
    return folder


def dry_run(project, *selection, config_selection=""):
    if config_selection:
        (project / "config.yaml").write_text(CONFIG + config_selection)
    return subprocess.run(  # nosec B603
        [sys.executable, "-m", "optics_framework.helper.cli", "dry_run", str(project), "--no-use-printer", *selection],
        capture_output=True, text=True, timeout=120,
    )


def reported(project):
    suite = ET.parse(project / "execution_output" / "junit_output.xml").getroot().find("testsuite")  # nosec B314
    return [(case.get("name"), case.get("classname")) for case in suite.iter("testcase")]


def wrapped(*tests):
    names = [name for test in tests for name in (f"Setup ({test})", test, f"Teardown ({test})")]
    return ["Suite Setup", *names, "Suite Teardown"]


def test_setup_and_teardown_run_around_every_test(project):
    assert dry_run(project).returncode == 0
    assert reported(project) == [(name, "shop_suite") for name in wrapped("Login Test", "Checkout Test", "Refund Check")]


@pytest.mark.parametrize("selection, expected", [
    (["--include", "login TEST"], ["Login Test"]),
    (["--include", "*test"], ["Login Test", "Checkout Test"]),
    (["--include", "*test", "--exclude", "Login*"], ["Checkout Test"]),
    (["--exclude", "Refund ?heck"], ["Login Test", "Checkout Test"]),
    (["--include", "Login Test", "--include", "Refund Check"], ["Login Test", "Refund Check"]),
])
def test_selection_keeps_setup_and_teardown_around_the_selected_tests(project, selection, expected):
    assert dry_run(project, *selection).returncode == 0
    assert [name for name, _ in reported(project)] == wrapped(*expected)


def test_flags_replace_the_selection_in_config_yaml(project):
    config_selection = "include:\n  - Refund Check\n"
    assert dry_run(project, config_selection=config_selection).returncode == 0
    assert [name for name, _ in reported(project)] == wrapped("Refund Check")
    assert dry_run(project, "--include", "Login Test", config_selection=config_selection).returncode == 0
    assert [name for name, _ in reported(project)] == wrapped("Login Test")


@pytest.mark.parametrize("selection, reason", [
    (["--include", "Logn Test"], "Did you mean 'Login Test'?"),
    (["--exclude", "*"], "leaves no test cases to run"),
])
def test_a_bad_selection_exits_3_before_a_session_starts(project, selection, reason):
    result = dry_run(project, *selection)
    assert result.returncode == 3
    assert "E0702" in result.stderr
    assert reason in result.stderr
    assert not (project / "execution_output").exists()
