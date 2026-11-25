import os
import stat
import sys

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from optics_framework.common import expose_api
from optics_framework.helper.doctor import check_usb_screen
from optics_framework.helper.execute import run_project

airhand = pytest.importorskip("airhand")
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake adb and the fake dongle need a POSIX shell and a pty")

from airhand import _dongle  # noqa: E402
from airhand.testing import FakeDongle  # noqa: E402

WIDTH, HEIGHT = 360, 780
BUTTON = (200, 500, 300, 560)

FAKE_ADB = """#!/bin/sh
echo "$*" >> "$FAKE_ADB_PHONE/adb.log"
case "$*" in
    devices) echo 'List of devices attached'; cat "$FAKE_ADB_PHONE/devices" ;;
    *" exec-out screencap -p") cat "$FAKE_ADB_PHONE/screen.png" ;;
    *) exit 1 ;;
esac
"""


def _screen() -> np.ndarray:
    screen = np.full((HEIGHT, WIDTH, 3), 235, np.uint8)
    for row in range(40, HEIGHT - 40, 70):
        cv2.rectangle(screen, (30, row), (30 + (row * 3) % 200, row + 20), (60, 60, 60), -1)
    x1, y1, x2, y2 = BUTTON
    cv2.rectangle(screen, (x1, y1), (x2, y2), (30, 120, 220), -1)
    cv2.circle(screen, ((x1 + x2) // 2, (y1 + y2) // 2), 18, (250, 250, 250), -1)
    return screen


@pytest.fixture(scope="session")
def adb_tools(tmp_path_factory):
    """One fake adb for the session: macOS checks a new executable on its first run, which costs about 0.4 s."""
    tools = tmp_path_factory.mktemp("tools")
    adb = tools / "adb"
    adb.write_text(FAKE_ADB)
    adb.chmod(adb.stat().st_mode | stat.S_IXUSR)
    return tools


@pytest.fixture
def phone(tmp_path, monkeypatch, adb_tools):
    """A phone on USB: the fake adb lists ``devices`` and returns the screen as PNG."""
    monkeypatch.setenv("PATH", f"{adb_tools}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_ADB_PHONE", str(tmp_path))
    log = tmp_path / "adb.log"
    log.write_text("")

    def plug(devices=("PHONE1",), size=(WIDTH, HEIGHT)):
        cv2.imwrite(str(tmp_path / "screen.png"), cv2.resize(_screen(), size))
        (tmp_path / "devices").write_text("".join(f"{serial}\tdevice\n" for serial in devices))
        return log

    return plug


def test_a_ble_project_reads_the_screen_over_usb_and_taps_through_the_dongle(phone, tmp_path, monkeypatch):
    monkeypatch.setattr(_dongle, "HOST_WAIT_S", 0.5)
    adb_log = phone()
    x1, y1, x2, y2 = BUTTON
    (tmp_path / "project").mkdir()
    project = tmp_path / "project"
    cv2.imwrite(str(project / "button.png"), _screen()[y1:y2, x1:x2])
    for folder, text in {
        "test_cases": "test_case,test_step\nTap the button,Tap it\n",
        "modules": "module_name,module_step,param_1,param_2\nTap it,Assert Presence,button.png,5\nTap it,Press Element,button.png,\n",
    }.items():
        (project / folder).mkdir()
        (project / folder / f"{folder}.csv").write_text(text)
    with FakeDongle(firmware="airhand 0.1.6") as dongle:
        (project / "config.yaml").write_text(
            "driver_sources:\n"
            f"  - ble: {{enabled: true, capabilities: {{port: '{dongle.port}', platform: android, frame: {{width: {WIDTH}, height: {HEIGHT}}}}}}}\n"
            "elements_sources:\n"
            "  - usb_screenshot: {enabled: true}\n"
            "image_detection:\n"
            "  - templatematch: {enabled: true}\n"
            "json_log: false\n"
        )
        assert run_project(str(project), use_printer=False)
        taps = [words for words in dongle.commands if words[0] == "tap"]
    assert taps == [("tap", f"{(x1 + x2) / 2 / WIDTH:.4f}", f"{(y1 + y2) / 2 / HEIGHT:.4f}")]
    assert set(adb_log.read_text().splitlines()) == {"devices", "-s PHONE1 exec-out screencap -p"}


@pytest.mark.parametrize(
    ("devices", "size", "usb_caps", "status", "says"),
    [
        (("PHONE1",), (WIDTH, HEIGHT), {}, "ok", f"{WIDTH}x{HEIGHT} from PHONE1"),
        ((), (WIDTH, HEIGHT), {}, "fail", "attached: none"),
        (("PHONE1", "PHONE2"), (WIDTH, HEIGHT), {}, "fail", "attached: PHONE1, PHONE2"),
        (("PHONE1", "PHONE2"), (WIDTH, HEIGHT), {"serial": "PHONE2"}, "ok", "from PHONE2"),
        (("PHONE1",), (WIDTH * 2, HEIGHT * 2), {}, "fail", f"the screenshot is {WIDTH * 2}x{HEIGHT * 2}"),
    ],
    ids=["one phone", "no phone", "two phones", "two phones and a serial", "frame of another size"],
)
def test_doctor_checks_the_usb_screen(phone, devices, size, usb_caps, status, says):
    phone(devices, size)
    [row] = check_usb_screen(usb_caps, {"platform": "android", "frame": {"width": WIDTH, "height": HEIGHT}})
    assert (row.status, says in row.detail) == (status, True)


def test_doctor_names_a_missing_adb(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    [row] = check_usb_screen({}, {"platform": "android", "frame": {"width": WIDTH, "height": HEIGHT}})
    assert row.status == "fail"
    assert "needs adb" in row.detail


def test_optics_serve_refuses_usb_screenshots_from_a_remote_caller(monkeypatch):
    monkeypatch.setattr(expose_api, "server_reads_allowed", False)
    before = set(expose_api.session_manager.sessions)
    with TestClient(expose_api.app) as client:
        resp = client.post("/v1/sessions/start", json={"driver_sources": ["playwright"], "elements_sources": ["usb_screenshot"]})
    assert resp.status_code >= 400
    assert "E0501" in resp.text
    assert "usb_screenshot" in resp.text
    assert set(expose_api.session_manager.sessions) == before
