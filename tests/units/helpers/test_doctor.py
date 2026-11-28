from __future__ import annotations

import io
import json
import os
from importlib.metadata import PackageNotFoundError

import pytest
from rich.console import Console

from optics_framework.helper import doctor, project_config
from optics_framework.helper.doctor import Check
from tests.conftest import set_home

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


DESKTOP_CONFIG = """\
driver_sources:
  - appium:
      enabled: true
      url: 'http://127.0.0.1:4723'
      capabilities:
        platformName: Linux
        automationName: Xa11y
        {app}
elements_sources:
  - appium_find_element:
      enabled: true
"""

DEVICES_CONFIG = """\
elements_sources:
  - appium_find_element: {enabled: true}
  - playwright_find_element: {enabled: true}
devices:
  PH1:
    driver_sources:
      - appium:
          enabled: true
          url: 'http://hub-a.test:4444'
          capabilities: {platformName: Android, deviceName: pixel, appPackage: com.example.app, appActivity: .Main}
  TAB:
    driver_sources:
      - appium:
          enabled: true
          url: 'http://hub-b.test:4723'
          capabilities: {platformName: iOS, deviceName: ipad}
  WEB:
    driver_sources:
      - playwright: {enabled: true, capabilities: {browser: chromium}}
  GRID:
    driver_sources:
      - playwright: {enabled: true, url: 'ws://grid.test/playwright'}
"""


def _render(monkeypatch, func, *args, **kwargs):
    """Run a reporting function with a buffered console; returns (result, text)."""
    buf = io.StringIO()
    monkeypatch.setattr(doctor, "_console", Console(file=buf, width=200))
    return func(*args, **kwargs), buf.getvalue()


def _write_config(tmp_path, answers=None, raw=None) -> str:
    folder = str(tmp_path / "demo")
    os.makedirs(folder, exist_ok=True)
    text = raw if raw is not None else project_config.render_project_config(
        answers if answers is not None else ANDROID_ANSWERS)
    with open(os.path.join(folder, "config.yaml"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return folder


@pytest.fixture(autouse=True)
def appium_home(tmp_path, monkeypatch):
    home = tmp_path / "appium-home"
    home.mkdir()
    monkeypatch.setenv("APPIUM_HOME", str(home))
    return home


@pytest.fixture
def no_socket(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError("connection refused")
    monkeypatch.setattr(doctor.socket, "create_connection", refuse)


@pytest.fixture
def machine(tools, tmp_path, monkeypatch, no_socket):
    """A machine with no Appium server, no Playwright browser download and no device tools on PATH."""
    set_home(monkeypatch, tmp_path)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "browsers"))
    tools(installed=False)
    return tools


class TestCheckMobile:
    @pytest.mark.parametrize(("adb_says", "status", "detail"), [
        (f"{ADB_HEADER}\n\n", "warn", "no devices attached"),
        (f"{ADB_HEADER}\nemulator-5554\tdevice\nR58M\toffline\n", "ok", "1 connected: emulator-5554"),
        ("* daemon not running; starting now at tcp:5037\n", "warn", "unexpected adb output: * daemon not running"),
    ])
    def test_the_devices_adb_lists(self, machine, adb_says, status, detail):
        ran = machine(adb_says)
        row = next(r for r in doctor.check_mobile() if r.name == "adb devices")
        assert (row.status, row.detail[:len(detail)]) == (status, detail)
        assert ran() == ["adb devices"]


@pytest.mark.usefixtures("machine")
class TestCheckWeb:
    def test_uninstalled_packages_warn(self, monkeypatch):
        def not_installed(package):
            raise PackageNotFoundError(package)
        monkeypatch.setattr(doctor, "version", not_installed)
        rows = doctor.check_web()
        assert all(r.status == "warn" for r in rows)
        assert any("optics setup --install playwright" in r.hint for r in rows)

    def test_playwright_without_browser_download_warns(self, monkeypatch):
        monkeypatch.setattr(doctor, "version", lambda _package: "1.49.0")
        pw = next(r for r in doctor.check_web() if r.name == "playwright browser")
        assert pw.status == "warn"
        assert "playwright install chromium" in pw.hint


XA11Y_INSTALL = "appium driver install --source=npm appium-xa11y-driver"


def _install_xa11y(home, listed=True, present=True):
    """Lay out appium-xa11y-driver 0.1.3 under ``home`` the way `appium driver install` does."""
    package = home / "node_modules" / "appium-xa11y-driver"
    if present:
        package.mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"name": "appium-xa11y-driver", "version": "0.1.3"}))
    if listed:
        manifest = home / "node_modules" / ".cache" / "appium"
        manifest.mkdir(parents=True)
        (manifest / "extensions.yaml").write_text(
            f"drivers:\n  xa11y:\n    pkgName: appium-xa11y-driver\n    version: 0.1.0\n    installPath: '{package}'\n"
            "    automationName: Xa11y\nplugins: {}\nschemaRev: 4\n")


def _xa11y_row(system="Linux"):
    return next(r for r in doctor.check_desktop(system=system) if r.name == "xa11y driver")


class TestCheckDesktop:
    def test_driver_in_node_modules_without_a_manifest_is_ok(self, appium_home, no_socket):
        _install_xa11y(appium_home, listed=False)
        assert _xa11y_row().status == "ok"

    def test_macos_row_is_an_info_mark_outside_the_counts(self, no_socket, monkeypatch):
        monkeypatch.setattr(doctor, "_glyphs", lambda: doctor._EMOJI_GLYPHS)
        _, out = _render(monkeypatch, doctor.print_report, [("Desktop", [Check("xa11y driver", "ok", "found"),
                                                            *doctor.check_desktop(system="Darwin")[2:]])], [])
        assert "ℹ️ macOS permissions" in out
        assert "1 ok, 0 warning(s), 0 failure(s)" in out


class TestValidateProject:
    def test_ios_settings_row_ok_with_bundle_id(self, tmp_path):
        folder = _write_config(tmp_path, answers=IOS_ANSWERS)
        rows = doctor.validate_project(folder)
        settings = next(r for r in rows if r.name == "config: appium settings")
        assert settings.status == "ok"

    @pytest.mark.parametrize("session", ["existingSessionId: s-1", "appium:options: {sessionId: s-1}"])
    def test_desktop_settings_that_attach_to_a_session_only_warn_without_an_app(self, tmp_path, session):
        folder = _write_config(tmp_path, raw=DESKTOP_CONFIG.format(app=session))
        settings = next(r for r in doctor.validate_project(folder) if r.name == "config: appium settings")
        assert settings.status == "warn"
        assert "attaches to session s-1" in settings.detail

    def test_a_devices_project_gets_the_rows_of_each_device(self, tmp_path):
        rows = {row.name: row.status for row in doctor.validate_project(_write_config(tmp_path, raw=DEVICES_CONFIG))}
        assert rows == {
            "PH1: driver": "ok", "PH1: appium element source": "ok", "PH1: appium settings": "ok",
            "TAB: driver": "ok", "TAB: appium element source": "ok", "TAB: appium settings": "fail",
            "WEB: driver": "ok", "WEB: playwright element source": "ok", "WEB: playwright settings": "ok",
            "GRID: driver": "ok", "GRID: playwright element source": "ok", "GRID: playwright settings": "warn",
        }

    @pytest.mark.parametrize(("devices", "row", "status", "detail", "runnable"), [
        ("devices: {PH1: {driver_sources: [{appium: {enabled: true}}], values: {pin: 1234}}}", "config: file", "fail",
         "devices.PH1.values.pin: put the value in quotes", False),
        ("devices: [pixel, iphone]", "config: devices", "info", "not a devices block", True),
    ], ids=["a block that the config refuses", "a devices key of the project's own"])
    def test_a_devices_key_gets_its_own_row(self, tmp_path, devices, row, status, detail, runnable):
        raw = project_config.render_project_config(IOS_ANSWERS) + devices + "\n"
        rows = {shown.name: shown for shown in doctor.validate_project(_write_config(tmp_path, raw=raw))}
        assert (rows[row].status, detail in rows[row].detail) == (status, True)
        assert ("config: project" in rows) is runnable

    def test_unparseable_yaml_fails(self, tmp_path):
        folder = _write_config(tmp_path, raw="driver_sources: [oops\n")
        rows = doctor.validate_project(folder)
        assert any(r.status == "fail" for r in rows)


@pytest.mark.usefixtures("machine")
class TestRunDoctor:
    def test_environment_gaps_are_warnings(self, monkeypatch):
        code, out = _render(monkeypatch, doctor.run_doctor, check=True)
        assert code == 0
        assert "not found on PATH" in out

    @pytest.mark.parametrize(("check", "code"), [(False, 0), (True, 1)])
    def test_a_project_that_fails_its_checks(self, tmp_path, monkeypatch, check, code):
        folder = _write_config(tmp_path, raw="driver_sources: [oops\n")
        assert _render(monkeypatch, doctor.run_doctor, folder, check=check)[0] == code

    def test_a_missing_xa11y_driver_does_not_block_an_android_project(self, tmp_path, monkeypatch):
        _, out = _render(monkeypatch, doctor.run_doctor, _write_config(tmp_path))
        closing = out.split("Before your first real run:")[1]
        assert "Install Android platform-tools" in closing
        assert XA11Y_INSTALL not in closing

    def test_a_devices_project_needs_what_each_of_its_devices_needs(self, tmp_path, monkeypatch):
        runnable = DEVICES_CONFIG.replace("deviceName: ipad", "deviceName: ipad, bundleId: com.example.app")
        _, out = _render(monkeypatch, doctor.run_doctor, _write_config(tmp_path, raw=runnable))
        report, closing = out.split("Before your first real run:")
        assert ["not reachable at hub-a.test:4444" in report, "not reachable at hub-b.test:4723" in report] == [True, True]
        assert ["Install Android platform-tools" in closing, "optics setup --install playwright" in closing] == [True, True]
