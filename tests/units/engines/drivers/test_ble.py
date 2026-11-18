import sys

import pytest

airhand = pytest.importorskip("airhand")
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake dongle runs on a pty")

from airhand import _dongle  # noqa: E402
from airhand.testing import FakeDongle  # noqa: E402

from optics_framework.common.error import Code, OpticsError  # noqa: E402
from optics_framework.common.utils import SpecialKey  # noqa: E402
from optics_framework.engines.drivers import ble  # noqa: E402
from optics_framework.engines.drivers.ble import BLEDriver  # noqa: E402

OUTSIDE_THE_FIRMWARE = {"numpadequal", "numpadcomma"}
PARK = ("move", "1.0000", "0.0000")


@pytest.fixture
def plugged(monkeypatch):
    monkeypatch.setattr(_dongle, "HOST_WAIT_S", 0.5)
    monkeypatch.setattr(_dongle, "REPLY_WAIT_S", 0.3)
    for pause in ("SEARCH_OPEN_S", "SEARCH_RESULTS_S", "APP_OPEN_S", "HOME_TWICE_S", "HOME_SETTLE_S", "SEARCH_CLOSED_S", "SWITCHER_OPEN_S", "FOCUS_MOVE_S"):
        monkeypatch.setattr(ble, pause, 0)
    fakes: list[FakeDongle] = []

    def plug(**behaviour) -> FakeDongle:
        fakes.append(FakeDongle(**behaviour))
        return fakes[-1]

    yield plug
    for fake in fakes:
        fake.unplug()


class Events:
    def __init__(self):
        self.captured: list[str] = []

    def capture_event(self, name):
        self.captured.append(name)

    def send_all_events(self):
        pass


def make_driver(fake: FakeDongle, platform="android", **caps) -> BLEDriver:
    capabilities = {"port": fake.port, "platform": platform, "frame": {"width": 1080, "height": 2340}, **caps}
    return BLEDriver(config={"capabilities": capabilities}, event_sdk=Events())


def test_a_short_swipe_is_sent_fast_enough_to_drag(plugged):
    fake = plugged()
    make_driver(fake, "ios").swipe_percentage(50, 60, "up", 20)
    assert fake.commands == [("swipe", "0.5000", "0.6000", "0.5000", "0.4000", "267"), PARK]


IOS_HOME = [("media", "home"), ("media", "home")]


def test_android_closes_an_app_from_recents(plugged):
    fake = plugged()
    make_driver(fake, app_search={"x": 540, "y": 2200}).force_terminate_app("Outlook")
    assert fake.commands[-4:] == [
        ("media", "home"), ("key", "meta+tab"), ("swipe", "0.5000", "0.7500", "0.5000", "0.0200", "250"), ("media", "home")
    ]


def test_ios_closes_an_app_from_the_app_switcher(plugged):
    fake = plugged()
    make_driver(fake, "ios").force_terminate_app("Settings")
    assert fake.commands == [
        *IOS_HOME, ("key", "cmd+space"), ("text", '"Settings"'), ("key", "enter"),
        *IOS_HOME, ("media", "home"), ("media", "home"),
        ("swipe", "0.5000", "0.5000", "0.5000", "0.0500", "250"), PARK,
        ("media", "home"),
    ]


def test_a_computer_scrolls_with_the_wheel(plugged):
    fake = plugged()
    driver = make_driver(fake, "linux")
    driver.scroll("down")
    driver.scroll("right")
    assert not driver.supports("launch_app")
    assert fake.commands == [("wheel", "-5"), ("wheel", "0", "5")]


def test_every_optics_key_reaches_the_dongle(plugged):
    driver = make_driver(plugged(), "android")
    refused = {}
    for key in {k.value for k in SpecialKey} - ble.NO_HID_KEY:
        try:
            driver.press_keycode(key)
        except OpticsError as exc:
            refused[key] = exc.code
    assert refused == dict.fromkeys(OUTSIDE_THE_FIRMWARE, Code.E0403)


def blind_project(folder, port, steps):
    (folder / "test_cases").mkdir()
    (folder / "modules").mkdir()
    (folder / "config.yaml").write_text(
        "driver_sources:\n"
        "  - ble:\n"
        "      enabled: true\n"
        f"      capabilities: {{port: '{port}', platform: ios, frame: {{width: 1080, height: 2340}}}}\n"
        "elements_sources: []\n"
        "json_log: false\n"
    )
    (folder / "test_cases" / "test_cases.csv").write_text("test_case,test_step\nDrive blind,Blind steps\n")
    rows = "".join(f"Blind steps,{','.join(step)}\n" for step in steps)
    (folder / "modules" / "modules.csv").write_text(f"module_name,module_step,param_1,param_2\n{rows}")
    return str(folder)


def test_a_project_without_a_camera_runs_by_coordinates_and_keys(plugged, tmp_path):
    from optics_framework.helper.execute import run_project

    fake = plugged()
    folder = blind_project(tmp_path, fake.port, [("Press By Percentage", "50", "50"), ("Enter Text Using Keyboard", "hi")])

    assert run_project(folder, use_printer=False)
    assert fake.commands == [("tap", "0.5000", "0.5000"), PARK, ("text", '"hi"'), ("reset",)]
