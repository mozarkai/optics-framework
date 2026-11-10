import os
import signal
import socket
import subprocess  # nosec B404
import sys
import threading
import xml.etree.ElementTree as ET  # nosec B405
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from optics_framework.helper.execute import run_project

_CONFIG = """\
driver_sources:
  - selenium:
      enabled: false
      capabilities: {}
elements_sources:
  - selenium_find_element:
      enabled: false
      capabilities: {}
"""

_MODULES = (
    "module_name,module_step,param_1,param_2\n"
    "Pass,Evaluate,${total},1 + 1\n"
    "Break,Not A Keyword,,\n"
)


def _project(tmp_path, test_cases: str, config: str = _CONFIG, modules: str = _MODULES) -> None:
    (tmp_path / "test_cases").mkdir()
    (tmp_path / "modules").mkdir()
    (tmp_path / "config.yaml").write_text(config)
    (tmp_path / "modules" / "modules.csv").write_text(modules)
    (tmp_path / "test_cases" / "test_cases.csv").write_text("test_case,test_step\n" + test_cases)


def _report(tmp_path) -> tuple[dict[str, ET.Element], ET.Element]:
    suite = ET.parse(tmp_path / "execution_output" / "junit_output.xml").getroot().find("testsuite")  # nosec B314
    assert suite is not None
    return {case.get("name"): case for case in suite.findall("testcase")}, suite


def _run(tmp_path, test_cases: str) -> tuple[bool, dict[str, ET.Element], ET.Element]:
    _project(tmp_path, test_cases)
    passed = run_project(str(tmp_path), use_printer=False)
    return passed, *_report(tmp_path)


def _run_with(runner: str, tmp_path, test_cases: str) -> tuple[bool, dict[str, ET.Element], ET.Element]:
    _project(tmp_path, test_cases)
    passed = _execute(tmp_path, "--runner", runner).wait(120) == 0
    return passed, *_report(tmp_path)


_RUNNERS = pytest.mark.parametrize("runner", ["test_runner", "pytest"])


@_RUNNERS
def test_failed_suite_setup_skips_every_test_but_runs_the_suite_teardown(tmp_path, runner):
    passed, cases, suite = _run_with(runner, tmp_path, (
        "Suite Setup,Break\n"
        "Test Setup,Pass\n"
        "TC One,Pass\n"
        "Test Teardown,Pass\n"
        "Suite Teardown,Pass\n"
    ))
    assert passed is False
    setup = cases["Suite Setup"]
    assert (setup.get("status"), setup.get("kind")) == ("FAIL", "suite_setup")
    failure = setup.find("failure")
    assert failure is not None
    assert failure.get("code") == "E0402"
    assert failure.get("message", "").startswith("Code.E0402: Keyword not found: Not A Keyword.")
    assert setup.find("kw/kw").get("code") == "E0402"
    for name, kind in [("Test Setup (TC One)", "setup"), ("TC One", "test"), ("Test Teardown (TC One)", "teardown")]:
        case = cases[name]
        assert (case.get("status"), case.get("kind")) == ("NOT_RUN", kind)
        assert case.find("skipped").get("message", "").startswith("Suite setup failed: Code.E0402: Keyword not found")
        assert [kw.get("status") for kw in case.iter("kw")] == ["NOT_RUN", "NOT_RUN"]
    assert (cases["Suite Teardown"].get("status"), cases["Suite Teardown"].get("kind")) == ("PASS", "suite_teardown")
    assert (suite.get("tests"), suite.get("failures"), suite.get("skipped")) == ("5", "1", "3")


@_RUNNERS
def test_failed_test_setup_skips_only_its_test_and_still_runs_its_teardown(tmp_path, runner):
    passed, cases, _ = _run_with(runner, tmp_path, (
        "Test Setup,Break\n"
        "TC One,Pass\n"
        "TC Two,Pass\n"
        "Test Teardown,Pass\n"
    ))
    assert passed is False
    for test in ("TC One", "TC Two"):
        assert cases[f"Test Setup ({test})"].get("status") == "FAIL"
        assert cases[test].get("status") == "NOT_RUN"
        assert cases[test].find("skipped").get("message", "").startswith("Setup failed: Code.E0402:")
        assert cases[f"Test Teardown ({test})"].get("status") == "PASS"


def test_only_a_name_ending_in_setup_or_teardown_wraps_the_tests(tmp_path):
    passed, cases, _ = _run(tmp_path, (
        "Web Suite Setup,Pass\n"
        "Login teardown check,Pass\n"
        "Setup wizard opens,Pass\n"
        "Web Suite Teardown,Pass\n"
    ))
    assert passed is True
    assert [(name, case.get("kind")) for name, case in cases.items()] == [
        ("Web Suite Setup", "suite_setup"),
        ("Login teardown check", "test"),
        ("Setup wizard opens", "test"),
        ("Web Suite Teardown", "suite_teardown"),
    ]


@pytest.mark.parametrize("test_cases, kind", [
    ("Suite Setup,Pass\nWeb Suite Setup,Pass\nTC One,Pass\n", "suite setup"),
    ("Login Setup,Pass\nCart Setup,Pass\nTC One,Pass\n", "test setup"),
])
def test_two_setups_of_one_kind_stop_the_run(tmp_path, capsys, monkeypatch, test_cases, kind):
    monkeypatch.setenv("COLUMNS", "300")
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, test_cases)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Cannot start: E0503" in err
    assert f"more than one {kind}" in err


def _execute(tmp_path, *args: str) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if k != "OPTICS_SKIP_PREFLIGHT"}
    return subprocess.Popen(  # nosec B603
        [sys.executable, "-m", "optics_framework.helper.cli", "execute", str(tmp_path), *args],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )


class _Hang(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.called.set()
        self.server.release.wait(30)

    def log_message(self, *args):
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGINT to send to a child process")
@pytest.mark.parametrize("sig, exit_code", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_a_stopped_run_gives_the_running_test_case_e0706(tmp_path, sig, exit_code):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Hang)
    server.called, server.release = threading.Event(), threading.Event()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        (tmp_path / "apis.yaml").write_text(
            "api:\n  collections:\n    slow:\n      name: Slow\n"
            f"      base_url: http://127.0.0.1:{server.server_port}\n"
            "      apis:\n        hang:\n          name: Hang\n          endpoint: /hang\n"
            "          request:\n            method: GET\n"
        )
        _project(tmp_path, "TC Hang,Call Slow API\nSuite Teardown,Pass\n",
                 modules=_MODULES + "Call Slow API,Invoke API,slow.hang,\n")
        run = _execute(tmp_path)
        assert server.called.wait(60), "the run never reached Invoke API"
        run.send_signal(sig)
        assert run.wait(60) == exit_code
    finally:
        server.release.set()
        server.shutdown()
    cases, _ = _report(tmp_path)
    hang = cases["TC Hang"]
    assert hang.get("status") == "ERROR"
    assert hang.find("error").get("code") == "E0706"
    assert hang.find("kw/kw").get("code") == "E0706"
    assert cases["Suite Teardown"].get("status") == "NOT_RUN"


def test_a_run_that_cannot_start_gives_each_test_case_its_code(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    _project(tmp_path, "TC One,Pass\n", config=(
        "driver_sources:\n  - appium:\n      enabled: true\n"
        f"      url: http://127.0.0.1:{closed_port}\n"
        "      capabilities:\n        platformName: iOS\n"
        "elements_sources:\n  - appium_find_element:\n      enabled: true\n      capabilities: {}\n"
    ))
    assert _execute(tmp_path).wait(60) == 1
    cases, _ = _report(tmp_path)
    assert cases["TC One"].get("status") == "ERROR"
    assert cases["TC One"].find("error").get("code") == "E0102"
