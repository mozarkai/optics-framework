"""Unit tests for the CSV/YAML CLI entry points (``optics_framework/helper/execute.py``).

Covers the exit-code contract of ``execute_main`` / ``dryrun_main`` (non-zero when
any returned test case did not PASS), the friendly pre-session gate that stops
the run when no driver is enabled in the project's ``config.yaml`` — before any
``SessionManager`` is constructed — and the execute-only environment preflight
that probes Selenium/Appium servers and attached Android devices before any
test runs. Heavy collaborators are patched; nothing real executes.
"""
from __future__ import annotations

import socket
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

def _tc(status):
    return SimpleNamespace(status=status)


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
    def test_all_pass_exits_zero(self):
        results = {"tc": _tc("PASS")}
        with patch.object(execute_module, "RunnerArgs", _StubArgs), \
                patch.object(execute_module, "ExecuteRunner", _runner_cls(results)):
            execute_main("proj")

    def test_any_fail_exits_nonzero(self):
        results = {"ok": _tc("PASS"), "bad": _tc("FAIL")}
        with patch.object(execute_module, "RunnerArgs", _StubArgs), \
                patch.object(execute_module, "ExecuteRunner", _runner_cls(results)):
            with pytest.raises(SystemExit) as exc:
                execute_main("proj")
        assert exc.value.code == 1

    def test_dry_run_any_fail_exits_nonzero(self):
        results = {"bad": _tc("FAIL")}
        with patch.object(execute_module, "RunnerArgs", _StubArgs), \
                patch.object(execute_module, "DryRunRunner", _runner_cls(results)):
            with pytest.raises(SystemExit) as exc:
                dryrun_main("proj")
        assert exc.value.code == 1

    def test_non_dict_result_never_exits(self):
        with patch.object(execute_module, "RunnerArgs", _StubArgs), \
                patch.object(execute_module, "ExecuteRunner", _runner_cls(None)):
            execute_main("proj")

    def test_empty_results_exit_zero(self):
        with patch.object(execute_module, "RunnerArgs", _StubArgs), \
                patch.object(execute_module, "DryRunRunner", _runner_cls({})):
            dryrun_main("proj")


# --------------------------------------------------------------------------- #
# No-enabled-driver gate                                                       #
# --------------------------------------------------------------------------- #

def _bare_runner(config):
    runner = BaseRunner.__new__(BaseRunner)
    runner.config = config
    return runner


class TestHasEnabledDriver:
    def test_true_when_any_driver_enabled(self):
        config = Config(driver_sources=[{
            "selenium": DependencyConfig(enabled=False),
            "appium": DependencyConfig(enabled=True),
        }])
        assert _bare_runner(config)._has_enabled_driver() is True

    def test_false_when_all_disabled(self):
        config = Config(driver_sources=[{
            "selenium": DependencyConfig(enabled=False),
        }])
        assert _bare_runner(config)._has_enabled_driver() is False


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
    def test_empty_test_cases_exits_with_guidance(self, tmp_path, capsys):
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
    def test_gate_prints_guidance_and_skips_session(self, tmp_path, capsys, main):
        _make_project(tmp_path, _ALL_DISABLED_CONFIG)
        with patch.object(execute_module, "SessionManager") as session_manager:
            with pytest.raises(SystemExit) as exc:
                main(str(tmp_path))
        assert exc.value.code == 1
        session_manager.assert_not_called()
        out = capsys.readouterr().out
        assert f"No driver enabled in {tmp_path / 'config.yaml'}" in out
        assert f"optics configure {tmp_path}" in out
        assert "Unexpected error" not in out



def _config_for(driver: str, **dep_kwargs) -> Config:
    return Config(driver_sources=[{driver: DependencyConfig(enabled=True, **dep_kwargs)}])


class TestProbeTcp:
    def test_open_port_returns_true(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            assert execute_module._probe_tcp(
                f"http://127.0.0.1:{port}", default_port=4723) is True
        finally:
            server.close()

    def test_default_port_used_when_url_has_no_scheme_or_port(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            assert execute_module._probe_tcp(
                "//127.0.0.1", default_port=port) is True
        finally:
            server.close()

    def test_closed_port_returns_false(self):
        assert execute_module._probe_tcp(
            "http://127.0.0.1:1", default_port=4723, timeout=0.5) is False

    @pytest.mark.parametrize("url", [None, "", "   ", "http://:4723/wd/hub"])
    def test_missing_or_unparseable_url_returns_false(self, url):
        assert execute_module._probe_tcp(url, default_port=4723) is False

    @pytest.mark.parametrize("url, expected_port", [
        ("https://appium-hub.example.com:8443/wd/hub", 8443),
        ("http://appium-hub.example.com:4723/wd/hub", 4723),
        ("https://appium-hub.example.com/wd/hub", 443),
        ("http://appium-hub.example.com/wd/hub", 80),
        ("//appium-hub.example.com", 4723),
    ])
    def test_port_comes_from_url_then_scheme_then_default(self, url, expected_port):
        with patch.object(execute_module.socket, "create_connection") as connect:
            assert execute_module._probe_tcp(url, default_port=4723) is True
        assert connect.call_args.args[0] == ("appium-hub.example.com", expected_port)


def _fake_adb_run(stdout):
    def _run(*args, **kwargs):
        return SimpleNamespace(stdout=stdout)
    return _run


class TestAdbDeviceCount:
    def test_none_when_adb_not_installed(self):
        with patch.object(execute_module.shutil, "which", return_value=None):
            assert execute_module._adb_device_count() is None

    def test_counts_only_device_state_lines(self):
        stdout = ("* daemon not running; starting now *\n"
                  "List of devices attached\n"
                  "emulator-5554\tdevice\n"
                  "abc123\tdevice\n")
        with patch.object(execute_module.shutil, "which", return_value="/usr/bin/adb"), \
                patch.object(execute_module.subprocess, "run", _fake_adb_run(stdout)):
            assert execute_module._adb_device_count() == 2

    def test_ignores_offline_entries(self):
        stdout = "List of devices attached\nemulator-5554\toffline\n"
        with patch.object(execute_module.shutil, "which", return_value="/usr/bin/adb"), \
                patch.object(execute_module.subprocess, "run", _fake_adb_run(stdout)):
            assert execute_module._adb_device_count() == 0

    def test_headerless_output_is_zero_not_a_device(self):
        stdout = "error: device not found\n"
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
    def test_playwright_skips_silently_without_probes(self):
        config = _config_for("playwright")
        with patch.object(execute_module, "_probe_tcp") as probe, \
                patch.object(execute_module, "_adb_device_count") as adb:
            execute_module._preflight_or_exit(config)
        probe.assert_not_called()
        adb.assert_not_called()

    def test_appium_unreachable_aborts_with_fix(self, capsys):
        config = _config_for("appium", url="http://127.0.0.1:9999")
        with patch.object(execute_module, "_probe_tcp", return_value=False):
            with pytest.raises(SystemExit) as exc:
                execute_module._preflight_or_exit(config, folder_path="/proj")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "No Appium server reachable at http://127.0.0.1:9999" in err
        assert "appium" in err
        assert "optics execute /proj" in err

    def test_appium_reachable_non_android_passes_without_adb_check(self):
        config = _config_for("appium", url="http://127.0.0.1:4723",
                             capabilities={"platformName": "iOS"})
        with patch.object(execute_module, "_probe_tcp", return_value=True), \
                patch.object(execute_module, "_adb_device_count") as adb:
            execute_module._preflight_or_exit(config)
        adb.assert_not_called()

    def test_android_with_attached_device_passes(self):
        config = _config_for("appium", capabilities={"platformName": "Android"})
        with patch.object(execute_module, "_probe_tcp", return_value=True), \
                patch.object(execute_module, "_adb_device_count", return_value=1):
            execute_module._preflight_or_exit(config)

    def test_android_without_devices_aborts(self, capsys):
        config = _config_for("appium", capabilities={"platformName": "Android"})
        with patch.object(execute_module, "_probe_tcp", return_value=True), \
                patch.object(execute_module, "_adb_device_count", return_value=0):
            with pytest.raises(SystemExit) as exc:
                execute_module._preflight_or_exit(config)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "No Android device/emulator attached" in err
        assert "adb devices" in err

    def test_android_without_adb_aborts(self, capsys):
        config = _config_for("appium", capabilities={"platformName": "android"})
        with patch.object(execute_module, "_probe_tcp", return_value=True), \
                patch.object(execute_module, "_adb_device_count", return_value=None):
            with pytest.raises(SystemExit) as exc:
                execute_module._preflight_or_exit(config)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "adb" in err
        assert "platform-tools" in err

    def test_appium_default_url_used_when_config_url_missing(self, capsys):
        config = _config_for("appium")
        seen = {}

        def _fake_probe(url, default_port, timeout=2.0):
            seen["url"] = url
            return False

        with patch.object(execute_module, "_probe_tcp", side_effect=_fake_probe):
            with pytest.raises(SystemExit):
                execute_module._preflight_or_exit(config)
        assert seen["url"] == "http://127.0.0.1:4723"

    def test_selenium_unreachable_aborts_with_fix(self, capsys):
        config = _config_for("selenium", url="http://localhost:4444/wd/hub")
        with patch.object(execute_module, "_probe_tcp", return_value=False):
            with pytest.raises(SystemExit) as exc:
                execute_module._preflight_or_exit(config)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "No Selenium/WebDriver server reachable at http://localhost:4444/wd/hub" in err

    def test_selenium_reachable_passes(self):
        config = _config_for("selenium")
        with patch.object(execute_module, "_probe_tcp", return_value=True):
            execute_module._preflight_or_exit(config)

    @pytest.mark.parametrize("value", ["1", "true", "TRUE"])
    def test_skip_env_bypasses_every_probe(self, monkeypatch, value):
        monkeypatch.setenv("OPTICS_SKIP_PREFLIGHT", value)
        config = _config_for("appium", capabilities={"platformName": "Android"})
        with patch.object(execute_module, "_probe_tcp") as probe, \
                patch.object(execute_module, "_adb_device_count") as adb:
            execute_module._preflight_or_exit(config)
        probe.assert_not_called()
        adb.assert_not_called()

    def test_no_enabled_driver_is_left_to_the_existing_gate(self):
        config = Config(driver_sources=[{"selenium": DependencyConfig(enabled=False)}])
        with patch.object(execute_module, "_probe_tcp") as probe:
            execute_module._preflight_or_exit(config)
        probe.assert_not_called()

    def test_none_config_steps_aside(self):
        execute_module._preflight_or_exit(None)


class TestPreflightWiring:
    def test_execute_main_preflights_before_running(self):
        order = []
        sentinel_config = object()

        class _RunnerWithConfig:
            def __init__(self, args):
                self.config = sentinel_config

            async def execute(self):
                order.append("execute")
                return {}

        def _spy_preflight(config, folder_path="<folder>"):
            assert config is sentinel_config
            order.append("preflight")

        with patch.object(execute_module, "RunnerArgs", _StubArgs), \
                patch.object(execute_module, "ExecuteRunner", _RunnerWithConfig), \
                patch.object(execute_module, "_preflight_or_exit",
                             side_effect=_spy_preflight):
            execute_main("proj")
        assert order == ["preflight", "execute"]

    def test_dry_run_never_preflights(self):
        with patch.object(execute_module, "_preflight_or_exit") as preflight, \
                patch.object(execute_module, "RunnerArgs", _StubArgs), \
                patch.object(execute_module, "DryRunRunner", _runner_cls({})):
            dryrun_main("proj")
        preflight.assert_not_called()
