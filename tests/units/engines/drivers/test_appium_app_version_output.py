import subprocess
from unittest.mock import MagicMock, patch

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.drivers.appium import (
    Appium,
    _MAX_ERROR_OUTPUT_LINES,
)


def _driver_instance(capabilities: dict) -> Appium:
    instance = Appium.__new__(Appium)
    instance.driver = None
    instance.capabilities = capabilities
    return instance


def _numbered_output(count: int) -> str:
    return "\n".join(f"line {index}" for index in range(count))


class TestAndroidAppVersionErrorOutput:
    def _instance(self) -> Appium:
        instance = _driver_instance({"appPackage": "com.example.app"})
        instance._get_android_device_serial = MagicMock(return_value=None)
        return instance

    def test_command_failure_without_output_says_no_output(self):
        instance = self._instance()

        with patch("subprocess.check_output", side_effect=OSError("adb not found")):
            with pytest.raises(OpticsError) as excinfo:
                instance._get_android_app_version()

        assert "Output:\n<no output>" in str(excinfo.value)

    def test_version_name_is_returned_without_an_error(self):
        instance = self._instance()

        with patch("subprocess.check_output", return_value="    versionName=1.2.3\n"):
            assert instance._get_android_app_version() == "1.2.3"


class TestIosAppVersionErrorOutput:
    def _instance(self) -> Appium:
        instance = _driver_instance({"bundleId": "com.example.app"})
        instance._get_ios_device_udid = MagicMock(return_value=None)
        return instance

    def test_missing_bundle_truncates_the_listing(self):
        instance = self._instance()

        with patch("subprocess.check_output", return_value=_numbered_output(400)):
            with pytest.raises(OpticsError) as excinfo:
                instance._get_ios_app_version()

        assert excinfo.value.code == Code.E0401
        assert f"… ({400 - _MAX_ERROR_OUTPUT_LINES} lines omitted)" in str(excinfo.value)

    def test_command_failure_reports_no_output_when_nothing_was_captured(self):
        instance = self._instance()
        failure = subprocess.CalledProcessError(returncode=1, cmd=["ideviceinstaller"])

        with patch("subprocess.check_output", side_effect=failure):
            with pytest.raises(OpticsError) as excinfo:
                instance._get_ios_app_version()

        assert "Output:\n<no output>" in str(excinfo.value)

    def test_missing_binary_keeps_the_install_hint(self):
        instance = self._instance()

        with patch("subprocess.check_output", side_effect=FileNotFoundError()):
            with pytest.raises(OpticsError) as excinfo:
                instance._get_ios_app_version()

        assert "brew install ideviceinstaller" in str(excinfo.value)

    def test_bundle_version_is_returned_without_an_error(self):
        instance = self._instance()
        listing = 'com.example.app, "4.5.6", "Example"'

        with patch("subprocess.check_output", return_value=listing):
            assert instance._get_ios_app_version() == "4.5.6"
