import os
import tempfile
import textwrap

import pytest

from optics_framework.helper import live as live_mod
from optics_framework.helper.live import LiveController
from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.common.error import OpticsError, Code

pytestmark = [pytest.mark.white_box, pytest.mark.usefixtures("pt_app_session")]


def _project(body: str) -> str:
    """Create a temp project dir containing a config.yaml with ``body``."""
    d = tempfile.mkdtemp(prefix="optics_live_test_")
    with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(body))
    return d


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


class TestConfigErrorSurfacing:

    def test_unrelated_malformed_yaml_is_skipped(self):
        # A non-config YAML with a syntax error should NOT abort; the real config still loads.
        d = _project(_PLAYWRIGHT)
        with open(os.path.join(d, "testdata.yaml"), "w", encoding="utf-8") as fh:
            fh.write(": : not valid : :\n")
        cfg = live_mod._compose_config(d)
        assert live_mod._enabled_drivers(cfg) == ["playwright"]

    def test_optics_live_stops_on_a_bad_popup_rule_naming_the_rule_field_and_value(self, capsys):
        d = _project(_PLAYWRIGHT + """
popup_rules:
  - {name: Rate Us, detect: {text_contains: [rate us]}, action: {type: swipe, target: Later}}
""")
        with pytest.raises(SystemExit) as exc:
            live_mod.live_main(d)
        assert exc.value.code == 1
        error = capsys.readouterr().err
        assert "popup_rules['Rate Us'].action.type" in error
        assert "'swipe'" in error


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


class TestDeviceSwitching:
    def test_switch_device_raises_for_non_switchable(self):
        c = _shell("playwright", {"browser": "chromium"})
        with pytest.raises(OpticsError) as exc:
            c.switch_device("anything")
        assert exc.value.code == Code.E0501
        assert "Appium sessions only" in exc.value.message


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
    def test_teardown_removes_empty_dir(self):
        d = tempfile.mkdtemp(prefix="optics_live_shots_")
        _teardown_shell(d).teardown()
        # An empty session dir is cleaned up so it doesn't litter.
        assert not os.path.isdir(d)
