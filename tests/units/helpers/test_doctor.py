"""Unit tests for ``optics doctor`` (``optics_framework/helper/doctor.py``).

Environment probes are exercised through their seams — shutil.which, the
metadata ``version`` lookup, subprocess.run and the socket — so the suite never
needs adb, Appium or a browser installed. Project validation runs against real
config.yaml files on disk and doubles as a guard against doctor mutating the
project it inspects (no execution_output/ may appear).
"""
from __future__ import annotations

import io
import os
import subprocess
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from optics_framework.helper import doctor, project_config
from optics_framework.helper.doctor import Check
from optics_framework.helper.setup import ALL_ENGINES, DISTRIBUTION_NAME

pytestmark = pytest.mark.white_box

MODULE = "optics_framework.helper.doctor"

# The real header `adb devices` prints. Tests must quote adb verbatim —
# paraphrasing it here is exactly how the header-matching bug slipped through.
ADB_HEADER = "List of devices attached"

ANDROID_ANSWERS = {
    "platform": "android",
    "device_name": "emu-1",
    "platform_name": "Android",
    "app_package": "com.example.app",
    "app_activity": "com.example.MainActivity",
    "appium_url": "http://127.0.0.1:4723",
    "ocr": False,
    "log_level": "INFO",
}

IOS_ANSWERS = {
    "platform": "ios",
    "device_name": "iPhone 15",
    "platform_name": "iOS",
    "bundle_id": "com.example.app",
    "appium_url": "http://127.0.0.1:4723",
    "ocr": False,
    "log_level": "INFO",
}


def _render(func, *args, **kwargs):
    """Run a reporting function with a buffered console; returns (result, text)."""
    buf = io.StringIO()
    with patch(f"{MODULE}._console", Console(file=buf, width=200)):
        result = func(*args, **kwargs)
    return result, buf.getvalue()


def _write_config(tmp_path, answers=None, raw=None) -> str:
    folder = str(tmp_path / "demo")
    os.makedirs(folder, exist_ok=True)
    text = raw if raw is not None else project_config.render_project_config(
        answers if answers is not None else ANDROID_ANSWERS)
    with open(os.path.join(folder, "config.yaml"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return folder


# --------------------------------------------------------------------------- #
# check_core                                                                   #
# --------------------------------------------------------------------------- #

class TestCheckCore:
    def test_reports_installed_version(self):
        with patch(f"{MODULE}.version", return_value="9.9.9"):
            rows = doctor.check_core()
        optics_row = next(r for r in rows if r.name == DISTRIBUTION_NAME)
        assert optics_row.status == "ok"
        assert "9.9.9" in optics_row.detail

    def test_missing_package_is_warn_not_fail(self):
        with patch(f"{MODULE}.version", side_effect=PackageNotFoundError):
            rows = doctor.check_core()
        optics_row = rows[-1]
        assert optics_row.status == "warn"
        assert "pip install" in optics_row.hint

    def test_python_row_never_fails(self):
        with patch(f"{MODULE}.version", side_effect=PackageNotFoundError):
            rows = doctor.check_core()
        python_row = rows[0]
        assert python_row.name == "python"
        assert python_row.status == "ok"


# --------------------------------------------------------------------------- #
# check_engines                                                                #
# --------------------------------------------------------------------------- #

class TestCheckEngines:
    def test_one_row_per_engine(self):
        with patch(f"{MODULE}.version", side_effect=PackageNotFoundError):
            rows = doctor.check_engines()
        assert sorted(r.name for r in rows) == sorted(e.name for e in ALL_ENGINES.values())

    def test_missing_engine_warns_with_install_hint(self):
        with patch(f"{MODULE}.version", side_effect=PackageNotFoundError):
            rows = doctor.check_engines()
        appium = next(r for r in rows if r.name == "Appium")
        assert appium.status == "warn"
        assert appium.hint == "optics setup --install appium"

    def test_installed_engine_reports_package_and_version(self):
        def fake_version(package):
            if package == "appium-python-client":
                return "5.0.0"
            raise PackageNotFoundError(package)

        with patch(f"{MODULE}.version", side_effect=fake_version):
            rows = doctor.check_engines()
        appium = next(r for r in rows if r.name == "Appium")
        assert appium.status == "ok"
        assert "appium-python-client 5.0.0" in appium.detail


# --------------------------------------------------------------------------- #
# check_mobile                                                                 #
# --------------------------------------------------------------------------- #

@pytest.fixture
def no_socket(monkeypatch):
    """Simulate "nothing listening": any connect attempt raises OSError."""
    def refuse(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(doctor.socket, "create_connection", refuse)


class TestCheckMobile:
    def test_no_adb_warns(self, no_socket):
        with patch(f"{MODULE}.shutil.which", return_value=None):
            rows = doctor.check_mobile()
        assert [r.name for r in rows] == ["adb", "appium server"]
        assert rows[0].status == "warn"
        assert rows[1].status == "warn"

    def test_connected_devices_are_parsed(self, no_socket, monkeypatch):
        monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/adb")
        out = ("* daemon not running; starting now at tcp:5037\n"
               "* daemon started successfully\n"
               f"{ADB_HEADER}\n"
               "emu-5554\tdevice\ndeadbeef\toffline\nx\tunauthorized\n")
        stub = SimpleNamespace(stdout=out)
        with patch.object(doctor.subprocess, "run", return_value=stub) as run, \
                patch.object(doctor.socket, "create_connection"):
            rows = doctor.check_mobile()
        devices_row = next(r for r in rows if r.name == "adb devices")
        assert devices_row.status == "ok"
        assert "emu-5554" in devices_row.detail
        assert "deadbeef" not in devices_row.detail
        assert run.call_args.kwargs["shell"] is False

    def test_no_devices_warns(self, no_socket, monkeypatch):
        monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/adb")
        stub = SimpleNamespace(stdout=f"{ADB_HEADER}\n\n")
        with patch.object(doctor.subprocess, "run", return_value=stub), \
                patch.object(doctor.socket, "create_connection"):
            rows = doctor.check_mobile()
        row = next(r for r in rows if r.name == "adb devices")
        assert row.status == "warn"
        assert "no devices attached" in row.detail

    def test_output_without_header_is_not_mistaken_for_zero_devices(
            self, no_socket, monkeypatch):
        # An adb error banner has no device-list header; reporting the serial
        # count would be wrong either way — surface the unexpected output.
        monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/adb")
        stub = SimpleNamespace(stdout="error: no devices/emulators found")
        with patch.object(doctor.subprocess, "run", return_value=stub), \
                patch.object(doctor.socket, "create_connection"):
            rows = doctor.check_mobile()
        row = next(r for r in rows if r.name == "adb devices")
        assert row.status == "warn"
        assert "unexpected adb output" in row.detail
        assert "no devices attached" not in row.detail

    def test_adb_failure_degrades_to_warning(self, no_socket, monkeypatch):
        monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/adb")
        with patch.object(doctor.subprocess, "run",
                          side_effect=subprocess.SubprocessError("kaboom")):
            rows = doctor.check_mobile()
        assert next(r for r in rows if r.name == "adb devices").status == "warn"

    def test_appium_reachable_is_ok(self, monkeypatch):
        monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
        with patch.object(doctor.socket, "create_connection") as connect:
            rows = doctor.check_mobile("10.0.0.5", 5555)
        appium_row = rows[-1]
        assert appium_row.status == "ok"
        assert "10.0.0.5:5555" in appium_row.detail
        connect.assert_called_once_with(("10.0.0.5", 5555), timeout=doctor._SOCKET_TIMEOUT_S)


# --------------------------------------------------------------------------- #
# check_web                                                                    #
# --------------------------------------------------------------------------- #

class TestCheckWeb:
    @pytest.fixture(autouse=True)
    def isolated_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        self.home = tmp_path

    def test_uninstalled_packages_warn(self, monkeypatch):
        monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
        with patch(f"{MODULE}.version", side_effect=PackageNotFoundError):
            rows = doctor.check_web()
        assert all(r.status == "warn" for r in rows)
        hints = {r.hint for r in rows}
        assert any("optics setup --install playwright" in h for h in hints)

    @pytest.mark.parametrize(
        "browser_dir", [["Library/Caches/ms-playwright"], [".cache/ms-playwright"]]
    )
    def test_chromium_download_detected(self, browser_dir, monkeypatch):
        cache = self.home
        for part in browser_dir:
            cache = cache / part
        (cache / "chromium-1200").mkdir(parents=True)
        monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
        with patch(f"{MODULE}.version", return_value="1.49.0"):
            rows = doctor.check_web()
        pw = next(r for r in rows if r.name == "playwright browser")
        assert pw.status == "ok"

    def test_playwright_without_browser_download_warns(self, monkeypatch):
        monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
        with patch(f"{MODULE}.version", return_value="1.49.0"):
            rows = doctor.check_web()
        pw = next(r for r in rows if r.name == "playwright browser")
        assert pw.status == "warn"
        assert "playwright install chromium" in pw.hint

    def test_selenium_with_driver_on_path_is_ok(self, monkeypatch):
        monkeypatch.setattr(doctor.shutil, "which",
                            lambda name: "/usr/bin/chromedriver" if name == "chromedriver" else None)
        with patch(f"{MODULE}.version", return_value="4.20.0"):
            rows = doctor.check_web()
        selenium_row = next(r for r in rows if r.name == "selenium webdriver")
        assert selenium_row.status == "ok"


# --------------------------------------------------------------------------- #
# validate_project                                                             #
# --------------------------------------------------------------------------- #

class TestValidateProject:
    def test_valid_rendered_config_all_ok(self, tmp_path):
        folder = _write_config(tmp_path)
        rows = doctor.validate_project(folder)
        assert rows, "expected at least one row"
        assert all(r.status == "ok" for r in rows)
        assert all(r.name.startswith("config:") for r in rows)

    def test_no_enabled_driver_fails(self, tmp_path):
        starter = "\n".join(
            line.replace("enabled: true", "enabled: false")
            for line in project_config.render_project_config(ANDROID_ANSWERS).splitlines()
        )
        folder = _write_config(tmp_path, raw=starter + "\n")
        rows = doctor.validate_project(folder)
        assert any(r.status == "fail" and "no driver enabled" in r.detail for r in rows)

    def test_multiple_enabled_drivers_warn(self, tmp_path):
        # The framework deliberately supports multi-driver fallback
        # (InstanceFallback), so >1 enabled driver stays a warning — but the
        # "exactly one" guidance must still surface.
        raw = (
            "driver_sources:\n"
            "  - appium:\n"
            "      enabled: true\n"
            "      url: 'http://127.0.0.1:4723'\n"
            "      capabilities:\n"
            "        deviceName: emu-1\n"
            "        platformName: Android\n"
            "        appPackage: com.example.app\n"
            "        appActivity: com.example.MainActivity\n"
            "  - playwright:\n"
            "      enabled: true\n"
            "      capabilities:\n"
            "        browser: chromium\n"
            "elements_sources:\n"
            "  - appium_find_element:\n"
            "      enabled: true\n"
            "  - playwright_find_element:\n"
            "      enabled: true\n"
        )
        folder = _write_config(tmp_path, raw=raw)
        rows = doctor.validate_project(folder)
        driver_row = next(r for r in rows if r.name == "config: driver")
        assert driver_row.status == "warn"
        assert "2 drivers enabled" in driver_row.detail
        assert "appium" in driver_row.detail
        assert "playwright" in driver_row.detail

    def test_driver_without_matching_source_fails(self, tmp_path):
        raw = (
            "driver_sources:\n"
            "  - appium:\n"
            "      enabled: true\n"
            "      url: 'http://127.0.0.1:4723'\n"
            "elements_sources:\n"
            "  - appium_find_element:\n"
            "      enabled: false\n"
        )
        folder = _write_config(tmp_path, raw=raw)
        rows = doctor.validate_project(folder)
        assert any(r.status == "fail" and "element source" in r.name for r in rows)

    def test_appium_without_url_fails(self, tmp_path):
        raw = (
            "driver_sources:\n"
            "  - appium:\n"
            "      enabled: true\n"
            "      capabilities:\n"
            "        deviceName: emu-1\n"
            "        platformName: Android\n"
            "        appPackage: com.example.app\n"
            "        appActivity: com.example.MainActivity\n"
            "elements_sources:\n"
            "  - appium_find_element:\n"
            "      enabled: true\n"
        )
        folder = _write_config(tmp_path, raw=raw)
        rows = doctor.validate_project(folder)
        assert any(r.status == "fail" and "url is missing" in r.detail for r in rows)

    def test_ios_settings_row_ok_with_bundle_id(self, tmp_path):
        folder = _write_config(tmp_path, answers=IOS_ANSWERS)
        rows = doctor.validate_project(folder)
        settings = next(r for r in rows if r.name == "config: appium settings")
        assert settings.status == "ok"

    def test_ios_settings_row_fails_without_bundle_id(self, tmp_path):
        raw = (
            "driver_sources:\n"
            "  - appium:\n"
            "      enabled: true\n"
            "      url: 'http://127.0.0.1:4723'\n"
            "      capabilities:\n"
            "        automationName: XCUITest\n"
            "        deviceName: iPhone 15\n"
            "        platformName: iOS\n"
            "elements_sources:\n"
            "  - appium_find_element:\n"
            "      enabled: true\n"
        )
        folder = _write_config(tmp_path, raw=raw)
        rows = doctor.validate_project(folder)
        settings = next(r for r in rows if r.name == "config: appium settings")
        assert settings.status == "fail"
        assert "bundleId missing in capabilities" in settings.detail

    def test_android_settings_still_require_app_package_and_activity(self, tmp_path):
        raw = (
            "driver_sources:\n"
            "  - appium:\n"
            "      enabled: true\n"
            "      url: 'http://127.0.0.1:4723'\n"
            "      capabilities:\n"
            "        automationName: UiAutomator2\n"
            "        deviceName: emu-1\n"
            "        platformName: Android\n"
            "elements_sources:\n"
            "  - appium_find_element:\n"
            "      enabled: true\n"
        )
        folder = _write_config(tmp_path, raw=raw)
        rows = doctor.validate_project(folder)
        settings = next(r for r in rows if r.name == "config: appium settings")
        # Historical severity for a missing android capability is a warning.
        assert settings.status == "warn"
        assert "appPackage missing in capabilities" in settings.detail
        assert "appActivity missing in capabilities" in settings.detail

    def test_missing_config_file_fails(self, tmp_path):
        rows = doctor.validate_project(str(tmp_path / "nowhere"))
        assert rows[0].status == "fail"
        assert "no config.yaml" in rows[0].detail

    def test_unparseable_yaml_fails(self, tmp_path):
        folder = _write_config(tmp_path, raw="driver_sources: [oops\n")
        rows = doctor.validate_project(folder)
        assert any(r.status == "fail" for r in rows)

    def test_creates_no_execution_output_dir(self, tmp_path):
        folder = _write_config(tmp_path)
        before = sorted(os.listdir(folder))
        doctor.validate_project(folder)
        after = sorted(os.listdir(folder))
        assert before == after
        assert "execution_output" not in after


# --------------------------------------------------------------------------- #
# _split_host_port                                                             #
# --------------------------------------------------------------------------- #

class TestSplitHostPort:
    @pytest.mark.parametrize("url, expected", [
        ("http://10.1.2.3:5555", ("10.1.2.3", 5555)),
        ("https://appium.corp.local:4443/wd/hub", ("appium.corp.local", 4443)),
        ("10.0.0.5:4723", ("10.0.0.5", 4723)),  # schemeless host:port
        ("127.0.0.1", ("127.0.0.1", 4723)),     # bare host keeps the default port
        ("localhost:8080/", ("localhost", 8080)),
    ])
    def test_parses_host_and_port(self, url, expected):
        assert doctor._split_host_port(url) == expected

    @pytest.mark.parametrize("url", [
        None,
        "",
        "   ",
        "host:notaport",   # non-numeric port → urlparse raises ValueError
        "host:99999",      # out-of-range port → ValueError
    ])
    def test_unusable_url_falls_back_to_none(self, url):
        assert doctor._split_host_port(url) is None


# --------------------------------------------------------------------------- #
# run_doctor                                                                   #
# --------------------------------------------------------------------------- #

def _stub_env_rows():
    return [Check("python", "ok", "3.12.1"),
            Check("Appium", "warn", "not installed", "optics setup --install appium")]


class TestRunDoctor:
    @pytest.fixture
    def quiet_env(self, monkeypatch):
        """Replace every environment probe with one canned warning row."""
        monkeypatch.setattr(doctor, "check_core", _stub_env_rows)
        monkeypatch.setattr(doctor, "check_engines", lambda: [])
        monkeypatch.setattr(doctor, "check_web", lambda: [])
        captured = {}

        def fake_check_mobile(host="127.0.0.1", port=4723):
            captured["target"] = (host, port)
            return [Check("appium server", "warn", f"{host}:{port}")]

        monkeypatch.setattr(doctor, "check_mobile", fake_check_mobile)
        return captured

    def test_zero_when_environment_has_only_warnings(self, quiet_env):
        code, out = _render(doctor.run_doctor)
        assert code == 0
        assert "⚠️" in out

    def test_nonzero_when_critical_row_fails(self, quiet_env, monkeypatch):
        monkeypatch.setattr(
            doctor, "validate_project",
            lambda _f: [Check("config: driver", "fail", "no driver enabled")])
        code, _ = _render(doctor.run_doctor, "/some/project", check=True)
        assert code == 1

    def test_check_false_always_returns_zero(self, quiet_env, monkeypatch):
        monkeypatch.setattr(
            doctor, "validate_project",
            lambda _f: [Check("config: driver", "fail", "no driver enabled")])
        code, _ = _render(doctor.run_doctor, "/some/project")
        assert code == 0

    def test_zero_when_project_validates(self, quiet_env, tmp_path):
        folder = _write_config(tmp_path)
        code, _ = _render(doctor.run_doctor, folder, check=True)
        assert code == 0

    def test_validation_failure_ignored_without_check(self, quiet_env, monkeypatch):
        sentinel = [Check("config: file", "fail", "no config.yaml in /gone")]
        validator = MagicMock(return_value=sentinel)
        monkeypatch.setattr(doctor, "validate_project", validator)
        code, _ = _render(doctor.run_doctor, "/gone", check=False)
        validator.assert_called_once_with("/gone")
        # check=False must stay green even though validation failed.
        assert code == 0

    def test_mobile_probe_uses_project_appium_url(self, quiet_env, tmp_path):
        folder = _write_config(tmp_path, answers={
            **ANDROID_ANSWERS, "appium_url": "http://10.1.2.3:5555"})
        _render(doctor.run_doctor, folder)
        assert quiet_env["target"] == ("10.1.2.3", 5555)

    def test_mobile_probe_defaults_for_non_appium_project(self, quiet_env, tmp_path):
        folder = _write_config(tmp_path, answers={
            **ANDROID_ANSWERS, "platform": "web-selenium"})
        _render(doctor.run_doctor, folder)
        assert quiet_env["target"] == ("127.0.0.1", 4723)

    def test_report_prints_glyphs(self, quiet_env, tmp_path):
        folder = tmp_path / "broken"
        folder.mkdir()
        _, out = _render(doctor.run_doctor, str(folder))
        assert "❌" in out
        assert "config:" in out


class TestMissingProjectRow:
    """A bare `optics doctor` must speak about the absent project instead of
    printing only machine rows (and the absence itself is a warning, never a
    --check failure)."""

    def test_bare_run_in_empty_dir_warns_about_missing_project(
            self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        with patch(f"{MODULE}.check_core", _stub_env_rows), \
                patch(f"{MODULE}.check_engines", return_value=[]), \
                patch(f"{MODULE}.check_web", return_value=[]), \
                patch(f"{MODULE}.check_mobile", return_value=[]):
            code, out = _render(doctor.run_doctor)
        assert 'project' in out
        assert "No config.yaml found here" in out
        assert "`optics quickstart`" in out

    def test_missing_project_row_does_not_fail_check(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        with patch(f"{MODULE}.check_core", _stub_env_rows), \
                patch(f"{MODULE}.check_engines", return_value=[]), \
                patch(f"{MODULE}.check_web", return_value=[]), \
                patch(f"{MODULE}.check_mobile", return_value=[]):
            code, _ = _render(doctor.run_doctor, check=True)
        assert code == 0

    def test_no_project_row_when_cwd_has_config(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("driver_sources: []\n",
                                              encoding="utf-8")
        with patch(f"{MODULE}.check_core", _stub_env_rows), \
                patch(f"{MODULE}.check_engines", return_value=[]), \
                patch(f"{MODULE}.check_web", return_value=[]), \
                patch(f"{MODULE}.check_mobile", return_value=[]):
            _, out = _render(doctor.run_doctor)
        assert "No config.yaml found here" not in out

    def test_explicit_folder_keeps_its_own_fail_row(self, tmp_path):
        folder = str(tmp_path / "empty")
        os.makedirs(folder)
        with patch(f"{MODULE}.check_core", _stub_env_rows), \
                patch(f"{MODULE}.check_engines", return_value=[]), \
                patch(f"{MODULE}.check_web", return_value=[]), \
                patch(f"{MODULE}.check_mobile", return_value=[]):
            code, out = _render(doctor.run_doctor, folder, check=True)
        assert "No config.yaml found here" not in out
        assert "config:" in out
        assert code == 1


class TestGatedClosingMessage:
    """Warnings on an ENABLED driver's must-haves replace the reassurance
    with a targeted call-to-action listing exactly those hints."""

    APPIUM_HINT = "Start it in another terminal with the `appium` command."
    DEVICE_HINT = "Connect a device or start an emulator (USB debugging on)."
    SELENIUM_HINT = "Install a browser with a matching driver (e.g. chromedriver)."
    PLAYWRIGHT_HINT = ("playwright install chromium  "
                       "(or: optics setup --install playwright)")

    def _run_with_rows(self, tmp_path, *, env_rows, web_rows=None,
                       answers=None):
        folder = _write_config(tmp_path, answers=answers)
        web = web_rows if web_rows is not None else []
        with patch(f"{MODULE}.check_core",
                   return_value=[Check("python", "ok", "3.12")]), \
                patch(f"{MODULE}.check_engines", return_value=[]), \
                patch(f"{MODULE}.check_web", return_value=web), \
                patch(f"{MODULE}.check_mobile", return_value=env_rows):
            return _render(doctor.run_doctor, folder)

    def test_appium_server_and_device_warns_are_called_out(self, tmp_path):
        rows = [
            Check("adb devices", "warn", "no devices attached", self.DEVICE_HINT),
            Check("appium server", "warn", "not reachable at 127.0.0.1:4723",
                  self.APPIUM_HINT),
        ]
        _, out = self._run_with_rows(tmp_path, env_rows=rows)
        assert "⚠️ Before your first real run:" in out
        assert f"  • {self.DEVICE_HINT}" in out
        assert f"  • {self.APPIUM_HINT}" in out
        assert "Good enough to start" not in out

    def test_selenium_driver_warn_is_called_out(self, tmp_path):
        _, out = self._run_with_rows(
            tmp_path, env_rows=[],
            web_rows=[Check("selenium webdriver", "warn",
                            "package installed, but no WebDriver binary on PATH",
                            self.SELENIUM_HINT)],
            answers={**ANDROID_ANSWERS, "platform": "web-selenium"})
        assert "⚠️ Before your first real run:" in out
        assert f"  • {self.SELENIUM_HINT}" in out

    def test_playwright_browser_warn_is_called_out(self, tmp_path):
        _, out = self._run_with_rows(
            tmp_path, env_rows=[],
            web_rows=[Check("playwright browser", "warn",
                            "package installed, but no Chromium download detected",
                            self.PLAYWRIGHT_HINT)],
            answers={**ANDROID_ANSWERS, "platform": "web-playwright"})
        assert "⚠️ Before your first real run:" in out
        assert f"  • {self.PLAYWRIGHT_HINT}" in out

    def test_warning_for_other_drivers_keeps_reassurance(self, tmp_path):
        _, out = self._run_with_rows(
            tmp_path, env_rows=[],
            web_rows=[Check("playwright browser", "warn", "missing",
                            self.PLAYWRIGHT_HINT)])
        assert "Good enough to start" in out
        assert "Before your first real run" not in out

    def test_ok_driver_requirements_keep_reassurance(self, tmp_path):
        rows = [
            Check("adb devices", "ok", "1 connected: emu-5554"),
            Check("appium server", "ok", "reachable at 127.0.0.1:4723"),
            Check("EasyOCR", "warn", "easyocr not installed",
                  "optics setup --install easyocr"),
        ]
        _, out = self._run_with_rows(tmp_path, env_rows=rows)
        assert "Good enough to start" in out

    def test_counts_line_survives_gating(self, tmp_path):
        rows = [
            Check("adb devices", "warn", "no devices attached", self.DEVICE_HINT),
            Check("appium server", "warn", "not reachable", self.APPIUM_HINT),
        ]
        _, out = self._run_with_rows(tmp_path, env_rows=rows)
        assert "warning(s)" in out
