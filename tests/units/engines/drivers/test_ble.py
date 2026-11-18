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
TOUCH = ("mode", "touch")


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


def test_keywords_reach_the_dongle_as_screen_fractions(plugged):
    fake = plugged()
    driver = make_driver(fake)

    driver.press_coordinates(540, 585)
    driver.swipe(540, 2000, "up", 5000)
    driver.scroll("down", 400)

    assert fake.commands == [
        TOUCH,
        ("tap", "0.5000", "0.2500"),
        ("swipe", "0.5000", "0.8547", "0.5000", "0.0000", "1000"),
        ("swipe", "0.5000", "0.8000", "0.5000", "0.2000", "4000"),
    ]


def test_a_short_swipe_is_sent_fast_enough_to_drag(plugged):
    fake = plugged()
    make_driver(fake, "ios").swipe_percentage(50, 60, "up", 20)
    assert fake.commands == [("swipe", "0.5000", "0.6000", "0.5000", "0.4000", "267"), PARK]


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("android", [TOUCH, ("key", "ctrl+a"), ("key", "backspace"), ("media", "back"), ("media", "home")]),
        ("macos", [("key", "cmd+a"), ("key", "backspace"), ("key", "cmd+["), ("key", "home")]),
        ("windows", [("key", "ctrl+a"), ("key", "backspace"), ("key", "alt+left"), ("key", "home")]),
    ],
)
def test_each_platform_has_its_own_select_all_back_and_home(plugged, platform, expected):
    fake = plugged()
    driver = make_driver(fake, platform)
    driver.clear_text()
    driver.go_back()
    driver.press_keycode("home")
    assert fake.commands == expected


def test_media_keys_reach_a_tv_or_computer(plugged):
    fake = plugged()
    driver = make_driver(fake, "linux")
    for name in ("mute", "playpause", "volumeup", "brightnessdown", "cmd+space", "f5"):
        driver.press_keycode(name)
    assert fake.commands == [
        ("media", "mute"),
        ("media", "playpause"),
        ("media", "volup"),
        ("media", "brightnessdown"),
        ("key", "cmd+space"),
        ("key", "f5"),
    ]


def test_ios_has_no_back_key(plugged):
    fake = plugged()
    driver = make_driver(fake, "ios")
    assert not driver.supports("go_back")
    for back in (driver.go_back, lambda: driver.press_keycode("back")):
        with pytest.raises(OpticsError) as refused:
            back()
        assert refused.value.code == Code.E0105
    assert fake.commands == []


IOS_HOME = [("media", "home"), ("media", "home")]


@pytest.mark.parametrize(
    ("platform", "launcher", "home"), [("ios", "cmd+space", IOS_HOME), ("macos", "cmd+space", []), ("windows", "ctrl+escape", [])]
)
def test_apps_open_through_the_system_search(plugged, platform, launcher, home):
    fake = plugged()
    driver = make_driver(fake, platform)
    assert driver.supports("launch_app")
    driver.launch_app(" Settings ")
    driver.launch_other_app("Notes", None)
    assert fake.commands == [
        *home, ("key", launcher), ("text", '"Settings"'), ("key", "enter"),
        *home, ("key", launcher), ("text", '"Notes"'), ("key", "enter"),
    ]
    with pytest.raises(OpticsError) as unnamed:
        driver.launch_other_app(" ", None)
    assert unnamed.value.code == Code.E0403


@pytest.mark.parametrize(("platform", "expected"), [("android", [TOUCH]), ("ios", [])])
def test_a_session_start_without_an_app_keeps_the_screen(plugged, platform, expected):
    fake = plugged()
    make_driver(fake, platform).launch_app()
    assert fake.commands == expected


def test_android_opens_apps_from_the_app_list(plugged):
    fake = plugged()
    make_driver(fake).launch_app("Outlook")
    make_driver(fake, app_search={"x": 540, "y": 2200}).launch_app("Outlook")
    opened = [("media", "home"), ("key", "meta")]
    assert fake.commands == [
        TOUCH, *opened, ("text", '"Outlook"'), ("key", "enter"),
        TOUCH, *opened, ("tap", "0.5000", "0.9402"), ("text", '"Outlook"'), ("key", "up"), ("key", "enter"),
    ]


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
    refused = set()
    for key in {k.value for k in SpecialKey} - ble.NO_HID_KEY:
        try:
            driver.press_keycode(key)
        except OpticsError as exc:
            assert exc.code == Code.E0403, (key, exc)
            refused.add(key)
    assert refused == OUTSIDE_THE_FIRMWARE


@pytest.mark.parametrize(
    ("behaviour", "send", "code"),
    [
        ({"host_connected": False}, lambda d: d.press_coordinates(10, 10), Code.E0106),
        ({"unencrypted_replies": 10**6}, lambda d: d.press_coordinates(10, 10), Code.E0106),
        ({"silent_after": 1}, lambda d: d.press_coordinates(10, 10), Code.E0106),
        ({}, lambda d: d.enter_text("price €5"), Code.E0403),
    ],
)
def test_dongle_errors_become_optics_codes(plugged, behaviour, send, code):
    driver = make_driver(plugged(**behaviour))
    with pytest.raises(OpticsError) as refused:
        send(driver)
    assert refused.value.code == code
    assert driver.is_dead_session_error(refused.value) == (code == Code.E0106)


def test_other_firmware_asks_for_a_flash(plugged):
    fake = plugged(firmware="other 2.0.0")
    with pytest.raises(OpticsError) as refused:
        make_driver(fake)
    assert refused.value.code == Code.E0501


def test_v1_capabilities_point_to_the_migration_note(plugged):
    fake = plugged()
    with pytest.raises(OpticsError) as refused:
        make_driver(fake, mickeys_width=1080)
    assert refused.value.code == Code.E0503
    assert "Migrating from the v1 ble driver" in str(refused.value)


def test_element_keywords_are_offered_only_where_they_run(plugged):
    driver = make_driver(plugged(), "linux")
    assert driver.supports("press_coordinates")
    assert driver.supports("go_back")
    for refused in ("press_element", "get_text_element", "launch_app", "force_terminate_app", "get_app_version", "execute_script"):
        assert not driver.supports(refused)


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


def test_a_screen_keyword_without_a_camera_says_what_is_missing(plugged, tmp_path):
    from optics_framework.helper.execute import run_project

    folder = blind_project(tmp_path, plugged().port, [("Press Element", "Continue")])

    assert not run_project(folder, use_printer=False)
    junit = (tmp_path / "execution_output" / "junit_output.xml").read_text()
    assert 'code="E0501"' in junit
    assert "elements_sources" in junit
