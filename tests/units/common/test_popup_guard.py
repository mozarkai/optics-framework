"""PopupGuard against fake screens: no device, no Appium."""
from xml.sax.saxutils import quoteattr

import pytest

from optics_framework.common.error import Code
from optics_framework.engines.drivers.appium_popup import AppiumScreen
from optics_framework.common.popup_guard import PopupGuard, PopupIO, PopupRun, Verdict
from tests.units.popup_helpers import rule


def node(tag="android.widget.TextView", **attrs):
    return f"<{tag} " + " ".join(f"{k.replace('_', '-')}={quoteattr(v)}" for k, v in attrs.items()) + "/>"


def android(*children):
    return "<hierarchy>" + "".join(children) + "</hierarchy>"


PLAIN = android(node(text="Balance"), node(text="Transfer"))
RATE_US = android(node(text="Balance", package="in.app"), node(resource_id="android:id/alertTitle", text="Rate Us"),
                  node("android.widget.Button", text="Maybe Later"), node("android.widget.Button", text="Rate Now"))
SESSION = android(node(text="Your session is about to expire"), node("android.widget.Button", text="Continue Session"))
IOS_ALERT = "<AppiumAUT><XCUIElementTypeAlert name='Allow notifications?' label='Allow notifications?'>" \
            "<XCUIElementTypeButton name='Allow' label='Allow'/></XCUIElementTypeAlert></AppiumAUT>"


class Device:
    """A screen that changes when a tap hits ``dismisses``, and keeps a record of what was done to it."""

    def __init__(self, source, after=PLAIN, dismisses=("Maybe Later", "Continue Session", "Close"), read_fails=False,
                 dismiss_points=(), system_alert=None):
        self.system_alert = system_alert
        self.source, self.after, self.dismisses, self.read_fails = source, after, set(dismisses), read_fails
        self.dismiss_points = set(dismiss_points)
        self.reads = 0
        self.modules = []
        self.on_module = None
        self.taps, self.points, self.backs, self.alerts, self.saved_images, self.saved_text = [], [], 0, [], [], []

    def read_source(self):
        self.reads += 1
        if self.read_fails:
            raise RuntimeError("E0403 no page source")
        return self.source

    def tap(self, target):
        self.taps.append(target)
        if target in self.dismisses:
            self.source = self.after
            return True
        return False

    def tap_at(self, x, y):
        self.points.append((x, y))
        if (x, y) in self.dismiss_points:
            self.source = self.after

    def back(self):
        self.backs += 1
        self.source = self.after

    def alert(self, button):
        self.alerts.append(button)
        self.source = self.after
        self.system_alert = None

    def run_module(self, name):
        self.modules.append(name)
        if self.on_module:
            self.on_module(name)

    def read_alert(self):
        self.alert_reads += 1
        if self.read_fails_alert:
            raise RuntimeError("no alert API")
        return self.system_alert

    read_fails_alert = False
    alert_reads = 0

    def io(self):
        return PopupIO(
            read_source=self.read_source, screen_of=AppiumScreen, tap=self.tap, tap_at=self.tap_at, back=self.back, alert=self.alert, run_module=self.run_module, read_alert=self.read_alert,
            screenshot=lambda: "frame",
            save_image=lambda img, name: self.saved_images.append(name) or f"{name}.png",
            save_text=lambda text, name: self.saved_text.append((name, text)) or f"{name}.xml",
        )


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def guard(device, *rules, run=None):
    run = run or PopupRun()
    clock = Clock()
    g = PopupGuard(lambda: list(rules), device.io(), lambda: run, sleep=clock.sleep, clock=clock)
    g.begin_step("press_element", "Balance card")
    return g, run


def test_a_popup_a_rule_dismisses_is_handled_and_the_step_may_retry():
    device = Device(RATE_US)
    g, run = guard(device, rule())
    assert g.on_failure().verdict is Verdict.HANDLED
    assert device.taps == ["Maybe Later"]
    event, = run.drain()
    assert (event["verdict"], event["rule"], event["popup_gone"], event["check"]) == ("handled", "Rate Us", True, "on_failure")
    assert event["action"] == "tap Maybe Later"
    assert event["matched"] == ["text_contains"]
    assert event["step_count"] == 1
    assert event["screenshots"] == [f"popup_{run.id}_1_before.png", f"popup_{run.id}_1_after.png"]
    assert (event["keyword"], event["element"], event["platform"], event["app_id"]) == ("press_element", "Balance card", "android", "in.app")
    assert run.count("Rate Us", Verdict.HANDLED) == 1


@pytest.mark.parametrize("detect, hit", [
    ({"text_contains": ["RATE us"]}, True),
    ({"text_contains": ["nothing"]}, False),
    ({"resource_id": "android:id/alertTitle"}, True),
    ({"xpath": "//android.widget.Button[@text='Nope']"}, False),
    ({"text_contains": ["Rate Us"], "xpath": "//*[@text='Nope']"}, False),
    ({"text_contains": ["Rate Us"], "resource_id": "android:id/alertTitle", "xpath": "//*[@text='Rate Now']"}, True),
])
def test_a_rule_matches_only_when_every_detect_key_given_does(detect, hit):
    g, _ = guard(Device(RATE_US), rule(detect=detect))
    assert (g.on_failure().verdict is Verdict.HANDLED) is hit


def test_the_first_rule_in_the_list_that_matches_wins():
    device = Device(RATE_US)
    g, run = guard(device, rule("First"), rule("Second", action={"type": "tap", "target": "Rate Now"}))
    g.on_failure()
    assert run.drain()[0]["rule"] == "First"
    assert device.taps == ["Maybe Later"]


def test_a_popup_that_follows_another_counts_against_its_own_rule_only():
    update = android(node(resource_id="android:id/alertTitle", text="New update"), node("android.widget.Button", text="Not now"))
    device = Device(RATE_US, after=update, dismisses=("Maybe Later", "Not now"))
    later = rule("Update", detect={"text_contains": ["New update"]}, action={"type": "tap", "target": "Not now"}, max_per_step=1)
    g, run = guard(device, rule(max_per_step=1), later)
    assert g.on_failure().verdict is Verdict.HANDLED
    device.after = PLAIN
    assert g.on_failure().verdict is Verdict.HANDLED
    assert [(e["rule"], e["step_count"]) for e in run.drain()] == [("Rate Us", 1), ("Update", 1)]


def test_a_popup_that_stays_after_the_tap_fails_the_step_with_e0408_after_waiting_for_it():
    device = Device(RATE_US, after=RATE_US)
    g, run = guard(device, rule())
    out = g.on_failure()
    assert (out.verdict, out.code, out.stop_run) == (Verdict.STOP, Code.E0408, False)
    assert device.reads > 3
    assert run.drain()[0]["popup_gone"] is False


def test_the_per_step_and_the_per_run_limits_fail_the_step_with_e0407_without_tapping_again():
    device = Device(RATE_US)
    g, run = guard(device, rule(max_per_step=2, max_per_run=3))
    codes, last = [], None
    for element in ("a", "a", "a", "b", "b"):
        if element != last:
            g.begin_step("press_element", last := element)
        device.source = RATE_US
        codes.append(g.on_failure().code)
    assert codes == [None, None, Code.E0407, None, Code.E0407], "step a stops at 2, and the run stops at 3"
    assert len(device.taps) == 3
    assert run.count("Rate Us", Verdict.LIMIT) == 2


def test_a_rule_can_run_a_module_to_dismiss_the_popup():
    device = Device(RATE_US)
    device.on_module = lambda name: setattr(device, "source", PLAIN)
    g, run = guard(device, rule(action={"type": "module", "target": "Dismiss Rate Us"}))
    assert g.on_failure().verdict is Verdict.HANDLED
    assert device.modules == ["Dismiss Rate Us"]
    assert device.taps == []
    assert run.drain()[0]["action"] == "module Dismiss Rate Us"


def test_a_module_that_leaves_the_popup_up_or_fails_stops_the_step():
    stays = Device(RATE_US)
    g, _ = guard(stays, rule(action={"type": "module", "target": "Nothing"}))
    assert g.on_failure().code == Code.E0408
    broken = Device(RATE_US)
    broken.on_module = lambda name: (_ for _ in ()).throw(RuntimeError("module failed"))
    g, _ = guard(broken, rule(action={"type": "module", "target": "Broken"}))
    assert g.on_failure().code == Code.E0408


def test_a_popup_inside_the_module_is_handled_too_and_the_outer_step_keeps_its_own_count():
    device = Device(RATE_US, after=PLAIN, dismisses=("Accept all",))
    cookies = android(node(text="Balance", package="in.app"), node(resource_id="android:id/alertTitle", text="We use cookies"),
                      node("android.widget.Button", text="Accept all"))
    g, run = guard(
        device, rule(max_per_step=2, action={"type": "module", "target": "Dismiss Rate Us"}),
        rule("Cookies", detect={"text_contains": ["We use cookies"]}, action={"type": "tap", "target": "Accept all"}),
    )

    def module(name):
        device.source = cookies
        g.begin_step("tap", "Close")
        assert g.on_failure().verdict is Verdict.HANDLED, "the popup inside the module met the cookies rule"
        device.source = PLAIN

    device.on_module = module
    assert g.on_failure().verdict is Verdict.HANDLED
    assert (run.count("Rate Us"), run.count("Cookies")) == (1, 1)
    device.source = RATE_US
    assert g.on_failure().verdict is Verdict.HANDLED
    device.source = RATE_US
    assert g.on_failure().code == Code.E0407, "the outer count went on from 2 handled, though the module's steps began their own"


def test_a_popup_the_rules_own_module_is_working_on_is_left_to_that_module_and_the_rule_does_not_start_again():
    device = Device(RATE_US)
    g, run = guard(device, rule(check="before_every_step", action={"type": "module", "target": "Dismiss Rate Us"}))
    inner = []

    def module(name):
        g.begin_step("press_element", "Maybe Later")
        inner.extend([g.before_step().verdict, g.on_failure().verdict])
        device.source = PLAIN

    device.on_module = module
    assert g.before_step().verdict is Verdict.HANDLED
    assert inner == [Verdict.NONE, Verdict.NONE], "the module's own steps are not stopped by the popup they dismiss"
    assert device.modules == ["Dismiss Rate Us"]
    assert run.count("Rate Us") == 1


@pytest.mark.parametrize("then, ends", [("fail_step", False), ("stop_run", True)])
def test_a_rule_that_ends_the_step_or_the_run_dismisses_the_popup_first(then, ends):
    device = Device(RATE_US)
    g, run = guard(device, rule(then=then))
    out = g.on_failure()
    assert (out.verdict, out.code, out.stop_run) == (Verdict.STOP, Code.E0408, ends)
    assert device.taps == ["Maybe Later"]
    assert bool(run.stop) is ends
    assert run.drain()[0]["stop_run"] is ends


@pytest.mark.parametrize("stopper", [rule(action={"type": "none"}, then="stop_run"), rule("Another device", sensitive=True)])
def test_a_rule_that_only_stops_the_run_presses_nothing_and_keeps_the_evidence(stopper):
    device = Device(RATE_US)
    g, run = guard(device, stopper)
    out = g.on_failure()
    assert (out.verdict, out.stop_run) == (Verdict.STOP, True)
    assert run.stop
    assert (device.taps, device.backs, device.alerts) == ([], 0, [])
    assert device.saved_images == [f"popup_{run.id}_1_before"]
    assert device.saved_text == [(f"popup_{run.id}_1_source", RATE_US)]


CAMERA_PROMPT = "<hierarchy><android.widget.FrameLayout package='com.android.permissioncontroller'><n text='Allow Camera?'/></android.widget.FrameLayout></hierarchy>"


@pytest.mark.parametrize("source, kind, text", [
    (RATE_US, "in_app", "Rate Us"),
    (CAMERA_PROMPT, "system_alert", "Allow Camera?"),
    (IOS_ALERT, "system_alert", "Allow notifications? Allow"),
])
def test_a_dialog_no_rule_covers_is_an_unknown_popup_with_evidence_and_nothing_pressed(source, kind, text):
    device = Device(source)
    g, run = guard(device, rule("Other", detect={"text_contains": ["something else"]}))
    out = g.on_failure()
    assert (out.verdict, out.code) == (Verdict.UNKNOWN_POPUP, Code.E0206)
    assert device.taps == []
    assert device.saved_text
    event, = run.drain()
    assert (event["verdict"], event["popup_kind"], event["popup_text"]) == ("unknown", kind, text)
    assert run.unknown[0]["element"] == "Balance card"


@pytest.mark.parametrize("device", [Device(PLAIN), Device(RATE_US, read_fails=True), Device("not xml")])
def test_a_screen_without_a_popup_or_one_that_cannot_be_read_is_no_popup_and_leaves_one_event(device):
    g, run = guard(device, rule())
    assert g.on_failure().verdict is Verdict.NONE
    assert device.taps == []
    assert [e["verdict"] for e in run.drain()] == ["none"]


def test_before_step_reads_nothing_without_a_rule_that_opts_in_and_handles_the_popup_of_one_that_does():
    device = Device(SESSION)
    g, _ = guard(device, rule())
    assert g.before_step().verdict is Verdict.NONE
    assert device.reads == 0
    session = rule("Session", check="before_every_step", detect={"text_contains": ["about to expire"]},
                   action={"type": "tap", "target": "Continue Session"})
    g, run = guard(device, session)
    assert g.before_step().verdict is Verdict.HANDLED
    assert device.taps == ["Continue Session"]
    assert run.drain()[0]["check"] == "before_every_step"


BOUNDED = android(node(text="Balance", package="in.app"), node(resource_id="android:id/alertTitle", text="Rate Us"),
                  node("android.widget.Button", resource_id="in.app:id/later", text="Maybe Later", bounds="[100,200][300,260]"),
                  node("android.widget.Button", text="Rate Now", bounds="[400,200][600,260]"))


@pytest.mark.parametrize("target, point", [
    ("maybe  LATER", (200, 230)),
    ("in.app:id/later", (200, 230)),
    ("xpath=//android.widget.Button[@text='Rate Now']", (500, 230)),
    ("id:later", (200, 230)),
    ("text_only:Maybe Later", (200, 230)),
])
def test_a_tap_target_is_found_in_the_page_source_the_guard_already_holds_and_tapped_at_its_centre(target, point):
    device = Device(BOUNDED, dismiss_points=[point])
    g, _ = guard(device, rule(action={"type": "tap", "target": target}))
    assert g.on_failure().verdict is Verdict.HANDLED
    assert device.points == [point]
    assert device.taps == [], "no locate ladder was needed"


def test_a_target_not_in_the_page_source_is_left_to_the_locate_ladder_and_the_fallback_is_still_tried():
    device = Device(BOUNDED, dismiss_points=[(200, 230)])
    g, _ = guard(device, rule(action={"type": "tap", "target": "Not on screen", "fallback": "Maybe Later"}))
    assert g.on_failure().verdict is Verdict.HANDLED
    assert device.taps == ["Not on screen"]
    assert device.points == [(200, 230)]


NOTIFICATIONS = ("Allow “Maps” to use your location?", ["Allow Once", "Allow While Using App", "Don’t Allow"])


def test_a_system_alert_ios_keeps_out_of_the_page_source_is_read_through_the_driver_and_answered():
    device = Device("<AppiumAUT><XCUIElementTypeApplication name='Maps'/></AppiumAUT>", system_alert=NOTIFICATIONS)
    g, run = guard(device, rule("Location", detect={"text_contains": ["use your location"]},
                                action={"type": "alert", "target": "Allow While Using App"}))
    assert g.on_failure().verdict is Verdict.HANDLED
    assert device.alerts == ["Allow While Using App"]
    assert device.taps == []
    assert device.points == []
    event, = run.drain()
    assert (event["popup_kind"], event["platform"], event["app_id"]) == ("system_alert", "ios", "Maps")


def test_an_unplanned_system_alert_is_an_unknown_popup_with_its_text_and_buttons_in_the_evidence():
    device = Device("<AppiumAUT/>", system_alert=NOTIFICATIONS)
    g, run = guard(device, rule("Other", detect={"text_contains": ["zzz"]}))
    out = g.on_failure()
    assert out.verdict is Verdict.UNKNOWN_POPUP
    assert device.alerts == []
    assert "Don’t Allow" in device.saved_text[0][1]
    assert run.unknown[0]["popup_text"].startswith("Allow “Maps”")
