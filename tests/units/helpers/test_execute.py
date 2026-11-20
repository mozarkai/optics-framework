from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.helper import execute as execute_module


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


class TestNoEnabledDriverGate:

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

    @pytest.mark.parametrize("url", [None, "", "   ", "http://:4723/wd/hub"])
    def test_missing_or_unparseable_url_returns_false(self, url):
        assert execute_module._probe_tcp(url, default_port=4723) is False


def _fake_adb_run(stdout):
    def _run(*args, **kwargs):
        return SimpleNamespace(stdout=stdout)
    return _run


class TestAdbDeviceCount:

    def test_ignores_offline_entries(self):
        stdout = "List of devices attached\nemulator-5554\toffline\n"
        with patch.object(execute_module.shutil, "which", return_value="/usr/bin/adb"), \
                patch.object(execute_module.subprocess, "run", _fake_adb_run(stdout)):
            assert execute_module._adb_device_count() == 0


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
