"""Unit tests for the driver-agnostic `optics live` controller.

Covers config validation (require exactly one driver + a source), surfacing of real
config-parse/validation errors, per-driver target labels, and the Android-only gating
of device discovery/switching. No device or network is needed.
"""
import os
import tempfile
import textwrap

import pytest

from optics_framework.helper import live as live_mod
from optics_framework.helper.live import LiveController
from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.common.error import OpticsError, Code

pytestmark = pytest.mark.white_box


def _project(body: str) -> str:
    """Create a temp project dir containing a config.yaml with ``body``."""
    d = tempfile.mkdtemp(prefix="optics_live_test_")
    with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(body))
    return d


_APPIUM_ANDROID = """
driver_sources:
  - appium: {enabled: true, capabilities: {platformName: Android, udid: emulator-5554}}
elements_sources:
  - appium_find_element: {enabled: true}
"""

_PLAYWRIGHT = """
driver_sources:
  - playwright: {enabled: true, capabilities: {browser: chromium}}
elements_sources:
  - playwright_find_element: {enabled: true}
"""


class TestComposeConfig:
    def test_missing_config_raises_guidance(self):
        empty = tempfile.mkdtemp(prefix="optics_live_test_")
        with pytest.raises(OpticsError) as exc:
            live_mod._compose_config(empty)
        assert exc.value.code == Code.E0501
        assert "needs a config.yaml" in exc.value.message

    def test_no_enabled_driver_raises(self):
        d = _project("""
            driver_sources:
              - appium: {enabled: false}
            elements_sources:
              - appium_find_element: {enabled: true}
        """)
        with pytest.raises(OpticsError) as exc:
            live_mod._compose_config(d)
        assert "No enabled driver" in exc.value.message

    def test_multiple_enabled_drivers_rejected(self):
        d = _project("""
            driver_sources:
              - appium: {enabled: true, capabilities: {platformName: Android}}
              - playwright: {enabled: true, capabilities: {browser: chromium}}
            elements_sources:
              - appium_find_element: {enabled: true}
        """)
        with pytest.raises(OpticsError) as exc:
            live_mod._compose_config(d)
        assert "exactly one enabled driver" in exc.value.message
        assert "appium" in exc.value.message and "playwright" in exc.value.message

    def test_no_enabled_element_source_raises(self):
        d = _project("""
            driver_sources:
              - playwright: {enabled: true, capabilities: {browser: chromium}}
            elements_sources:
              - playwright_find_element: {enabled: false}
        """)
        with pytest.raises(OpticsError) as exc:
            live_mod._compose_config(d)
        assert "elements_sources" in exc.value.message

    def test_valid_single_driver_returns_config(self):
        cfg = live_mod._compose_config(_project(_PLAYWRIGHT))
        assert live_mod._enabled_drivers(cfg) == ["playwright"]


class TestConfigErrorSurfacing:
    def test_malformed_config_yaml_surfaces_parse_error(self):
        # Invalid YAML in the conventional config.yaml must not be masked as "no config".
        d = tempfile.mkdtemp(prefix="optics_live_test_")
        with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write("driver_sources: [\n  appium: : :\n")
        with pytest.raises(OpticsError) as exc:
            live_mod._compose_config(d)
        assert "Failed to parse" in exc.value.message
        assert "config.yaml" in exc.value.message

    def test_config_like_but_invalid_schema_surfaces(self):
        # A config-like file (has a recognised key) that fails Config() validation surfaces.
        d = tempfile.mkdtemp(prefix="optics_live_test_")
        with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write("driver_sources: 12345\n")  # wrong type
        with pytest.raises(OpticsError) as exc:
            live_mod._compose_config(d)
        assert "Invalid config" in exc.value.message

    def test_unrelated_malformed_yaml_is_skipped(self):
        # A non-config YAML with a syntax error should NOT abort; the real config still loads.
        d = _project(_PLAYWRIGHT)
        with open(os.path.join(d, "testdata.yaml"), "w", encoding="utf-8") as fh:
            fh.write(": : not valid : :\n")
        cfg = live_mod._compose_config(d)
        assert live_mod._enabled_drivers(cfg) == ["playwright"]


def _shell(driver: str, caps: dict) -> LiveController:
    """A LiveController shell with just enough state for the driver-type helpers."""
    ctrl = LiveController.__new__(LiveController)
    ctrl.config = Config(
        driver_sources=[{driver: DependencyConfig(enabled=True, capabilities=caps)}]
    )
    ctrl.driver_type = ctrl._enabled_driver_name()
    # Mirror the constructor: the target label is computed once at init.
    ctrl.active_target_label = ctrl._get_target_id_from_config()
    return ctrl


class TestTargetLabel:
    def test_appium_android(self):
        c = _shell("appium", {"platformName": "Android", "udid": "emulator-5554"})
        assert c.active_target() == "appium:emulator-5554"

    def test_selenium(self):
        c = _shell("selenium", {"browserName": "chrome"})
        assert c.active_target() == "selenium:chrome"

    def test_playwright(self):
        c = _shell("playwright", {"browser": "chromium"})
        assert c.active_target() == "playwright:chromium"

    def test_label_falls_back_to_driver_type(self):
        c = _shell("playwright", {})  # no identifying cap
        assert c.active_target() == "playwright"


class TestDeviceSwitching:
    def test_android_appium_supports_switching(self):
        c = _shell("appium", {"platformName": "Android", "udid": "x"})
        assert c.supports_device_switching() is True

    def test_appium_without_platform_can_switch_but_not_hotplug(self):
        # Any Appium session can target a device by udid; adb hot-plug needs Android.
        c = _shell("appium", {"udid": "x"})
        assert c.supports_device_switching() is True
        assert c.supports_adb_hotplug() is False

    def test_switch_device_raises_for_non_switchable(self):
        c = _shell("playwright", {"browser": "chromium"})
        with pytest.raises(OpticsError) as exc:
            c.switch_device("anything")
        assert exc.value.code == Code.E0501
        assert "Appium sessions only" in exc.value.message

    def test_appium_ios_can_switch_but_not_hotplug(self):
        c = _shell("appium", {"platformName": "iOS", "udid": "x"})
        assert c.supports_device_switching() is True   # /device works for iOS Appium
        assert c.supports_adb_hotplug() is False        # but no adb hot-plug monitor

    def test_appium_android_supports_both(self):
        c = _shell("appium", {"platformName": "Android", "udid": "x"})
        assert c.supports_device_switching() is True
        assert c.supports_adb_hotplug() is True

    @pytest.mark.parametrize("driver,caps", [("selenium", {"browserName": "chrome"}),
                                             ("playwright", {"browser": "chromium"})])
    def test_web_drivers_support_neither(self, driver, caps):
        c = _shell(driver, caps)
        assert c.supports_device_switching() is False
        assert c.supports_adb_hotplug() is False


class _Proc:
    def __init__(self, stdout: str):
        self.stdout = stdout


_ADB_OUT = (
    "List of devices attached\n"
    "emulator-5554\tdevice\n"
    "ABCD1234\tdevice\n"
    "offlinedev\toffline\n"   # not ready -> excluded
    "\n"
)
# idevice_id -l prints one UDID per line; tolerate an optional (USB)/(Network) suffix.
_IDEVICE_OUT = (
    "00008030-001A2D3E1234567A\n"
    "00008101-AABBCCDDEEFF0011 (USB)\n"
    "\n"
)


def _fake_run(adb_out="", ios_out="", missing=()):
    def _run(cmd, **kwargs):
        tool = cmd[0]
        if tool in missing:
            raise FileNotFoundError(tool)
        if cmd[:2] == ["adb", "devices"]:
            return _Proc(adb_out)
        if tool == "idevice_id":
            return _Proc(ios_out)
        return _Proc("")
    return _run


class TestDeviceListing:
    def test_list_android_devices(self, monkeypatch):
        monkeypatch.setattr(live_mod.subprocess, "run", _fake_run(adb_out=_ADB_OUT))
        assert LiveController.list_android_devices() == ["emulator-5554", "ABCD1234"]

    def test_list_ios_devices(self, monkeypatch):
        monkeypatch.setattr(live_mod.subprocess, "run", _fake_run(ios_out=_IDEVICE_OUT))
        # First token of each non-empty line; the (USB) suffix is stripped.
        assert LiveController.list_ios_devices() == [
            "00008030-001A2D3E1234567A",
            "00008101-AABBCCDDEEFF0011",
        ]

    def test_list_devices_combines_with_platform(self, monkeypatch):
        monkeypatch.setattr(
            live_mod.subprocess, "run", _fake_run(adb_out=_ADB_OUT, ios_out=_IDEVICE_OUT)
        )
        assert LiveController.list_devices() == [
            ("emulator-5554", "android"),
            ("ABCD1234", "android"),
            ("00008030-001A2D3E1234567A", "ios"),
            ("00008101-AABBCCDDEEFF0011", "ios"),
        ]

    def test_missing_tools_degrade_gracefully(self, monkeypatch):
        monkeypatch.setattr(
            live_mod.subprocess, "run", _fake_run(missing=("adb", "idevice_id"))
        )
        assert LiveController.list_android_devices() == []
        assert LiveController.list_ios_devices() == []
        assert LiveController.list_devices() == []

    def test_only_ios_present(self, monkeypatch):
        # adb returns nothing, idevice_id has devices -> /device still shows the iOS ones.
        monkeypatch.setattr(live_mod.subprocess, "run", _fake_run(ios_out=_IDEVICE_OUT))
        assert LiveController.list_devices() == [
            ("00008030-001A2D3E1234567A", "ios"),
            ("00008101-AABBCCDDEEFF0011", "ios"),
        ]


class _DeviceStubController:
    """Minimal controller for driving the TUI /device picker in a test."""

    saved = True
    recorded: list = []
    live_log_path = None
    driver_type = "appium"

    def __init__(self, devices):
        self._devices = devices

    # used by LiveTUI construction / completer (lazy, harmless)
    def keyword_names(self):
        return []

    def keyword_signature(self, _name):
        return ""

    def element_names(self):
        return []

    def element_first_locator(self, _name):
        return None

    def natural_language_available(self):
        return False

    # used by _cmd_device
    def supports_device_switching(self):
        return True

    def active_target(self):
        return "appium:emulator-5554"

    def list_devices(self):
        return self._devices


class TestDevicePicker:
    def test_cmd_device_lists_android_and_ios(self):
        from optics_framework.helper.live_tui import LiveTUI

        devices = [("emulator-5554", "android"), ("00008030-ABCD", "ios")]
        tui = LiveTUI(_DeviceStubController(devices))
        tui._cmd_device("")
        labels = [label for label, _value in tui.overlay_items]
        values = [value for _label, value in tui.overlay_items]
        assert values == ["emulator-5554", "00008030-ABCD"]
        assert any("(android)" in lbl for lbl in labels)
        assert any("(ios)" in lbl for lbl in labels)
        # active device is marked
        assert any("(active)" in lbl for lbl in labels)


class _FakeManager:
    def terminate_session(self, _session_id):
        return None


def _teardown_shell(artifacts_dir: str) -> LiveController:
    """A LiveController shell wired just enough to exercise teardown()."""
    ctrl = LiveController.__new__(LiveController)
    ctrl._artifacts_dir = artifacts_dir
    ctrl.session_id = "sid"
    ctrl.manager = _FakeManager()
    ctrl._live_log_handler = None  # makes _teardown_live_logging a no-op
    return ctrl


class TestArtifactsPersistence:
    def test_teardown_keeps_dir_with_screenshots(self):
        d = tempfile.mkdtemp(prefix="optics_live_shots_")
        with open(os.path.join(d, "pre-press_element.jpg"), "w") as fh:
            fh.write("x")
        _teardown_shell(d).teardown()
        # A session that captured screenshots keeps them after /quit.
        assert os.path.isdir(d) and os.listdir(d)

    def test_teardown_removes_empty_dir(self):
        d = tempfile.mkdtemp(prefix="optics_live_shots_")
        _teardown_shell(d).teardown()
        # An empty session dir is cleaned up so it doesn't litter.
        assert not os.path.isdir(d)
