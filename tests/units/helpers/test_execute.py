"""Unit tests for the CSV/YAML CLI entry points (``optics_framework/helper/execute.py``).

Covers the exit-code contract of ``execute_main`` / ``dryrun_main`` (non-zero when
any returned test case did not PASS), the friendly pre-session gate that stops
a run needing a device when no driver is enabled in the project's ``config.yaml``
— before any ``SessionManager`` is constructed — and the execute-only environment
preflight that probes Selenium/Appium servers and attached Android devices before
any test runs. Heavy collaborators are patched, except in the driver-free run.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.helper import execute as execute_module
from optics_framework.helper.execute import (
    BaseRunner,
    execute_main,
    dryrun_main,
)

pytestmark = pytest.mark.white_box


# --------------------------------------------------------------------------- #
# Exit-code mapping                                                            #
# --------------------------------------------------------------------------- #


class _StubArgs:
    def __init__(self, **kwargs):
        pass


def _runner_cls(results):
    class _StubRunner:
        def __init__(self, args):
            pass

        async def execute(self):
            return results
    return _StubRunner


class TestExitCodeMapping:


    def test_empty_results_exit_zero(self):
        with patch.object(execute_module, "RunnerArgs", _StubArgs), \
                patch.object(execute_module, "DryRunRunner", _runner_cls({})):
            dryrun_main("proj")


# --------------------------------------------------------------------------- #
# No-enabled-driver gate                                                       #
# --------------------------------------------------------------------------- #


def _make_project(tmp_path, config_text):
    (tmp_path / "test_cases").mkdir()
    (tmp_path / "modules").mkdir()
    (tmp_path / "test_cases" / "test_cases.csv").write_text(
        "test_case,test_step\nTC One,Launch App\n"
    )
    (tmp_path / "modules" / "modules.csv").write_text(
        "module_name,module_step\nLaunch App,Launch App,\n"
    )
    (tmp_path / "config.yaml").write_text(config_text)


_ALL_DISABLED_CONFIG = """\
driver_sources:
  - selenium:
      enabled: false
      capabilities: {}
elements_sources:
  - selenium_find_element:
      enabled: false
      capabilities: {}
"""


class TestEmptyProjectGuard:
    def test_empty_test_cases_exits_with_guidance(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        runner = BaseRunner.__new__(BaseRunner)
        runner.folder_path = str(tmp_path)
        runner.test_cases_data = {}
        with pytest.raises(SystemExit) as exc:
            runner._filter_and_build_execution_queue()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "No test cases to run" in err
        assert "optics init" in err
        assert "Unexpected error" not in err
        assert "linked list" not in err


class TestNoEnabledDriverGate:
    @pytest.mark.parametrize("main", [execute_main, dryrun_main])
    def test_gate_prints_guidance_and_skips_session(self, tmp_path, capsys, monkeypatch, main):
        monkeypatch.setenv("COLUMNS", "300")
        _make_project(tmp_path, _ALL_DISABLED_CONFIG)
        with patch.object(execute_module, "SessionManager") as session_manager:
            with pytest.raises(SystemExit) as exc:
                main(str(tmp_path))
        assert exc.value.code == 1
        session_manager.assert_not_called()
        err = capsys.readouterr().err
        assert "Cannot start: E0104" in err
        assert f"No driver enabled in {tmp_path / 'config.yaml'}" in err
        assert f"optics configure {tmp_path}" in err
        assert "Unexpected error" not in err

    def test_driver_free_suite_runs_without_a_driver(self, tmp_path):
        _make_project(tmp_path, _ALL_DISABLED_CONFIG)
        (tmp_path / "test_cases" / "test_cases.csv").write_text(
            "test_case,test_step\nTC One,Compute Total\n"
        )
        (tmp_path / "modules" / "modules.csv").write_text(
            "module_name,module_step,param_1,param_2\n"
            "Compute Total,Evaluate,total,1 + 1\n"
        )
        assert execute_module.run_project(str(tmp_path), use_printer=False) is True


def _config_for(driver: str, **dep_kwargs) -> Config:
    return Config(driver_sources=[{driver: DependencyConfig(enabled=True, **dep_kwargs)}])


class TestProbeTcp:


    def test_closed_port_returns_false(self):
        assert execute_module._probe_tcp(
            "http://127.0.0.1:1", default_port=4723, timeout=0.5) is False

    @pytest.mark.parametrize("url", [None, "", "   ", "http://:4723/wd/hub"])
    def test_missing_or_unparseable_url_returns_false(self, url):
        assert execute_module._probe_tcp(url, default_port=4723) is False


def _fake_adb_run(stdout):
    def _run(*args, **kwargs):
        return SimpleNamespace(stdout=stdout)
    return _run


class TestAdbDeviceCount:
    def test_none_when_adb_not_installed(self):
        with patch.object(execute_module.shutil, "which", return_value=None):
            assert execute_module._adb_device_count() is None

    def test_ignores_offline_entries(self):
        stdout = "List of devices attached\nemulator-5554\toffline\n"
        with patch.object(execute_module.shutil, "which", return_value="/usr/bin/adb"), \
                patch.object(execute_module.subprocess, "run", _fake_adb_run(stdout)):
            assert execute_module._adb_device_count() == 0

    def test_none_when_adb_cannot_execute(self):
        def _boom(*args, **kwargs):
            raise OSError("no exec")
        with patch.object(execute_module.shutil, "which", return_value="/usr/bin/adb"), \
                patch.object(execute_module.subprocess, "run", _boom):
            assert execute_module._adb_device_count() is None


class TestPreflightGate:

    def test_appium_unreachable_names_the_fix(self):
        config = _config_for("appium", url="http://127.0.0.1:9999")
        with patch.object(execute_module, "_probe_tcp", return_value=False):
            problem = execute_module._preflight(config, folder_path="/proj")
        assert problem.reason == "E0102: No Appium server reachable at http://127.0.0.1:9999."
        assert "Start one in another terminal:  appium" in problem.lines
        assert "Then re-run:  optics execute /proj" in problem.lines

    def test_android_without_devices_is_a_problem(self):
        config = _config_for("appium", capabilities={"platformName": "Android"})
        with patch.object(execute_module, "_probe_tcp", return_value=True), \
                patch.object(execute_module, "_adb_device_count", return_value=0):
            problem = execute_module._preflight(config)
        assert problem.reason == "E0102: No Android device/emulator attached (adb reports none)."
        assert "then check:  adb devices" in problem.lines

    def test_android_without_adb_is_a_problem(self):
        config = _config_for("appium", capabilities={"platformName": "android"})
        with patch.object(execute_module, "_probe_tcp", return_value=True), \
                patch.object(execute_module, "_adb_device_count", return_value=None):
            problem = execute_module._preflight(config)
        assert problem.reason == "E0102: Android run requested, but the adb tool was not found."
        assert any("platform-tools" in line for line in problem.lines)

    def test_selenium_unreachable_names_the_server(self):
        config = _config_for("selenium", url="http://localhost:4444/wd/hub")
        with patch.object(execute_module, "_probe_tcp", return_value=False):
            problem = execute_module._preflight(config)
        assert problem.reason == "E0102: No Selenium/WebDriver server reachable at http://localhost:4444/wd/hub."
