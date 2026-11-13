"""PopupGuard against fake screens: no device, no Appium."""
from xml.sax.saxutils import quoteattr

import pytest

from optics_framework.common.error import Code
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

    def read_alert(self):
        self.alert_reads += 1
        if self.read_fails_alert:
            raise RuntimeError("no alert API")
        return self.system_alert

    read_fails_alert = False
    alert_reads = 0

    def io(self):
        return PopupIO(
            read_source=self.read_source, tap=self.tap, tap_at=self.tap_at, back=self.back, alert=self.alert, read_alert=self.read_alert,
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


def test_a_guard_with_no_rules_is_inactive():
    g, _ = guard(Device(RATE_US))
    assert not g.active


def test_a_popup_a_rule_dismisses_is_handled_and_the_step_may_retry():
    device = Device(RATE_US)
    g, run = guard(device, rule())
    out = g.on_failure("step frame")
    assert out.verdict is Verdict.HANDLED and device.taps == ["Maybe Later"]
    event, = run.drain()
    assert (event["verdict"], event["rule"], event["popup_gone"], event["check"]) == ("handled", "Rate Us", True, "on_failure")
    assert event["action"] == "tap Maybe Later" and event["matched"] == ["text_contains"] and event["step_count"] == 1
    assert event["screenshots"] == ["popup_1_before.png", "popup_1_after.png"] and event["dismissed_in_ms"] is not None
    assert event["keyword"] == "press_element" and event["element"] == "Balance card"
    assert (event["platform"], event["app_id"]) == ("android", "in.app")
    assert run.count("Rate Us", Verdict.HANDLED) == 1


def test_the_steps_own_screenshot_is_the_before_image_and_none_is_taken_twice():
    device = Device(RATE_US)
    g, _ = guard(device, rule())
    g.on_failure("step frame")
    assert device.saved_images == ["popup_1_before", "popup_1_after"]


def test_the_fallback_is_tapped_when_the_target_is_not_found():
    device = Device(RATE_US, dismisses=("close_icon",))
    g, _ = guard(device, rule(action={"type": "tap", "target": "Maybe Later", "fallback": "close_icon"}))
    assert g.on_failure().verdict is Verdict.HANDLED
    assert device.taps == ["Maybe Later", "close_icon"]


def test_back_and_alert_actions():
    device = Device(RATE_US)
    g, _ = guard(device, rule(action={"type": "back"}))
    assert g.on_failure().verdict is Verdict.HANDLED and device.backs == 1
    ios = Device(IOS_ALERT)
    g, run = guard(ios, rule("Notifications", detect={"xpath": "//XCUIElementTypeAlert"}, action={"type": "alert", "target": "Allow"}))
    assert g.on_failure().verdict is Verdict.HANDLED and ios.alerts == ["Allow"]
    event = run.drain()[0]
    assert (event["popup_kind"], event["platform"]) == ("system_alert", "ios")


@pytest.mark.parametrize("detect, hit", [
    ({"text_contains": ["RATE us"]}, True),
    ({"text_contains": ["nothing", "Maybe Later"]}, True),
    ({"text_contains": ["nothing"]}, False),
    ({"resource_id": "android:id/alertTitle"}, True),
    ({"resource_id": "alertTitle"}, False),
    ({"xpath": "//android.widget.Button[@text='Rate Now']"}, True),
    ({"xpath": "//android.widget.Button[@text='Nope']"}, False),
    ({"xpath": "//[not xpath"}, False),
    ({"text_contains": ["Rate Us"], "xpath": "//*[@text='Nope']"}, False),
    ({"text_contains": ["Rate Us"], "resource_id": "android:id/alertTitle", "xpath": "//*[@text='Rate Now']"}, True),
])
def test_a_rule_matches_only_when_every_detect_key_given_does(detect, hit):
    device = Device(RATE_US)
    g, _ = guard(device, rule(detect=detect))
    # Nothing is dismissed here if it misses, but RATE_US has an alert node, so a miss is an unknown popup, not a handle.
    assert (g.on_failure().verdict is Verdict.HANDLED) is hit


def test_the_lowest_priority_number_that_matches_wins():
    device = Device(RATE_US)
    g, run = guard(device, rule("Late", priority=50, action={"type": "tap", "target": "Rate Now"}), rule("Early", priority=5))
    g.on_failure()
    assert run.drain()[0]["rule"] == "Early" and device.taps == ["Maybe Later"]


def test_a_popup_that_stays_after_the_tap_fails_the_step_with_e0208_and_waits_for_it():
    device = Device(RATE_US, after=RATE_US)
    g, run = guard(device, rule())
    out = g.on_failure()
    assert (out.verdict, out.code, out.stop_run) == (Verdict.STOP, Code.E0208, False)
    assert "still on screen" in out.message
    assert device.reads > 3, "polled for it to go before giving up"
    event, = run.drain()
    assert event["verdict"] == "stopped" and event["popup_gone"] is False and event["page_source"] == "popup_1_source.xml"


def test_a_target_that_cannot_be_tapped_leaves_the_popup_and_fails_the_step():
    g, _ = guard(Device(RATE_US, dismisses=()), rule())
    out = g.on_failure()
    assert (out.verdict, out.code) == (Verdict.STOP, Code.E0208)


def test_an_action_that_raises_is_a_popup_that_stays():
    device = Device(RATE_US)
    device.tap = lambda target: (_ for _ in ()).throw(RuntimeError("driver gone"))
    g, _ = guard(device, rule())
    assert g.on_failure().code == Code.E0208


def test_a_popup_that_keeps_coming_back_hits_the_per_step_limit_with_e0207_and_is_not_tapped_again():
    device = Device(RATE_US)
    g, run = guard(device, rule(max_per_step=2))
    for _ in range(2):
        device.source = RATE_US
        assert g.on_failure().verdict is Verdict.HANDLED
    device.source = RATE_US
    out = g.on_failure()
    assert (out.verdict, out.code, out.stop_run) == (Verdict.STOP, Code.E0207, False)
    assert device.taps == ["Maybe Later", "Maybe Later"]
    verdicts = [e["verdict"] for e in run.drain()]
    assert verdicts == ["handled", "handled", "limit"]
    assert run.count("Rate Us", Verdict.LIMIT) == 1


def test_the_per_run_limit_holds_across_steps_and_the_per_step_count_starts_again():
    device = Device(RATE_US)
    g, run = guard(device, rule(max_per_step=2, max_per_run=3))
    for step in range(3):
        g.begin_step("press_element", f"el{step}")
        device.source = RATE_US
        assert g.on_failure().verdict is Verdict.HANDLED
    g.begin_step("press_element", "el3")
    device.source = RATE_US
    assert g.on_failure().code == Code.E0207
    assert len(device.taps) == 3


def test_a_fail_step_rule_dismisses_the_popup_then_fails_only_the_step():
    device = Device(RATE_US)
    g, run = guard(device, rule(then="fail_step"))
    out = g.on_failure()
    assert (out.verdict, out.code, out.stop_run) == (Verdict.STOP, Code.E0208, False)
    assert device.taps == ["Maybe Later"] and run.stop is None


def test_a_stop_run_rule_acts_then_ends_the_run():
    device = Device(RATE_US)
    g, run = guard(device, rule(then="stop_run"))
    out = g.on_failure()
    assert (out.verdict, out.code, out.stop_run) == (Verdict.STOP, Code.E0208, True)
    assert device.taps == ["Maybe Later"] and run.stop and out.error().details == {"stop_run": True}


def test_a_stop_run_rule_with_no_action_presses_nothing():
    device = Device(RATE_US)
    g, run = guard(device, rule(action={"type": "none"}, then="stop_run"))
    out = g.on_failure()
    assert out.stop_run and device.taps == [] and device.backs == 0 and run.stop


def test_a_sensitive_rule_never_presses_anything_and_keeps_screenshot_and_page_source():
    device = Device(RATE_US)
    g, run = guard(device, rule("Another device", sensitive=True))
    out = g.on_failure()
    assert (out.verdict, out.code, out.stop_run) == (Verdict.STOP, Code.E0208, True)
    assert device.taps == [] and device.backs == 0 and device.alerts == []
    event, = run.drain()
    assert event["verdict"] == "stopped" and event["screenshots"] == ["popup_1_before.png"]
    assert device.saved_text == [("popup_1_source", RATE_US)]


def test_a_dialog_no_rule_covers_is_an_unknown_popup_with_evidence_and_nothing_pressed():
    device = Device(RATE_US)
    g, run = guard(device, rule("Other", detect={"text_contains": ["something else"]}))
    out = g.on_failure()
    assert (out.verdict, out.code, out.stop_run) == (Verdict.UNKNOWN_POPUP, Code.E0206, False)
    assert device.taps == [] and device.saved_text and device.saved_images == ["popup_1_before"]
    event, = run.drain()
    assert event["verdict"] == "unknown" and event["popup_kind"] == "in_app" and "Rate Us" in event["popup_text"]
    assert run.unknown and run.unknown[0]["element"] == "Balance card"


def test_the_text_kept_for_an_unknown_popup_is_cut_at_200_characters():
    long_text = "word " * 100
    device = Device(android(node(resource_id="android:id/alertTitle", text=long_text)))
    g, run = guard(device, rule("Other", detect={"text_contains": ["zzz"]}))
    g.on_failure()
    assert len(run.unknown[0]["popup_text"]) == 200


@pytest.mark.parametrize("source, kind", [
    (android(node("android.widget.FrameLayout", package="com.android.permissioncontroller"), node(text="Allow Camera?")), "system_alert"),
    (android(node(resource_id="in.app:id/design_bottom_sheet", text="Offer")), "in_app"),
    (IOS_ALERT, "system_alert"),
    ("<AppiumAUT><XCUIElementTypeSheet name='Share'/></AppiumAUT>", "in_app"),
])
def test_which_dialogs_the_guard_recognises(source, kind):
    g, run = guard(Device(source), rule("Other", detect={"text_contains": ["zzz"]}))
    assert g.on_failure().verdict is Verdict.UNKNOWN_POPUP
    assert run.drain()[0]["popup_kind"] == kind


def test_a_plain_screen_is_no_popup_and_leaves_one_event_saying_so():
    device = Device(PLAIN)
    g, run = guard(device, rule())
    assert g.on_failure().verdict is Verdict.NONE and device.taps == []
    event, = run.drain()
    assert (event["verdict"], event["rule"], event["popup_kind"]) == ("none", None, None)


def test_a_page_source_that_cannot_be_read_is_no_popup_not_an_error():
    g, run = guard(Device(RATE_US, read_fails=True), rule())
    assert g.on_failure().verdict is Verdict.NONE
    assert run.drain()[0]["verdict"] == "none"


def test_a_page_source_that_is_not_xml_is_no_popup():
    g, _ = guard(Device("not xml at all"), rule())
    assert g.on_failure().verdict is Verdict.NONE


def test_before_step_reads_nothing_without_a_rule_that_opts_in():
    device = Device(SESSION)
    g, _ = guard(device, rule())
    assert g.before_step().verdict is Verdict.NONE and device.reads == 0


def test_before_step_handles_a_popup_for_a_rule_that_opts_in():
    device = Device(SESSION)
    g, run = guard(device, rule("Session", check="before_every_step", detect={"text_contains": ["about to expire"]},
                                action={"type": "tap", "target": "Continue Session"}))
    assert g.before_step().verdict is Verdict.HANDLED and device.taps == ["Continue Session"]
    assert run.drain()[0]["check"] == "before_every_step"


def test_before_step_does_not_try_rules_that_wait_for_a_failure():
    device = Device(RATE_US)
    g, _ = guard(device, rule("Session", check="before_every_step", detect={"text_contains": ["about to expire"]}), rule())
    assert g.before_step().verdict is Verdict.NONE and device.taps == []


def test_before_step_never_reports_an_unknown_popup():
    g, _ = guard(Device(RATE_US), rule("Session", check="before_every_step", detect={"text_contains": ["zzz"]}))
    assert g.before_step().verdict is Verdict.NONE


def test_a_failed_lookup_also_tries_the_rules_that_opted_in_to_the_check_before_each_step():
    device = Device(SESSION)
    g, _ = guard(device, rule("Session", check="before_every_step", detect={"text_contains": ["about to expire"]},
                              action={"type": "tap", "target": "Continue Session"}))
    assert g.on_failure().verdict is Verdict.HANDLED


def test_the_before_and_failure_checks_share_one_page_source_read_per_step():
    device = Device(PLAIN)
    g, _ = guard(device, rule("Session", check="before_every_step", detect={"text_contains": ["zzz"]}), rule())
    g.before_step()
    g.on_failure()
    assert device.reads == 1
    g.begin_step("press_element", "next")
    g.before_step()
    assert device.reads == 2


def test_a_snapshot_is_dropped_after_an_action_so_the_next_check_reads_fresh():
    device = Device(RATE_US)
    g, _ = guard(device, rule())
    g.on_failure()
    reads = device.reads
    g.on_failure()
    assert device.reads == reads + 1


def test_the_run_summary_counts_each_rule_and_lists_unknown_popups():
    device = Device(RATE_US)
    g, run = guard(device, rule(max_per_step=1))
    g.on_failure()
    device.source = RATE_US
    g.on_failure()
    device.source = android(node(resource_id="android:id/alertTitle", text="Mystery"))
    g.begin_step("enter_text", "Name")
    g2, _ = guard(device, rule("Other", detect={"text_contains": ["zzz"]}), run=run)
    g2.begin_step("enter_text", "Name")
    g2.on_failure()
    lines = run.summary_lines()
    assert "Popup 'Rate Us': handled 1, limit 1, stopped 0" in lines
    assert any("Mystery" in line and "enter_text" in line for line in lines)


def test_a_snapshot_is_dropped_even_when_the_action_failed():
    device = Device(RATE_US, dismisses=())
    g, _ = guard(device, rule())
    g.on_failure()
    reads = device.reads
    g.on_failure()
    assert device.reads == reads + 1


BOUNDED = android(node(text="Balance", package="in.app"), node(resource_id="android:id/alertTitle", text="Rate Us"),
                  node("android.widget.Button", resource_id="in.app:id/later", text="Maybe Later", bounds="[100,200][300,260]"),
                  node("android.widget.Button", text="Rate Now", bounds="[400,200][600,260]"))


@pytest.mark.parametrize("target, point", [
    ("Maybe Later", (200, 230)),
    ("maybe  LATER", (200, 230)),
    ("text=Maybe Later", (200, 230)),
    ("in.app:id/later", (200, 230)),
    ("//android.widget.Button[@text='Rate Now']", (500, 230)),
    ("xpath=//android.widget.Button[@text='Rate Now']", (500, 230)),
])
def test_a_tap_target_is_found_in_the_page_source_the_guard_already_holds_and_tapped_at_its_centre(target, point):
    device = Device(BOUNDED, dismiss_points=[point])
    g, _ = guard(device, rule(action={"type": "tap", "target": target}))
    assert g.on_failure().verdict is Verdict.HANDLED
    assert device.points == [point] and device.taps == [], "no locate ladder was needed"


def test_an_ios_node_is_tapped_at_the_centre_of_its_rectangle():
    source = ("<AppiumAUT><XCUIElementTypeAlert name='Allow notifications?'>"
              "<XCUIElementTypeButton name='Allow' label='Allow' x='10' y='20' width='100' height='40'/>"
              "</XCUIElementTypeAlert></AppiumAUT>")
    device = Device(source, dismiss_points=[(60, 40)])
    g, _ = guard(device, rule("Notifications", detect={"xpath": "//XCUIElementTypeAlert"}, action={"type": "tap", "target": "Allow"}))
    assert g.on_failure().verdict is Verdict.HANDLED and device.points == [(60, 40)]


def test_a_node_without_bounds_is_left_to_the_locate_ladder():
    device = Device(RATE_US)
    g, _ = guard(device, rule())
    g.on_failure()
    assert device.points == [] and device.taps == ["Maybe Later"]


def test_a_target_not_in_the_page_source_is_left_to_the_locate_ladder_and_the_fallback_is_still_tried():
    device = Device(BOUNDED, dismiss_points=[(200, 230)])
    g, _ = guard(device, rule(action={"type": "tap", "target": "Not on screen", "fallback": "Maybe Later"}))
    assert g.on_failure().verdict is Verdict.HANDLED
    assert device.taps == ["Not on screen"] and device.points == [(200, 230)]


def test_a_tap_target_that_is_not_a_valid_xpath_is_not_found_not_an_error():
    device = Device(BOUNDED, dismisses=())
    g, _ = guard(device, rule(action={"type": "tap", "target": "//[bad"}))
    assert g.on_failure().code == Code.E0208 and device.points == []


NOTIFICATIONS = ("Allow “Maps” to use your location?", ["Allow Once", "Allow While Using App", "Don’t Allow"])


def test_a_system_alert_ios_keeps_out_of_the_page_source_is_read_through_the_driver_and_answered():
    device = Device("<AppiumAUT><XCUIElementTypeApplication name='Maps'/></AppiumAUT>", system_alert=NOTIFICATIONS)
    g, run = guard(device, rule("Location", detect={"text_contains": ["use your location"]},
                                action={"type": "alert", "target": "Allow While Using App"}))
    assert g.on_failure().verdict is Verdict.HANDLED
    assert device.alerts == ["Allow While Using App"] and device.taps == [] and device.points == []
    event, = run.drain()
    assert (event["popup_kind"], event["platform"], event["app_id"]) == ("system_alert", "ios", "Maps")
    assert "Allow While Using App" in event["popup_text"]


def test_a_system_alert_is_found_even_when_the_page_source_cannot_be_read():
    device = Device("", system_alert=NOTIFICATIONS, read_fails=True)
    g, _ = guard(device, rule("Location", detect={"xpath": "//XCUIElementTypeAlert/XCUIElementTypeButton[@name='Allow Once']"},
                              action={"type": "alert", "target": "Allow Once"}))
    assert g.on_failure().verdict is Verdict.HANDLED and device.alerts == ["Allow Once"]


def test_an_unplanned_system_alert_is_an_unknown_popup_with_its_text_and_buttons_in_the_evidence():
    device = Device("<AppiumAUT/>", system_alert=NOTIFICATIONS)
    g, run = guard(device, rule("Other", detect={"text_contains": ["zzz"]}))
    out = g.on_failure()
    assert out.verdict is Verdict.UNKNOWN_POPUP and device.alerts == []
    assert "Don’t Allow" in device.saved_text[0][1] and run.unknown[0]["popup_text"].startswith("Allow “Maps”")


def test_a_driver_that_cannot_read_alerts_leaves_the_page_source_check_as_it_was():
    device = Device(RATE_US)
    device.read_fails_alert = True
    g, _ = guard(device, rule())
    assert g.on_failure().verdict is Verdict.HANDLED


def test_a_malformed_alert_value_is_ignored():
    device = Device(RATE_US, system_alert=object())
    g, _ = guard(device, rule())
    assert g.on_failure().verdict is Verdict.HANDLED


def test_the_driver_is_asked_for_a_system_alert_only_when_the_screen_is_not_android():
    android_phone, ios_phone = Device(RATE_US), Device("<AppiumAUT/>")
    guard(android_phone, rule())[0].on_failure()
    guard(ios_phone, rule())[0].on_failure()
    assert (android_phone.alert_reads, ios_phone.alert_reads) == (0, 1)
