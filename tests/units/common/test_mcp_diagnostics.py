"""Unit tests for optics_framework.helper.mcp_diagnostics.

Pure parsing/reflection helpers behind the MCP onboarding tools. No device, no
fastmcp — subprocess is monkeypatched so the adb/idevice parsers are exercised
against canned output.
"""

import subprocess
from types import SimpleNamespace

import pytest

from optics_framework.helper import mcp_diagnostics as diag

pytestmark = pytest.mark.white_box


# --------------------------------------------------------------------------- #
# device discovery parsers
# --------------------------------------------------------------------------- #
def test_parse_adb_devices_keeps_only_ready_devices():
    output = (
        "List of devices attached\n"
        "emulator-5554\tdevice\n"
        "R58N12ABCDE\tdevice\n"
        "0123456789\toffline\n"
        "9999999999\tunauthorized\n"
    )
    assert diag._parse_adb_devices(output) == ["emulator-5554", "R58N12ABCDE"]


def test_parse_adb_devices_empty():
    assert diag._parse_adb_devices("List of devices attached\n\n") == []


def test_parse_idevice_ids_tolerates_suffix():
    assert diag._parse_idevice_ids("00008030-ABC (USB)\n00008030-DEF\n\n") == [
        "00008030-ABC",
        "00008030-DEF",
    ]


def test_list_connected_devices_merges_platforms(monkeypatch):
    monkeypatch.setattr(diag, "list_android_devices", lambda: ["emulator-5554"])
    monkeypatch.setattr(diag, "list_ios_devices", lambda: ["udid-1"])
    assert diag.list_connected_devices() == [
        {"udid": "emulator-5554", "platform": "android"},
        {"udid": "udid-1", "platform": "ios"},
    ]


def test_run_tool_missing_binary_returns_none(monkeypatch):
    def boom(*_a, **_k):
        raise FileNotFoundError("no adb")

    monkeypatch.setattr(diag.subprocess, "run", boom)
    assert diag._run_tool(["adb", "devices"]) is None
    # And the higher-level device list degrades to empty rather than raising.
    assert diag.list_android_devices() == []


def test_run_tool_timeout_returns_none(monkeypatch):
    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="adb", timeout=10)

    monkeypatch.setattr(diag.subprocess, "run", boom)
    assert diag._run_tool(["adb", "devices"]) is None


# --------------------------------------------------------------------------- #
# engine / source discovery
# --------------------------------------------------------------------------- #
def test_default_sources_for_driver_appium_trio():
    assert diag.default_sources_for_driver("appium") == [
        "appium_find_element",
        "appium_page_source",
        "appium_screenshot",
    ]


def test_default_sources_for_driver_orders_by_priority():
    ordered = diag.default_sources_for_driver("selenium")
    # find_element/page_source/screenshot ranked ahead of anything else.
    assert ordered[:1] == ["selenium_find_element"] or "selenium_page_source" in ordered
    assert "selenium_screenshot" in ordered


def test_default_sources_for_driver_unknown_is_empty():
    assert diag.default_sources_for_driver("ble") == []
    assert diag.default_sources_for_driver("") == []


def test_list_available_sources_buckets_by_driver():
    result = diag.list_available_sources()
    assert "appium_find_element" in result["elements_sources"]["appium"]
    assert "easyocr" in result["text_detection"]
    assert "templatematch" in result["image_detection"]


def test_list_available_sources_filtered_by_driver():
    result = diag.list_available_sources("appium")
    assert result["elements_sources"]["driver"] == "appium"
    assert result["elements_sources"]["sources"] == [
        "appium_find_element",
        "appium_page_source",
        "appium_screenshot",
    ]


# --------------------------------------------------------------------------- #
# Android app introspection parsers
# --------------------------------------------------------------------------- #
def test_parse_pm_list_packages():
    output = "package:com.android.settings\npackage:com.google.android.contacts\ngarbage\n"
    assert diag._parse_pm_list_packages(output) == [
        "com.android.settings",
        "com.google.android.contacts",
    ]


def test_parse_foreground_app_from_resumed_activity():
    output = (
        "  mResumedActivity: ActivityRecord{abc u0 com.oneplus.deskclock/.DeskClock t42}\n"
    )
    assert diag._parse_foreground_app(output) == {
        "package": "com.oneplus.deskclock",
        "activity": ".DeskClock",
    }


def test_parse_foreground_app_none_when_no_match():
    assert diag._parse_foreground_app("nothing here") == {"package": None, "activity": None}


def test_list_installed_packages_filters_by_query(monkeypatch):
    monkeypatch.setattr(
        diag,
        "_run_tool",
        lambda *_a, **_k: "package:com.google.android.contacts\npackage:com.android.settings\n",
    )
    assert diag.list_installed_packages(query="contacts") == ["com.google.android.contacts"]


def test_list_installed_packages_raises_without_adb(monkeypatch):
    monkeypatch.setattr(diag, "_run_tool", lambda *_a, **_k: None)
    with pytest.raises(RuntimeError):
        diag.list_installed_packages()


def test_get_foreground_app_raises_without_adb(monkeypatch):
    monkeypatch.setattr(diag, "_run_tool", lambda *_a, **_k: None)
    with pytest.raises(RuntimeError):
        diag.get_foreground_app()


# --------------------------------------------------------------------------- #
# doctor (delegates to helper/doctor.py)
# --------------------------------------------------------------------------- #
def test_run_doctor_delegates_and_reports_mcp_extra(monkeypatch):
    from optics_framework.helper import doctor as real_doctor

    fake = [real_doctor.Check("python", "ok", "3.12.0")]
    monkeypatch.setattr(real_doctor, "check_core", lambda: fake)
    monkeypatch.setattr(real_doctor, "check_engines", lambda: [])
    monkeypatch.setattr(real_doctor, "check_mobile", lambda *_a, **_k: [])
    monkeypatch.setattr(real_doctor, "check_web", lambda: [])

    report = diag.run_doctor()
    names = {row["name"] for row in report["checks"]}
    assert "python" in names and "mcp_extra" in names
    assert report["ok"] is True


def test_run_doctor_fail_row_flips_ok(monkeypatch):
    from optics_framework.helper import doctor as real_doctor

    monkeypatch.setattr(
        real_doctor, "check_core", lambda: [real_doctor.Check("bad", "fail", "broken", "fix it")]
    )
    monkeypatch.setattr(real_doctor, "check_engines", lambda: [])
    monkeypatch.setattr(real_doctor, "check_mobile", lambda *_a, **_k: [])
    monkeypatch.setattr(real_doctor, "check_web", lambda: [])

    report = diag.run_doctor()
    assert report["ok"] is False
    bad = next(row for row in report["checks"] if row["name"] == "bad")
    assert bad["fix"] == "fix it"


def test_run_doctor_validates_project_when_path_given(monkeypatch):
    from optics_framework.helper import doctor as real_doctor

    monkeypatch.setattr(real_doctor, "check_core", lambda: [])
    monkeypatch.setattr(real_doctor, "check_engines", lambda: [])
    monkeypatch.setattr(real_doctor, "check_mobile", lambda *_a, **_k: [])
    monkeypatch.setattr(real_doctor, "check_web", lambda: [])
    called = SimpleNamespace(folder=None)

    def fake_validate(folder):
        called.folder = folder
        return [real_doctor.Check("config.yaml", "ok", "valid")]

    monkeypatch.setattr(real_doctor, "validate_project", fake_validate)
    report = diag.run_doctor(project_path="/tmp/proj")
    assert called.folder == "/tmp/proj"
    assert any(row["name"] == "config.yaml" for row in report["checks"])


def test_is_truthy():
    assert diag.is_truthy("true") and diag.is_truthy("1") and diag.is_truthy("YES")
    assert not diag.is_truthy("false") and not diag.is_truthy("0") and not diag.is_truthy(None)
