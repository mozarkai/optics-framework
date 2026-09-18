import subprocess
from unittest.mock import MagicMock, patch

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.drivers.appium import (
    Appium,
    _MAX_ERROR_OUTPUT_LINES,
    _summarize_command_output,
)

pytestmark = pytest.mark.white_box

OMITTED_MARKER = "lines omitted"


def _driver_instance(capabilities: dict) -> Appium:
    instance = Appium.__new__(Appium)
    instance.driver = None
    instance.capabilities = capabilities
    return instance


def _numbered_output(count: int) -> str:
    return "\n".join(f"line {index}" for index in range(count))


class TestSummarizeCommandOutput:
    @pytest.mark.parametrize("output", ["", None, "\n  \n\t\n"])
    def test_blank_output_is_reported_as_no_output(self, output):
        assert _summarize_command_output(output) == "<no output>"

    def test_short_output_is_returned_whole(self):
        assert _summarize_command_output("a\nb\nc") == "a\nb\nc"

    def test_blank_lines_are_dropped(self):
        assert _summarize_command_output("a\n\n  \nb") == "a\nb"

    def test_exactly_max_lines_is_not_truncated(self):
        summary = _summarize_command_output(_numbered_output(_MAX_ERROR_OUTPUT_LINES))

        assert OMITTED_MARKER not in summary
        assert len(summary.splitlines()) == _MAX_ERROR_OUTPUT_LINES

    def test_one_line_over_max_is_truncated(self):
        summary = _summarize_command_output(_numbered_output(_MAX_ERROR_OUTPUT_LINES + 1))
        lines = summary.splitlines()

        assert len(lines) == _MAX_ERROR_OUTPUT_LINES + 1
        assert lines[_MAX_ERROR_OUTPUT_LINES // 2] == "… (1 lines omitted)"

    def test_head_and_tail_are_kept_with_omission_count(self):
        half = _MAX_ERROR_OUTPUT_LINES // 2
        summary = _summarize_command_output(_numbered_output(1000))
        lines = summary.splitlines()

        assert lines[:half] == [f"line {index}" for index in range(half)]
        assert lines[half] == f"… ({1000 - _MAX_ERROR_OUTPUT_LINES} lines omitted)"
        assert lines[half + 1:] == [f"line {index}" for index in range(1000 - half, 1000)]

    def test_blank_lines_do_not_count_towards_the_cap(self):
        padded = "\n\n".join(f"line {index}" for index in range(_MAX_ERROR_OUTPUT_LINES))

        assert OMITTED_MARKER not in _summarize_command_output(padded)

    def test_max_lines_is_configurable(self):
        summary = _summarize_command_output(_numbered_output(10), max_lines=4)

        assert summary.splitlines() == [
            "line 0",
            "line 1",
            "… (6 lines omitted)",
            "line 8",
            "line 9",
        ]


class TestAndroidAppVersionErrorOutput:
    def _instance(self) -> Appium:
        instance = _driver_instance({"appPackage": "com.example.app"})
        instance._get_android_device_serial = MagicMock(return_value=None)
        return instance

    def test_missing_version_name_truncates_the_dump(self):
        instance = self._instance()

        with patch("subprocess.check_output", return_value=_numbered_output(3000)):
            with pytest.raises(OpticsError) as excinfo:
                instance._get_android_app_version()

        assert excinfo.value.code == Code.E0401
        message = str(excinfo.value)
        assert f"… ({3000 - _MAX_ERROR_OUTPUT_LINES} lines omitted)" in message
        assert "line 2999" in message
        assert len(message.splitlines()) < 30

    def test_command_failure_embeds_captured_output(self):
        instance = self._instance()
        failure = subprocess.CalledProcessError(
            returncode=1, cmd=["adb"], output=_numbered_output(500)
        )

        with patch("subprocess.check_output", side_effect=failure):
            with pytest.raises(OpticsError) as excinfo:
                instance._get_android_app_version()

        message = str(excinfo.value)
        assert "<no output>" not in message
        assert "line 0" in message
        assert f"… ({500 - _MAX_ERROR_OUTPUT_LINES} lines omitted)" in message

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
