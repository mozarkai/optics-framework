"""Get App Version reads the app's version from the device tools, adb on Android and ideviceinstaller on iOS."""
import sys

import pytest

from optics_framework.common.error import Code, OpticsError
from tests.units.fake_appium import AppiumServer, appium_driver

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake device tools are shell scripts")

TOOL = """#!/bin/sh
echo "${0##*/} $*" >> "$FAKE_TOOL_ARGS"
printf '%s' "$FAKE_TOOL_OUTPUT"
exit "${FAKE_TOOL_STATUS:-0}"
"""
ANDROID = {"platformName": "Android", "appium:automationName": "UiAutomator2", "appium:appPackage": "in.app"}
IOS = {"platformName": "iOS", "appium:automationName": "XCUITest", "appium:bundleId": "in.app"}


@pytest.fixture(scope="session")
def tools(tmp_path_factory):
    """adb and ideviceinstaller as links to one script, which macOS checks once, on its first run."""
    folder = tmp_path_factory.mktemp("tools")
    (folder / "tool").write_text(TOOL)
    (folder / "tool").chmod(0o755)
    for name in ("adb", "ideviceinstaller"):
        (folder / name).symlink_to(folder / "tool")
    return folder


@pytest.fixture
def device(tools, tmp_path, monkeypatch):
    """A device whose tools print ``output`` and exit with ``status``; returns the command lines they were given."""
    ran = tmp_path / "ran"

    def answer(output="", status=0, installed=True):
        monkeypatch.setenv("PATH", str(tools if installed else tmp_path))
        monkeypatch.setenv("FAKE_TOOL_ARGS", str(ran))
        monkeypatch.setenv("FAKE_TOOL_OUTPUT", output)
        monkeypatch.setenv("FAKE_TOOL_STATUS", str(status))
        return lambda: ran.read_text().splitlines() if ran.exists() else []
    return answer


def started(monkeypatch, capabilities, device_caps):
    driver = appium_driver(monkeypatch, AppiumServer(device=device_caps), capabilities)
    driver.start_session()
    return driver


def version_error(driver):
    with pytest.raises(OpticsError) as caught:
        driver.get_app_version()
    assert caught.value.code == Code.E0401
    return str(caught.value)


class TestAndroid:
    def test_the_version_name_of_the_app(self, monkeypatch, device):
        ran = device("  versionCode=7\n    versionName=1.2.3\n")
        assert started(monkeypatch, ANDROID, {"deviceUDID": "emulator-5554"}).get_app_version() == "1.2.3"
        assert ran() == ["adb -s emulator-5554 shell pm dump in.app"]

    def test_no_adb(self, monkeypatch, device):
        device(installed=False)
        assert "Output:\n<no output>" in version_error(started(monkeypatch, ANDROID, {}))


class TestIos:
    def test_the_bundle_version_of_the_app(self, monkeypatch, device):
        ran = device('org.other, "1.0", "Other"\nin.app, "4.5.6", "Example"\n')
        assert started(monkeypatch, IOS, {"udid": "00008"}).get_app_version() == "4.5.6"
        assert ran() == ["ideviceinstaller -u 00008 -l"]

    def test_a_listing_without_the_app_is_shortened(self, monkeypatch, device):
        device("\n".join(f"line {index}" for index in range(400)))
        message = version_error(started(monkeypatch, IOS, {}))
        assert "line 9\n… (380 lines omitted)\nline 390" in message

    def test_a_failed_listing_without_output(self, monkeypatch, device):
        device(status=1)
        assert "Output:\n<no output>" in version_error(started(monkeypatch, IOS, {}))

    def test_no_ideviceinstaller(self, monkeypatch, device):
        device(installed=False)
        assert "brew install ideviceinstaller" in version_error(started(monkeypatch, IOS, {}))
