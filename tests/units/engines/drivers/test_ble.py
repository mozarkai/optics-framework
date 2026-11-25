import json
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


PAIRED = [("20:32:C6:02:7B:47", "iPhone"), ("78:ED:BC:25:5A:24", "OnePlus 15")]


def test_the_driver_takes_the_link_for_the_host_it_names(plugged):
    fake = plugged(firmware="airhand 0.1.6", hosts=PAIRED)
    make_driver(fake, host="oneplus 15")
    assert ("use", "78:ED:BC:25:5A:24") in fake.commands


@pytest.mark.parametrize(
    ("firmware", "host", "says"),
    [("airhand 0.1.6", "Galaxy", "iPhone (20:32:C6:02:7B:47)"), ("airhand 0.1.5", "iPhone", "airhand update")],
)
def test_a_host_the_dongle_cannot_choose_stops_the_session(plugged, firmware, host, says):
    fake = plugged(firmware=firmware, hosts=PAIRED)
    with pytest.raises(OpticsError) as stopped:
        make_driver(fake, host=host)
    assert stopped.value.code == Code.E0501
    assert says in str(stopped.value)
    assert not any(words[0] == "use" and words[1] != "any" for words in fake.commands if len(words) > 1)


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


def _row(text, x, y):
    return {"text": text, "bounds": {"x1": x, "y1": y, "x2": x + 30 * len(text), "y2": y + 40}}


class Launcher:
    """A phone whose launcher reacts to the dongle as one kind of real launcher does."""

    APPS = [f"App {n:02}" for n in range(45)] + ["Settings"] + [f"Tool {n:02}" for n in range(6)]

    def __init__(self, fake, opens_list=True, types=False, box_y=None, home=()):
        self.fake, self.opens_list, self.types, self.box_y, self.home = fake, opens_list, types, box_y, home
        self.seen, self.screen, self.query, self.focused, self.page = 0, "home", "", False, 0

    def _rows(self):
        if self.screen == "web":
            return [_row("12:00", 40, 30), _row(self.query or "Search the web", 40, 200), *([_row(self.query, 40, 400)] if self.query else [])]
        if self.screen != "list":
            return [_row("12:00", 40, 30), *({"home": [_row("Search", 300, 2200), *self.home]}.get(self.screen, [_row(self.screen, 40, 150)]))]
        rows = [_row("12:00", 40, 30), _row("All", 300, 150), _row("Categories", 450, 150), _row("Frequently used", 40, 420)]
        if self.box_y:
            rows.append(_row(self.query or "Search", 300, self.box_y))
        if self.query:
            return [*rows, _row(self.query, 40, 900), _row(self.query, 40, 1300)]
        return rows + [_row(app, 40 + 260 * (i % 4), 500 + 350 * (i // 4)) for i, app in enumerate(self.APPS[self.page * 20:][:20])]

    def _tap(self, x, y):
        for row in self._rows():
            b = row["bounds"]
            if row["text"] in self.APPS and b["x1"] - 40 <= x <= b["x2"] + 40 and b["y1"] - 160 <= y <= b["y1"]:
                self.screen = row["text"]
        self.focused = self.screen == "list" and self.box_y is not None and abs(y - self.box_y - 20) < 60
        if self.screen == "home" and abs(y - 2220) < 60:
            self.screen = "web"

    def _takes_text(self):
        return self.screen == "web" or self.screen == "list" and (self.types or self.focused)

    def _apply(self, command, args):
        if command == "media" and args == ["home"]:
            self.screen, self.query, self.focused, self.page = "home", "", False, 0
        elif (command, args) == ("key", ["meta"]) and self.opens_list:
            self.screen = "list"
        elif command == "text" and self._takes_text():
            self.query = json.loads(args[0])
        elif command == "tap":
            self._tap(float(args[0]) * 1080, float(args[1]) * 2340)
        elif command == "swipe" and self.screen == "list":
            self.page = min(self.page + 1, len(self.APPS) // 20)
        elif (command, args) == ("key", ["enter"]) and self.query in self.APPS and self.screen in ("list", "web"):
            self.screen = self.query if self.screen == "list" else "Web results"

    def read(self):
        for command, *args in self.fake.commands[self.seen:]:
            self._apply(command, args)
        self.seen = len(self.fake.commands)
        return self._rows()


@pytest.mark.parametrize("launcher", [
    {"types": True, "box_y": 200},
    {"box_y": 2100},
    {"box_y": 200},
    {},
    {"opens_list": False, "home": [_row("Phone", 100, 1900), _row("Settings", 400, 1900)]},
], ids=["types into its app list", "searches from a box at the bottom", "searches from a box at the top", "has no search box", "has no app list"])
def test_launch_app_opens_the_app_on_any_kind_of_launcher(plugged, launcher):
    fake = plugged()
    phone = Launcher(fake, **launcher)
    driver = make_driver(fake)
    driver.attach_screen(phone.read)
    for _ in range(2):
        before = len(fake.commands)
        driver.launch_app("Settings")
        assert phone.read()[1]["text"] == "Settings"
    assert any(command[0] == "swipe" for command in fake.commands) == (launcher == {})
    assert any(command[0] == "text" for command in fake.commands[before:]) == ("box_y" in launcher)
