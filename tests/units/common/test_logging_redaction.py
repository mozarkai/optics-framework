"""Credential redaction in log output (I3).

Driver capabilities carry cloud-farm access keys, and ``optics serve`` forces
``log_level=DEBUG`` on every session it creates, so anything a driver logs
verbatim at DEBUG lands in the server console. ``SensitiveDataFormatter`` only
masks ``@:``-style URL credentials, so it does not help here.

These tests capture real ``logging`` output at DEBUG level via a handler
attached to ``internal_logger`` -- not by patching a single method -- because
the earlier version of this check only patched ``.info`` and therefore missed
every ``.debug`` call site.

The driver call-site tests import ``optics_framework.engines.drivers.appium``
and ``...selenium`` behind stub ``appium``/``selenium`` packages so they stay
hermetic and device-less even without the optional extras installed. The stubs
are torn down again so no other test module sees them.

Sources under test:
  optics_framework/common/logging_config.py
  optics_framework/engines/drivers/appium.py
  optics_framework/engines/drivers/selenium.py
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import logging
import sys
import types
from unittest.mock import MagicMock

import pytest

from optics_framework.common.logging_config import (
    REDACTED,
    internal_logger,
    redact_sensitive_values,
)

pytestmark = pytest.mark.white_box

SECRET = "SUPERSECRET123"


# ---------------------------------------------------------------------------
# Capturing DEBUG output from the real logger
# ---------------------------------------------------------------------------


class _CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def captured_debug_logs():
    """Every message emitted on ``internal_logger`` at DEBUG or above."""
    handler = _CapturingHandler()
    previous_level = internal_logger.level
    previous_disabled = internal_logger.disabled
    internal_logger.addHandler(handler)
    internal_logger.setLevel(logging.DEBUG)
    internal_logger.disabled = False
    try:
        yield handler.lines
    finally:
        internal_logger.removeHandler(handler)
        internal_logger.setLevel(previous_level)
        internal_logger.disabled = previous_disabled


# ---------------------------------------------------------------------------
# redact_sensitive_values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "browserstack.key",
        "bstack:accessKey",
        "LT:accessToken",
        "password",
        "app_secret",
        "AUTHORIZATION",
        "licenseKey",
    ],
)
def test_sensitive_keys_are_masked(key):
    assert redact_sensitive_values({key: SECRET})[key] == REDACTED


def test_non_sensitive_values_survive():
    caps = {"platformName": "Android", "appium:deviceName": "Pixel 7"}
    assert redact_sensitive_values(caps) == caps


def test_nested_option_blocks_are_redacted():
    caps = {
        "platformName": "Android",
        "bstack:options": {"userName": "someone", "accessKey": SECRET},
    }
    redacted = redact_sensitive_values(caps)
    assert redacted["bstack:options"]["accessKey"] == REDACTED
    assert redacted["bstack:options"]["userName"] == "someone"
    assert SECRET not in str(redacted)


# ---------------------------------------------------------------------------
# Driver call sites, behind stubbed appium/selenium packages
# ---------------------------------------------------------------------------


class _StubModule(types.ModuleType):
    """Module whose every attribute is a fresh ``MagicMock``."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return MagicMock()


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves any ``appium.*`` / ``selenium.*`` import as a stub package."""

    ROOTS = ("appium", "selenium")

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.ROOTS:
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        module = _StubModule(spec.name)
        module.__path__ = []  # type: ignore[attr-defined]
        return module

    def exec_module(self, module):
        pass


def _load_driver_modules():
    """Import both driver modules with stubbed clients, then undo the stubs."""
    before = set(sys.modules)
    finder = _StubFinder()
    sys.meta_path.insert(0, finder)
    try:
        import optics_framework.engines.drivers.appium as appium_driver
        import optics_framework.engines.drivers.selenium as selenium_driver
    finally:
        sys.meta_path.remove(finder)
        # Leave sys.modules exactly as we found it: the real extras may be
        # installed elsewhere (CI), and other test modules must not inherit
        # our stubs or a driver module bound to them.
        for name in set(sys.modules) - before:
            del sys.modules[name]
    return appium_driver, selenium_driver


APPIUM_DRIVER, SELENIUM_DRIVER = _load_driver_modules()


def _appium_instance(capabilities):
    driver = APPIUM_DRIVER.Appium.__new__(APPIUM_DRIVER.Appium)
    driver.capabilities = capabilities
    driver.driver = None
    driver.appium_server_url = "http://127.0.0.1:4723"
    driver.event_sdk = MagicMock()
    return driver


def test_appium_config_capabilities_are_not_logged_raw(captured_debug_logs):
    """``_get_platform_and_options`` dumped the whole config capability set."""
    driver = _appium_instance({"platformName": "Android", "browserstack.key": SECRET})

    driver._get_platform_and_options(dict(driver.capabilities))

    assert captured_debug_logs, "no DEBUG output captured; the check would be vacuous"
    assert not any(SECRET in line for line in captured_debug_logs), (
        f"capability secret leaked into logs: {captured_debug_logs}"
    )


def test_appium_final_capabilities_are_not_logged_raw(captured_debug_logs, monkeypatch):
    """``start_session`` dumped the merged capability set before connecting."""
    driver = _appium_instance({"platformName": "Android", "browserstack.key": SECRET})
    monkeypatch.setattr(
        APPIUM_DRIVER.Appium, "_create_new_driver_session",
        lambda self, options, event_name: "sess-1",
    )

    assert driver.start_session() == "sess-1"

    assert captured_debug_logs, "no DEBUG output captured; the check would be vacuous"
    assert not any(SECRET in line for line in captured_debug_logs), (
        f"capability secret leaked into logs: {captured_debug_logs}"
    )


def _selenium_instance(capabilities):
    driver = SELENIUM_DRIVER.SeleniumDriver.__new__(SELENIUM_DRIVER.SeleniumDriver)
    driver.capabilities = capabilities
    driver.driver = None
    driver.browser_url = "about:blank"
    driver.selenium_server_url = "http://127.0.0.1:4444"
    driver.event_sdk = MagicMock()
    return driver


def test_selenium_final_capabilities_are_not_logged_raw(captured_debug_logs):
    driver = _selenium_instance({"browserName": "chrome", "bstack:accessKey": SECRET})

    driver.start_session()

    assert captured_debug_logs, "no DEBUG output captured; the check would be vacuous"
    assert not any(SECRET in line for line in captured_debug_logs), (
        f"capability secret leaked into logs: {captured_debug_logs}"
    )
