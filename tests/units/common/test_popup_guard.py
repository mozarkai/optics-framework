"""PopupGuard against a fake device: no Appium, no clock, no output folder."""
from collections import Counter
from datetime import datetime, timezone
from xml.sax.saxutils import quoteattr

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.common.popup_guard import GONE_TIMEOUT, Outcome, PopupGuard, PopupIO, PopupRun, Verdict
from optics_framework.engines.drivers.appium_popup import AppiumScreen
from tests.units.popup_helpers import rule

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
STAMP = "2026-10-30T12:00:00.000+00:00"
RUN = "run1"


def node(tag="android.widget.TextView", **attrs):
    return f"<{tag} " + " ".join(f"{k.replace('_', '-')}={quoteattr(v)}" for k, v in attrs.items()) + "/>"


def android(*children):
    return "<hierarchy>" + "".join(children) + "</hierarchy>"


PLAIN = android(node(text="Balance"), node(text="Transfer"))
RATE_US = android(node(text="Balance", package="in.app"), node(resource_id="android:id/alertTitle", text="Rate Us"),
                  node("android.widget.Button", text="Maybe Later"), node("android.widget.Button", text="Rate Now"))
BOUNDED = android(node(text="Balance", package="in.app"), node(resource_id="android:id/alertTitle", text="Rate Us"),
                  node("android.widget.Button", text="Maybe Later", bounds="[100,200][300,260]"))
UPDATE = android(node(resource_id="android:id/alertTitle", text="New update"), node("android.widget.Button", text="Not now"))
COOKIES = android(node(text="Balance", package="in.app"), node(resource_id="android:id/alertTitle", text="We use cookies"),
                  node("android.widget.Button", text="Accept all"))
SESSION = android(node(text="Your session is about to expire"), node("android.widget.Button", text="Continue Session"))
MAPS = "<AppiumAUT><XCUIElementTypeApplication name='Maps'/></AppiumAUT>"
LOCATION = ("Allow “Maps” to use your location?", ["Allow Once", "Allow While Using App", "Don’t Allow"])

SESSION_RULE = rule("Session", check="before_every_step", detect={"text_contains": ["about to expire"]},
                    action={"type": "tap", "target": "Continue Session"})


class Device:
    """A screen that changes when an action hits what dismisses it, and records what was done to it."""

    def __init__(self, source, after=PLAIN, dismisses=("Maybe Later", "Continue Session", "Not now", "Accept all"),
                 dismiss_points=(), system_alert=None, fails=()):
        self.source, self.after, self.dismisses, self.dismiss_points = source, after, set(dismisses), set(dismiss_points)
        self.system_alert, self.fails = system_alert, set(fails)
        self.taps, self.points, self.backs, self.alerts, self.modules = [], [], 0, [], []
        self.saved_images, self.saved_text = [], []
        self.on_module = None

    def _maybe_fail(self, what):
        if what in self.fails:
            raise RuntimeError(f"{what} failed")

    def read_source(self):
        self._maybe_fail("read_source")
        return self.source

    def read_alert(self):
        self._maybe_fail("read_alert")
        return self.system_alert

    def tap(self, target):
        self._maybe_fail(f"tap {target}")
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
        self._maybe_fail("back")
        self.backs += 1
        self.source = self.after

    def alert(self, button):
        self.alerts.append(button)
        self.source, self.system_alert = self.after, None

    def run_module(self, name):
        self.modules.append(name)
        if self.on_module:
            self.on_module(name)

    def screenshot(self):
        return None if "screenshot" in self.fails else "frame"

    def io(self):
        return PopupIO(
            read_source=self.read_source, screen_of=AppiumScreen, tap=self.tap, tap_at=self.tap_at, back=self.back,
            alert=self.alert, run_module=self.run_module, read_alert=self.read_alert, screenshot=self.screenshot,
            save_image=lambda image, name: self.saved_images.append(name) or f"{name}.png",
            save_text=lambda text, name: self.saved_text.append((name, text)) or f"{name}.xml",
        )


class Clock:
    def __init__(self):
        self.now = 0.0
        self.on_sleep = None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


def setup(device, *rules):
    run, clock = PopupRun(id=RUN), Clock()
    guard = PopupGuard(lambda: list(rules), device.io(), lambda: run, sleep=clock.sleep, clock=clock, now=lambda: NOW)
    guard.begin_step("press_element", "Balance card")
    return guard, run, clock


def event(**fields):
    """A popup event of the step under test, every field written out."""
    return {
        "type": "popup", "keyword": "press_element", "element": "Balance card", "platform": "android", "app_id": "in.app",
        "check": "on_failure", "rule": None, "matched": [], "popup_kind": None, "popup_text": None, "popup_title": None,
        "verdict": "none", "timestamp": STAMP, "action": "", "popup_gone": None, "dismissed_in_ms": None,
        "step_count": 0, "run_count": 0, "screenshots": [], "page_source": None, "code": None, "stop_run": False,
        **fields,
    }


def rate_us(**fields):
    return event(rule="Rate Us", matched=["text_contains"], popup_kind="in_app", popup_text="Rate Us",
                 popup_title="Rate Us", **fields)


HANDLED_RATE_US = rate_us(verdict="handled", action="tap Maybe Later", popup_gone=True, dismissed_in_ms=0,
                          step_count=1, run_count=1, screenshots=[f"popup_{RUN}_1_before.png", f"popup_{RUN}_1_after.png"])


class TestActive:
    @pytest.mark.parametrize(("rules", "active"), [
        pytest.param((), False, id="no rules"),
        pytest.param((rule(),), True, id="one rule"),
    ])
    def test_active(self, rules, active):
        guard, _, _ = setup(Device(PLAIN), *rules)
        assert guard.active is active


class TestBeforeStep:
    def test_no_rule_that_checks_before_every_step(self):
        device = Device(SESSION)
        device.fails.add("read_source")
        guard, run, _ = setup(device, rule())
        assert guard.before_step() == Outcome(Verdict.NONE)
        assert run.drain() == []

    def test_a_rule_that_checks_before_every_step_on_its_popup(self):
        device = Device(SESSION)
        guard, run, _ = setup(device, SESSION_RULE)
        assert guard.before_step() == Outcome(Verdict.HANDLED, None, False, "")
        assert device.taps == ["Continue Session"]
        assert run.drain() == [event(
            check="before_every_step", app_id=None, rule="Session", matched=["text_contains"], popup_kind="in_app",
            popup_text="Your session is about to expire Continue Session", popup_title="Your session is about to expire",
            verdict="handled", action="tap Continue Session", popup_gone=True, dismissed_in_ms=0, step_count=1,
            run_count=1, screenshots=[f"popup_{RUN}_1_before.png", f"popup_{RUN}_1_after.png"],
        )]

    def test_a_rule_that_checks_before_every_step_on_a_plain_screen(self):
        device = Device(PLAIN)
        guard, run, _ = setup(device, SESSION_RULE)
        assert guard.before_step() == Outcome(Verdict.NONE)
        assert run.drain() == [event(check="before_every_step", app_id=None)]

    def test_inside_the_module_of_the_rule_that_is_dismissing_the_popup(self):
        device = Device(RATE_US)
        guard, run, _ = setup(device, rule(check="before_every_step", action={"type": "module", "target": "Dismiss Rate Us"}))
        inside = []

        def module(name):
            guard.begin_step("press_element", "Maybe Later")
            inside.extend([guard.before_step(), guard.on_failure()])
            device.source = PLAIN

        device.on_module = module
        assert guard.before_step().verdict is Verdict.HANDLED
        assert inside == [Outcome(Verdict.NONE), Outcome(Verdict.NONE)]
        assert device.modules == ["Dismiss Rate Us"]
        assert run.count("Rate Us") == 1


class TestOnFailure:
    def test_a_popup_a_rule_dismisses(self):
        device = Device(RATE_US)
        guard, run, _ = setup(device, rule())
        assert guard.on_failure() == Outcome(Verdict.HANDLED, None, False, "")
        assert device.taps == ["Maybe Later"]
        assert device.saved_images == [f"popup_{RUN}_1_before", f"popup_{RUN}_1_after"]
        assert run.drain() == [HANDLED_RATE_US]
        assert run.counts == {"Rate Us": Counter(handled=1)}

    def test_two_rules_that_match(self):
        device = Device(RATE_US)
        guard, run, _ = setup(device, rule("First"), rule("Second", action={"type": "tap", "target": "Rate Now"}))
        guard.on_failure()
        assert device.taps == ["Maybe Later"]
        assert run.counts == {"First": Counter(handled=1)}

    def test_a_plain_screen(self):
        device = Device(PLAIN)
        guard, run, _ = setup(device, rule())
        assert guard.on_failure() == Outcome(Verdict.NONE)
        assert device.taps == []
        assert run.drain() == [event(app_id=None)]

    def test_a_page_source_that_cannot_be_read(self):
        device = Device(RATE_US, fails=("read_source",))
        guard, run, _ = setup(device, rule())
        assert guard.on_failure() == Outcome(Verdict.NONE)
        assert run.drain() == [event(platform=None, app_id=None)]

    def test_a_dialog_no_rule_covers(self):
        device = Device(RATE_US)
        guard, run, _ = setup(device, rule("Other", detect={"text_contains": ["something else"]}))
        assert guard.on_failure() == Outcome(
            Verdict.UNKNOWN_POPUP, Code.E0206, False, "A popup with no rule is on screen: 'Rate Us'")
        assert device.taps == []
        assert device.saved_text == [(f"popup_{RUN}_1_source", RATE_US)]
        assert run.drain() == [event(
            popup_kind="in_app", popup_text="Rate Us", popup_title="Rate Us", verdict="unknown",
            screenshots=[f"popup_{RUN}_1_before.png"], page_source=f"popup_{RUN}_1_source.xml", code="E0206",
        )]
        assert run.unknown == [{"keyword": "press_element", "element": "Balance card", "popup_text": "Rate Us"}]

    def test_a_popup_that_stays_after_the_tap(self):
        device = Device(RATE_US, after=RATE_US)
        guard, run, clock = setup(device, rule())
        assert guard.on_failure() == Outcome(
            Verdict.STOP, Code.E0408, False, "Popup 'Rate Us' is still on screen after 'tap Maybe Later'")
        assert clock.now == GONE_TIMEOUT
        assert run.drain() == [rate_us(
            verdict="stopped", action="tap Maybe Later", popup_gone=False, step_count=1, run_count=1,
            screenshots=[f"popup_{RUN}_1_before.png", f"popup_{RUN}_1_after.png"],
            page_source=f"popup_{RUN}_1_source.xml", code="E0408",
        )]

    def test_a_popup_that_goes_while_the_guard_waits(self):
        device = Device(RATE_US, after=RATE_US)
        guard, run, clock = setup(device, rule())
        clock.on_sleep = lambda: setattr(device, "source", PLAIN)
        assert guard.on_failure() == Outcome(Verdict.HANDLED, None, False, "")
        assert run.drain() == [{**HANDLED_RATE_US, "dismissed_in_ms": 500}]

    def test_a_target_not_in_the_page_source_and_its_fallback(self):
        device = Device(BOUNDED, dismiss_points=[(200, 230)])
        guard, _, _ = setup(device, rule(action={"type": "tap", "target": "Not on screen", "fallback": "Maybe Later"}))
        assert guard.on_failure().verdict is Verdict.HANDLED
        assert device.taps == ["Not on screen"]
        assert device.points == [(200, 230)]

    def test_a_tap_that_fails(self):
        device = Device(RATE_US, fails=("tap Rate Now",))
        guard, _, _ = setup(device, rule(action={"type": "tap", "target": "Rate Now", "fallback": "Maybe Later"}))
        assert guard.on_failure().verdict is Verdict.HANDLED
        assert device.taps == ["Maybe Later"]

    def test_a_back_action(self):
        device = Device(RATE_US)
        guard, run, _ = setup(device, rule(action={"type": "back"}))
        assert guard.on_failure().verdict is Verdict.HANDLED
        assert device.backs == 1
        assert run.drain()[0]["action"] == "back"

    def test_a_back_action_that_fails(self):
        device = Device(RATE_US, fails=("back",))
        guard, _, _ = setup(device, rule(action={"type": "back"}))
        assert guard.on_failure() == Outcome(Verdict.STOP, Code.E0408, False, "Popup 'Rate Us' is still on screen after 'back'")

    def test_an_ios_system_alert_outside_the_page_source(self):
        device = Device(MAPS, system_alert=LOCATION)
        guard, run, _ = setup(device, rule("Location", detect={"text_contains": ["use your location"]},
                                           action={"type": "alert", "target": "Allow While Using App"}))
        assert guard.on_failure().verdict is Verdict.HANDLED
        assert (device.alerts, device.taps, device.points) == (["Allow While Using App"], [], [])
        assert run.drain() == [event(
            platform="ios", app_id="Maps", rule="Location", matched=["text_contains"], popup_kind="system_alert",
            popup_text="Allow “Maps” to use your location? Allow Once Allow While Using App Don’t Allow",
            popup_title="Allow “Maps” to use your location?", verdict="handled", action="alert Allow While Using App",
            popup_gone=True, dismissed_in_ms=0, step_count=1, run_count=1,
            screenshots=[f"popup_{RUN}_1_before.png", f"popup_{RUN}_1_after.png"],
        )]

    def test_a_driver_that_cannot_read_system_alerts(self):
        device = Device(MAPS, system_alert=LOCATION, fails=("read_alert",))
        guard, run, _ = setup(device, rule())
        assert guard.on_failure() == Outcome(Verdict.NONE)
        assert run.drain() == [event(platform="ios", app_id="Maps")]

    def test_no_screenshot(self):
        device = Device(RATE_US, fails=("screenshot",))
        guard, run, _ = setup(device, rule())
        guard.on_failure()
        assert device.saved_images == []
        assert run.drain() == [{**HANDLED_RATE_US, "screenshots": []}]

    @pytest.mark.parametrize(("then", "stop_run", "message"), [
        pytest.param("fail_step", False, "Popup 'Rate Us' was dismissed and then ended the step", id="fail the step"),
        pytest.param("stop_run", True, "Popup 'Rate Us' was dismissed and then ended the run", id="stop the run"),
    ])
    def test_a_rule_that_ends_after_dismissing(self, then, stop_run, message):
        device = Device(RATE_US)
        guard, run, _ = setup(device, rule(then=then))
        assert guard.on_failure() == Outcome(Verdict.STOP, Code.E0408, stop_run, message)
        assert device.taps == ["Maybe Later"]
        assert run.stop == (message if stop_run else None)
        assert run.drain() == [{**HANDLED_RATE_US, "verdict": "stopped", "code": "E0408", "stop_run": stop_run}]

    @pytest.mark.parametrize("stopper", [
        pytest.param(rule(action={"type": "none"}, then="stop_run"), id="no action"),
        pytest.param(rule(sensitive=True), id="a sensitive rule"),
    ])
    def test_a_rule_that_only_stops_the_run(self, stopper):
        device = Device(RATE_US)
        guard, run, _ = setup(device, stopper)
        message = "Popup 'Rate Us' ended the run; nothing was pressed"
        assert guard.on_failure() == Outcome(Verdict.STOP, Code.E0408, True, message)
        assert (device.taps, device.backs, device.alerts) == ([], 0, [])
        assert device.saved_text == [(f"popup_{RUN}_1_source", RATE_US)]
        assert run.stop == message
        assert run.drain() == [rate_us(
            verdict="stopped", step_count=1, run_count=1, screenshots=[f"popup_{RUN}_1_before.png"],
            page_source=f"popup_{RUN}_1_source.xml", code="E0408", stop_run=True,
        )]

    def test_a_popup_past_the_per_step_limit(self):
        device = Device(RATE_US)
        guard, run, _ = setup(device, rule(max_per_step=1))
        guard.on_failure()
        device.source = RATE_US
        assert guard.on_failure() == Outcome(
            Verdict.STOP, Code.E0407, False, "Popup 'Rate Us' came back too often (step 1/1, run 1/5)")
        assert device.taps == ["Maybe Later"]
        assert run.drain()[1] == rate_us(verdict="limit", step_count=1, run_count=1,
                                          screenshots=[f"popup_{RUN}_2_before.png"], code="E0407")

    def test_a_popup_past_the_per_run_limit_in_a_later_step(self):
        device = Device(RATE_US)
        guard, run, _ = setup(device, rule(max_per_step=5, max_per_run=1))
        guard.on_failure()
        guard.begin_step("press_element", "Transfer")
        device.source = RATE_US
        assert guard.on_failure() == Outcome(
            Verdict.STOP, Code.E0407, False, "Popup 'Rate Us' came back too often (step 0/5, run 1/1)")
        assert device.taps == ["Maybe Later"]
        assert run.counts == {"Rate Us": Counter(handled=1, limit=1)}

    def test_a_popup_again_in_a_new_step(self):
        device = Device(RATE_US)
        guard, run, _ = setup(device, rule(max_per_step=1))
        guard.on_failure()
        guard.begin_step("press_element", "Transfer")
        device.source = RATE_US
        assert guard.on_failure().verdict is Verdict.HANDLED
        assert run.count("Rate Us") == 2

    def test_a_popup_that_follows_another(self):
        device = Device(RATE_US, after=UPDATE)
        later = rule("Update", detect={"text_contains": ["New update"]}, action={"type": "tap", "target": "Not now"},
                     max_per_step=1)
        guard, run, _ = setup(device, rule(max_per_step=1), later)
        guard.on_failure()
        device.after = PLAIN
        assert guard.on_failure().verdict is Verdict.HANDLED
        assert run.counts == {"Rate Us": Counter(handled=1), "Update": Counter(handled=1)}

    def test_a_module_that_dismisses_the_popup(self):
        device = Device(RATE_US)
        device.on_module = lambda name: setattr(device, "source", PLAIN)
        guard, run, _ = setup(device, rule(action={"type": "module", "target": "Dismiss Rate Us"}))
        assert guard.on_failure() == Outcome(Verdict.HANDLED, None, False, "")
        assert (device.modules, device.taps) == (["Dismiss Rate Us"], [])
        assert run.drain() == [{**HANDLED_RATE_US, "action": "module Dismiss Rate Us"}]

    def test_a_module_that_leaves_the_popup(self):
        guard, _, _ = setup(Device(RATE_US), rule(action={"type": "module", "target": "Nothing"}))
        assert guard.on_failure() == Outcome(
            Verdict.STOP, Code.E0408, False, "Popup 'Rate Us' is still on screen after 'module Nothing'")

    def test_a_module_that_fails(self):
        device = Device(RATE_US)
        device.on_module = lambda name: (_ for _ in ()).throw(RuntimeError("module failed"))
        guard, _, _ = setup(device, rule(action={"type": "module", "target": "Broken"}))
        assert guard.on_failure() == Outcome(
            Verdict.STOP, Code.E0408, False, "Popup 'Rate Us' is still on screen after 'module Broken'")

    def test_a_module_the_session_does_not_have(self):
        device = Device(RATE_US)
        missing = OpticsError(Code.E0601, message="no module Missing")
        device.on_module = lambda name: (_ for _ in ()).throw(missing)
        guard, run, _ = setup(device, rule(action={"type": "module", "target": "Missing"}))
        assert guard.on_failure() == Outcome(
            Verdict.STOP, Code.E0601, False, "Popup 'Rate Us' runs a module this session does not have", missing)
        assert run.drain() == [rate_us(
            verdict="stopped", action="module Missing", step_count=1, run_count=1,
            screenshots=[f"popup_{RUN}_1_before.png"], page_source=f"popup_{RUN}_1_source.xml", code="E0601",
        )]

    def test_a_popup_inside_the_module(self):
        device = Device(RATE_US)
        guard, run, _ = setup(
            device, rule(max_per_step=2, action={"type": "module", "target": "Dismiss Rate Us"}),
            rule("Cookies", detect={"text_contains": ["We use cookies"]}, action={"type": "tap", "target": "Accept all"}),
        )
        inside = []

        def module(name):
            device.source = COOKIES
            guard.begin_step("tap", "Close")
            inside.append(guard.on_failure().verdict)
            device.source = PLAIN

        device.on_module = module
        assert guard.on_failure().verdict is Verdict.HANDLED
        assert inside == [Verdict.HANDLED]
        device.source = RATE_US
        guard.on_failure()
        device.source = RATE_US
        assert guard.on_failure().code is Code.E0407
        assert run.counts == {"Cookies": Counter(handled=2), "Rate Us": Counter(handled=2, limit=1)}


class TestPopupRun:
    def test_summary_lines(self):
        run = PopupRun(id=RUN, counts={"Rate Us": Counter(handled=2, limit=1)},
                       unknown=[{"keyword": "press_element", "element": "Pay", "popup_text": "Rate Us"}])
        assert run.summary_lines() == [
            "Popup 'Rate Us': handled 2, limit 1, stopped 0",
            "Unknown popup: 'Rate Us' at press_element Pay",
        ]

    def test_drain(self):
        run = PopupRun(id=RUN, events=[{"verdict": "none"}])
        assert run.drain() == [{"verdict": "none"}]
        assert run.events == []


class TestOutcome:
    def test_error(self):
        cause = RuntimeError("tap failed")
        error = Outcome(Verdict.STOP, Code.E0408, True, "Popup 'Rate Us' ended the run", cause).error()
        assert (error.code, error.message, error.details, error.__cause__) == (
            Code.E0408, "Popup 'Rate Us' ended the run", {"stop_run": True}, cause)
