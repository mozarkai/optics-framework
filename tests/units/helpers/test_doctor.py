from __future__ import annotations

import io
import json
import os
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

from optics_framework.helper import doctor, project_config
from tests.conftest import set_home
from optics_framework.helper.doctor import Check
from optics_framework.helper.setup import ALL_ENGINES


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


class TestCheckCore:
    def test_python_row_never_fails(self):
        with patch(f"{MODULE}.version", side_effect=PackageNotFoundError):
            rows = doctor.check_core()
        python_row = rows[0]
        assert python_row.name == "python"
        assert python_row.status == "ok"


class TestCheckEngines:
    def test_one_row_per_engine(self):
        with patch(f"{MODULE}.version", side_effect=PackageNotFoundError):
            rows = doctor.check_engines()
        assert sorted(r.name for r in rows) == sorted(e.name for e in ALL_ENGINES.values() if e.runs_here())


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


class TestCheckMobile:

    def test_no_devices_warns(self, no_socket, monkeypatch):
        monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/adb")
        stub = SimpleNamespace(stdout=f"{ADB_HEADER}\n\n")
        with patch.object(doctor.subprocess, "run", return_value=stub), \
                patch.object(doctor.socket, "create_connection"):
            rows = doctor.check_mobile()
        row = next(r for r in rows if r.name == "adb devices")
        assert row.status == "warn"
        assert "no devices attached" in row.detail


class TestCheckWeb:
    @pytest.fixture(autouse=True)
    def isolated_home(self, monkeypatch, tmp_path):
        set_home(monkeypatch, tmp_path)
        self.home = tmp_path

    def test_uninstalled_packages_warn(self, monkeypatch):
        monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
        with patch(f"{MODULE}.version", side_effect=PackageNotFoundError):
            rows = doctor.check_web()
        assert all(r.status == "warn" for r in rows)
        hints = {r.hint for r in rows}
        assert any("optics setup --install playwright" in h for h in hints)

    def test_playwright_without_browser_download_warns(self, monkeypatch):
        monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
        with patch(f"{MODULE}.version", return_value="1.49.0"):
            rows = doctor.check_web()
        pw = next(r for r in rows if r.name == "playwright browser")
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
        _, out = _render(doctor.print_report, [("Desktop", [Check("xa11y driver", "ok", "found"),
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

    def test_unparseable_yaml_fails(self, tmp_path):
        folder = _write_config(tmp_path, raw="driver_sources: [oops\n")
        rows = doctor.validate_project(folder)
        assert any(r.status == "fail" for r in rows)


def _stub_env_rows():
    return [Check("python", "ok", "3.12.1"),
            Check("Appium", "warn", "not installed", "optics setup --install appium")]


class TestRunDoctor:
    @pytest.fixture
    def quiet_env(self, monkeypatch):
        monkeypatch.setattr(doctor, "check_core", _stub_env_rows)
        monkeypatch.setattr(doctor, "check_engines", lambda: [])
        monkeypatch.setattr(doctor, "check_web", lambda *_: [])
        captured = {}
        def fake_check_mobile(host="127.0.0.1", port=4723):
            captured["target"] = (host, port)
            return [Check("appium server", "warn", f"{host}:{port}")]
        monkeypatch.setattr(doctor, "check_mobile", fake_check_mobile)
        monkeypatch.setattr(doctor, "check_desktop", lambda *_: [])
        return captured

    def test_zero_when_environment_has_only_warnings(self, quiet_env):
        code, out = _render(doctor.run_doctor)
        assert code == 0
        assert "⚠️" in out

    def test_check_false_always_returns_zero(self, quiet_env, monkeypatch):
        monkeypatch.setattr(
            doctor, "validate_project",
            lambda _f: [Check("config: driver", "fail", "no driver enabled")])
        code, _ = _render(doctor.run_doctor, "/some/project")
        assert code == 0


class TestGatedClosingMessage:
    APPIUM_HINT = "Start it in another terminal with the `appium` command."
    DEVICE_HINT = "Connect a device or start an emulator (USB debugging on)."
    ADB_HINT = "Install Android platform-tools (needed to drive Android devices)."
    SELENIUM_HINT = "Install a browser with a matching driver (e.g. chromedriver)."
    PLAYWRIGHT_HINT = ("playwright install chromium  "
                       "(or: optics setup --install playwright)")

    def _run_with_rows(self, tmp_path, *, env_rows, web_rows=None,
                       answers=None, desktop_rows=()):
        folder = _write_config(tmp_path, answers=answers)
        web = web_rows if web_rows is not None else []
        with patch(f"{MODULE}.check_core",
                   return_value=[Check("python", "ok", "3.12")]), \
                patch(f"{MODULE}.check_engines", return_value=[]), \
                patch(f"{MODULE}.check_web", return_value=web), \
                patch(f"{MODULE}.check_mobile", return_value=env_rows), \
                patch(f"{MODULE}.check_desktop", return_value=list(desktop_rows)):
            return _render(doctor.run_doctor, folder)

    def test_missing_xa11y_driver_does_not_block_an_android_project(self, tmp_path, no_socket, monkeypatch):
        monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/local/bin/appium")
        _, out = self._run_with_rows(tmp_path, env_rows=[], desktop_rows=doctor.check_desktop(system="Linux"))
        assert XA11Y_INSTALL not in out.split("Before your first real run:")[1]
