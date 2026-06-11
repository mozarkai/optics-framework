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
    ctrl.active_target_label = None
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

    def test_ios_appium_does_not_switch(self):
        c = _shell("appium", {"platformName": "iOS", "udid": "x"})
        assert c.supports_device_switching() is False

    def test_appium_without_platform_does_not_switch(self):
        c = _shell("appium", {"udid": "x"})
        assert c.supports_device_switching() is False

    @pytest.mark.parametrize("driver,caps", [("selenium", {"browserName": "chrome"}),
                                             ("playwright", {"browser": "chromium"})])
    def test_web_drivers_do_not_switch(self, driver, caps):
        assert _shell(driver, caps).supports_device_switching() is False

    def test_switch_device_raises_for_non_switchable(self):
        c = _shell("playwright", {"browser": "chromium"})
        with pytest.raises(OpticsError) as exc:
            c.switch_device("anything")
        assert exc.value.code == Code.E0501
        assert "Android/Appium only" in exc.value.message
