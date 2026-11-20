from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

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


_APPIUM_CONFIG = """\
driver_sources:
  - appium:
      enabled: true
      url: http://127.0.0.1:1
      capabilities:
        platformName: Android
elements_sources:
  - appium_find_element:
      enabled: true
      capabilities: {}
"""
_RULE = {"name": "Rate Us", "detect": {"text_contains": ["rate us"]}, "action": {"type": "tap", "target": "Later"}}
_SWIPE = {**_RULE, "action": {"type": "swipe", "target": "Later"}}


class TestInvalidPopupSettingsStopTheRunBeforeAnySession:

    @pytest.mark.parametrize("config, rules_file, expected", [
        ({"popup_rules": [_SWIPE]}, None, ["popup_rules['Rate Us'].action.type", "'swipe'"]),
        ({"popup_rules": [{k: v for k, v in _RULE.items() if k != "detect"}]}, None,
         ["popup_rules['Rate Us'].detect", "Field required"]),
        ({"popup_rules": [{**_RULE, "check": "sometimes"}]}, None, ["popup_rules['Rate Us'].check", "'sometimes'"]),
        ({"permissions": {"camera": "yes_please"}}, None, ["permissions.camera", "'yes_please'"]),
        ({"permissions": {"telepathy": "allow"}}, None, ["Unknown permission 'telepathy'"]),
        ({}, [_SWIPE], ["popup_rules.json", "popup_rules['Rate Us'].action.type", "'swipe'"]),
    ], ids=["unknown action", "missing detect", "wrong check", "permission value", "permission name", "rules file"])
    @pytest.mark.parametrize("entry", [execute_module.run_project, execute_module.dryrun_main], ids=["execute", "dry_run"])
    def test_the_error_names_the_bad_field_and_value(self, tmp_path, monkeypatch, capsys, entry, config, rules_file, expected):
        monkeypatch.setenv("COLUMNS", "400")
        _make_project(tmp_path, _APPIUM_CONFIG + (yaml.safe_dump(config) if config else ""))
        if rules_file is not None:
            (tmp_path / "popup_rules.json").write_text(json.dumps(rules_file))
        with pytest.raises(SystemExit) as stopped:
            entry(str(tmp_path), use_printer=False)
        shown = capsys.readouterr().err
        assert stopped.value.code == 1
        assert "Cannot start: E0501" in shown
        for text in expected:
            assert text in shown


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
