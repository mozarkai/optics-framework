"""Behaviour tests for the natural-language agent and its live wiring.

No network and no device: a scripted fake LLM decides, and a fake app turns executed
keyword lines into screen changes. The tests check what the agent runs, sends and returns.
"""
import json
import os
import types

import pytest

from optics_framework.api import ActionKeyword, Verifier
from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.models import ElementData
from optics_framework.common.nl_agent import (
    ACTION_SCHEMA,
    CURATION_SCHEMA,
    DECISION_SCHEMA,
    SYSTEM_PROMPT,
    AgentResult,
    ExecResult,
    KeywordSpec,
    NaturalLanguageAgent,
)
from optics_framework.common.screen_snapshot import Row, element_name
from optics_framework.engines.llm_models import gemini
from optics_framework.helper.live import LiveController, NLRunStatus

pytestmark = pytest.mark.white_box

PNG = b"PNGBYTES"


class FakeLLM:
    """Returns scripted replies; an exception in the script is raised instead."""

    def __init__(self, scripted, curation_reply=None):
        self.scripted = list(scripted)
        self.curation_reply = curation_reply
        self.calls = []
        self.curation_calls = 0

    def generate(self, *args, **kwargs):  # pragma: no cover - the agent uses generate_json
        raise RuntimeError("unexpected generate() call")

    def generate_json(self, prompt, response_schema, images=None, system=None, temperature=None):
        if response_schema is CURATION_SCHEMA:
            self.curation_calls += 1
            assert images is None
            return self.curation_reply
        assert response_schema is DECISION_SCHEMA
        self.calls.append({"prompt": prompt, "images": images, "system": system})
        reply = self.scripted.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _el(text, cls="button", act=("tap",), rid="", loc="", y=0):
    extra = {"class": cls}
    if rid:
        extra["resource-id"] = rid
    el = {"text": text, "bounds": {"x1": 0, "y1": y, "x2": 100, "y2": y + 20}, "act": list(act), "extra": extra}
    if loc:
        el["loc"] = loc
    return el


LOGIN = [
    _el("Email", "input", ("tap", "input"), "email", "css=#email", 0),
    _el("Password", "input", ("tap", "input"), "password", "css=#password", 30),
    _el("Sign in", "button", ("tap",), "", "css=#signin", 60),
    _el("Password for all users: secret", "p", (), y=90),
]
HOME = [
    _el("Welcome, a@b.c", "p", (), y=0),
    _el("Orders", "a", ("tap",), "orders", "css=#orders", 30),
    _el("Profile", "a", ("tap",), "profile", "css=#profile", 60),
    _el("Sign out", "button", ("tap",), "signout", "css=#signout", 90),
]
ORDERS = [_el("Your orders", "h2", ()), _el("Back", "button", ("tap",), "back", "css=#back", 30)]


class FakeApp:
    """A screen that changes when an executed line matches a transition."""

    def __init__(self, screen, transitions=None, fail=None):
        self.screen = screen
        self.transitions = transitions or {}
        self.fail = fail or {}
        self.lines = []

    def shot(self):
        return PNG

    def elements(self, png):
        assert png == PNG
        return self.screen

    def execute(self, line):
        self.lines.append(line)
        if line in self.fail:
            return ExecResult(ok=False, message=self.fail[line])
        if line in self.transitions:
            self.screen = self.transitions[line]
        return ExecResult(ok=True, strategy="CSS", elapsed=0.1)


class FakeRegistrar:
    def __init__(self):
        self.names = {}

    def __call__(self, row, live_rungs, replay_rungs):
        name = element_name(row, set(self.names))
        self.names[name] = (live_rungs, replay_rungs)
        return "${" + name + "}"


def _catalog():
    return [
        KeywordSpec("press_element", "press_element <element> [repeat]"),
        KeywordSpec("enter_text", "enter_text <element> <text>"),
        KeywordSpec("swipe_from_element", "swipe_from_element <element> <direction> [swipe_length]"),
        KeywordSpec("press_by_percentage", "press_by_percentage <percent_x> <percent_y>"),
    ]


def _agent(llm, app, **kwargs):
    kwargs.setdefault("screen_elements_provider", app.elements)
    return NaturalLanguageAgent(llm, app.shot, app.execute, _catalog, settle_pause_s=0, **kwargs)


def _say(action, target="", text="", last="unknown", present=(), absent=(), **extra):
    reply = {"last_step": last, "thought": f"{action} {target}".strip(), "action": action,
             "target": target, "text": text, "expect_present": list(present), "expect_absent": list(absent)}
    reply.update(extra)
    return reply


def _kw(keyword, params):
    return {"last_step": "none", "thought": f"do {keyword}", "action": "keyword", "keyword": keyword,
            "params": params}


def _done(*present):
    return _say("done", present=present or ("anything",))


def _login_app():
    return FakeApp(LOGIN, {"press_element 'css=#signin'": HOME, "press_element 'css=#orders'": ORDERS})


class TestJudge:
    def test_code_pass_overrides_the_model_verdict(self):
        app = _login_app()
        llm = FakeLLM([
            _say("tap", "e3", last="none", present=["Welcome"]),
            _say("done", last="fail", present=["Welcome"]),
        ])
        result = _agent(llm, app).run("sign in")
        assert result.status == "done"
        assert result.steps[0].verdict == "pass"
        assert "=> PASS by code" in llm.calls[1]["prompt"]

    def test_a_check_met_before_the_action_proves_nothing(self):
        llm = FakeLLM([_say("tap", "e1", present=["Password for all users"]), _say("fail")])
        _agent(llm, _login_app()).run("focus email")
        assert "=> unknown: judge it from SCREEN" in llm.calls[1]["prompt"]

    def test_a_failed_settle_read_keeps_the_last_screen(self):
        app = _login_app()
        reads = []

        def elements(png):
            reads.append(png)
            return None if len(reads) == 3 else app.elements(png)

        llm = FakeLLM([_say("tap", "e1", absent=["Password for all users"]), _say("fail")])
        _agent(llm, app, screen_elements_provider=elements).run("focus email")
        prompt = llm.calls[1]["prompt"]
        assert "=> FAIL by code" in prompt
        assert 'e3 button "Sign in" [tap]' in prompt

    def test_a_typed_value_is_not_judged_for_a_typing_keyword(self):
        app = _login_app()
        llm = FakeLLM([
            _say("keyword", "e1", keyword="enter_text", params=["a@b.c"], present=["a@b.c"]), _say("fail"),
        ])
        _agent(llm, app, keywords={"enter_text": ActionKeyword.enter_text}).run("type the email")
        assert app.lines == ["enter_text 'css=#email' a@b.c"]
        assert "=> unknown: judge it from SCREEN" in llm.calls[1]["prompt"]

    def test_unknown_code_verdict_takes_the_model_verdict(self):
        app = _login_app()
        llm = FakeLLM([_say("tap", "e3", last="none"), _say("done", last="fail", present=["Welcome"])])
        result = _agent(llm, app).run("sign in")
        assert result.steps[0].verdict == "fail"
        assert "=> unknown: judge it from SCREEN" in llm.calls[1]["prompt"]


class TestDoneGate:
    def test_unmet_done_is_rejected_once_then_fails(self):
        llm = FakeLLM([_done("Your orders"), _done("Your orders")])
        result = _agent(llm, _login_app()).run("open orders")
        assert result.status == "failed"
        assert result.message == 'Goal state not seen: "Your orders" not on screen.'
        assert 'DONE rejected: "Your orders" not on screen; keep working or reply fail' in llm.calls[1]["prompt"]

    def test_done_without_expectations_is_re_asked_then_unverified(self):
        llm = FakeLLM([_say("done"), _say("done")])
        result = _agent(llm, _login_app()).run("look around")
        assert result.status == "done_unverified"
        assert "CORRECTION: add expect_present/expect_absent that describe the goal state" in llm.calls[1]["prompt"]

    def test_met_done_is_verified(self):
        llm = FakeLLM([_done("Password for all users")])
        assert _agent(llm, _login_app()).run("open login").status == "done"


class TestCorrection:
    def test_stale_ref_is_re_asked_once_with_the_valid_refs(self):
        app = _login_app()
        llm = FakeLLM([
            _say("tap", "e3", present=["Welcome"]),
            _say("tap", "e3"),
            _say("tap", "e6", present=["Your orders"]),
            _done("Your orders"),
        ])
        result = _agent(llm, app).run("open orders")
        assert result.status == "done"
        assert app.lines == ["press_element 'css=#signin'", "press_element 'css=#orders'"]
        reask = llm.calls[2]["prompt"]
        assert 'CORRECTION: e3 ("Sign in" button) is not on the current screen.' in reask
        assert 'e6 "Orders"' in reask
        assert "CORRECTION" not in llm.calls[1]["prompt"]

    def test_no_op_repeat_is_re_asked_before_running(self):
        app = _login_app()
        llm = FakeLLM([_say("tap", "e1"), _say("tap", "e1"), _done("Password for all users")])
        _agent(llm, app).run("focus email")
        assert app.lines == ["press_element 'css=#email'"]
        assert "CORRECTION: the same action already ran and did not change the screen" in llm.calls[2]["prompt"]

    def test_a_ref_on_the_raw_hierarchy_screen_is_re_asked(self):
        executed = []
        llm = FakeLLM([_say("tap", "e3"), _say("tap", "Sign in"), _say("fail")])
        agent = NaturalLanguageAgent(llm, lambda: PNG, lambda raw: executed.append(raw) or ExecResult(ok=True),
                                     _catalog, pagesource_provider=lambda: "Button text=Sign in",
                                     screen_elements_provider=lambda png: None, settle_pause_s=0)
        agent.run("sign in")
        assert "CORRECTION: SCREEN has no refs now" in llm.calls[1]["prompt"]
        assert executed == ["press_element 'Sign in'"]

    def test_two_bad_replies_fail_the_turn_without_running(self):
        app = _login_app()
        llm = FakeLLM([_say("type", "e4", text="x"), _say("type", "e4", text="x"), _say("fail")])
        result = _agent(llm, app).run("type")
        assert app.lines == []
        assert result.steps[0].observation.startswith("REJECTED: e4 is not a text field")

    def test_unknown_value_is_a_soft_hint(self):
        app = _login_app()
        llm = FakeLLM([
            _say("type", "e1", text="bob@x.io"),
            _say("type", "e1", text="bob@x.io"),
            _say("fail"),
        ])
        _agent(llm, app).run("sign in")
        assert "is not in the GOAL or on SCREEN" in llm.calls[1]["prompt"]
        assert app.lines == ["enter_text 'css=#email' bob@x.io"]


class TestRetry:
    def test_llm_error_is_followed_by_a_good_turn(self):
        llm = FakeLLM([OpticsError(Code.E0801, message="boom"), _done("Password for all users")])
        result = _agent(llm, _login_app()).run("go")
        assert result.status == "done"
        assert result.steps[0].observation == "RETRY: LLM error: boom"

    def test_three_llm_errors_in_a_row_fail_the_run(self):
        llm = FakeLLM([OpticsError(Code.E0801, message="boom")] * 3)
        result = _agent(llm, _login_app()).run("go")
        assert result.status == "failed"
        assert result.message == "Too many consecutive failures."
        assert len(llm.calls) == 3

    def test_non_object_reply_is_retried(self):
        llm = FakeLLM([[1, 2], _done("Password for all users")])
        result = _agent(llm, _login_app()).run("go")
        assert result.status == "done"
        assert result.steps[0].observation == "RETRY: the reply was not a JSON object"

    def test_hard_turn_goes_to_the_escalation_model(self):
        primary = FakeLLM([OpticsError(Code.E0801, message="boom")])
        strong = FakeLLM([_done("Password for all users")])
        result = _agent(primary, _login_app(), escalation_llm=strong).run("go")
        assert result.status == "done"
        assert (len(primary.calls), len(strong.calls)) == (1, 1)


class TestPrompts:
    def test_system_prompt_is_stable_and_holds_the_catalog(self):
        app = _login_app()
        llm = FakeLLM([_say("tap", "e3", present=["Welcome"]), _done("Welcome")])
        _agent(llm, app).run("sign in")
        first, second = (call["system"] for call in llm.calls)
        assert first == second
        assert "swipe_from_element <element> <direction> [swipe_length]" in first
        assert "press_by_percentage" not in first
        assert "swipe_from_element" not in llm.calls[0]["prompt"]

    def test_user_prompt_shows_rows_and_history(self):
        app = _login_app()
        llm = FakeLLM([_say("tap", "e3", present=["Welcome"]), _done("Welcome")])
        _agent(llm, app).run("sign in")
        prompt = llm.calls[1]["prompt"]
        assert prompt.startswith("GOAL: sign in\nSTEP 2 of 15\n")
        assert 'LAST STEP: tap e3 "Sign in" -> ok via CSS; new screen; expected "Welcome" met => PASS by code' in prompt
        assert '1. tap e3 "Sign in" -> ok · pass (code)' in prompt
        assert 'e6 a "Orders" [tap] #orders' in prompt

    def test_history_says_what_each_step_changed(self):
        def shop(cart, banner):
            rows = [_el(f"Cart ({cart})", "a", ("tap",), "", "css=#cart", 0),
                    _el("Add to cart", "button", ("tap",), "", "css=#add", 30)]
            return rows + ([_el("We use cookies", "p", (), y=60), _el("Accept all", "button", ("tap",), "", "css=#accept", 90)]
                           if banner else [])

        app = FakeApp(shop(0, True), {"press_element 'css=#add'": shop(1, True),
                                      "press_element 'css=#accept'": shop(1, False)})
        llm = FakeLLM([_say("tap", "e2", present=["Cart (1)"]), _say("tap", "e4", absent=["We use cookies"]),
                       _done("Cart (1)")])
        _agent(llm, app).run("dismiss the cookie banner, then add the lamp to the cart")
        prompt = llm.calls[2]["prompt"]
        assert '1. tap e2 "Add to cart" -> ok · pass (code) · changed "Cart (0)"→"Cart (1)"' in prompt
        assert '2. tap e4 "Accept all" -> ok · pass (code) · changed -"We use cookies", -"Accept all"' in prompt

    def test_keycodes_stay_in_the_system_prompt(self):
        for token in ("press_keycode", "HOME=3", "BACK=4", "187"):
            assert token in SYSTEM_PROMPT


class TestVision:
    def test_auto_skips_the_image_after_a_code_pass(self):
        app = _login_app()
        llm = FakeLLM([_say("tap", "e3", present=["Welcome"]), _done("Welcome")])
        _agent(llm, app).run("sign in")
        assert [call["images"] for call in llm.calls] == [[PNG], None]

    def test_auto_sends_the_image_after_a_failure(self):
        app = FakeApp(LOGIN, fail={"press_element 'css=#signin'": "[E0201] not found"})
        llm = FakeLLM([_say("tap", "e3"), _say("fail")])
        _agent(llm, app).run("sign in")
        assert [call["images"] for call in llm.calls] == [[PNG], [PNG]]

    def test_never_sends_no_image(self):
        llm = FakeLLM([_say("tap", "e3", present=["Welcome"]), _done("Welcome")])
        _agent(llm, _login_app(), vision="never").run("sign in")
        assert [call["images"] for call in llm.calls] == [None, None]


class TestActions:
    def test_ref_tap_runs_the_registered_element(self):
        app = FakeApp(LOGIN, {"press_element '${sign_in_button}'": HOME})
        registrar = FakeRegistrar()
        llm = FakeLLM([_say("tap", "e3", present=["Welcome"]), _done("Welcome")])
        result = _agent(llm, app, element_registrar=registrar).run("sign in")
        assert app.lines == ["press_element '${sign_in_button}'"]
        assert registrar.names["sign_in_button"] == (["css=#signin"], ["css=#signin", "Sign in"])
        assert result.successful_steps == [("press_element", ["${sign_in_button}"])]

    def test_type_and_horizontal_scroll(self):
        app = _login_app()
        llm = FakeLLM([
            _say("type", "e1", text="a@b.c"),
            _say("scroll", direction="right"),
            _done("Password for all users"),
        ])
        _agent(llm, app).run("type a@b.c and scroll right")
        assert app.lines == ["enter_text 'css=#email' a@b.c", "swipe_by_percentage 50 50 left 50"]

    def test_web_drivers_scroll_sideways(self):
        app = _login_app()
        llm = FakeLLM([_say("scroll", direction="right"), _say("fail")])
        _agent(llm, app, driver="playwright").run("scroll right")
        assert app.lines == ["scroll right"]

    def test_scroll_until_keeps_its_text_when_a_target_is_given(self):
        app = _login_app()
        registrar = FakeRegistrar()
        llm = FakeLLM([_say("scroll", "e3", text="Footer", direction="down"), _say("fail")])
        _agent(llm, app, element_registrar=registrar).run("scroll to the footer")
        assert app.lines == ["scroll_until_element_appears Footer down 10"]
        assert registrar.names == {}

    def test_check_no_is_not_a_failure(self):
        llm = FakeLLM([_say("check", present=["Your orders"])] * 3 + [_done("Password for all users")])
        result = _agent(llm, _login_app(), max_consecutive_failures=2).run("is it orders?")
        assert result.status == "done"
        assert result.steps[0].observation == 'NO: "Your orders" not as expected'

    def test_check_yes_is_recorded_as_an_assertion(self):
        llm = FakeLLM([_say("check", present=["Sign in"]), _done("Sign in")])
        keywords = {"assert_presence": Verifier.assert_presence}
        result = _agent(llm, _login_app(), keywords=keywords).run("check sign in")
        assert result.successful_steps == [("assert_presence", ["Sign in", "rule=all"])]

    def test_check_records_a_ref_by_its_locator_and_skips_part_of_a_label(self):
        llm = FakeLLM([_say("check", present=["e3", "Password for all"]), _done("Sign in")])
        result = _agent(llm, _login_app()).run("check sign in")
        assert result.successful_steps == [("assert_presence", ["css=#signin", "rule=all"])]
        assert '"Password for all" is not the whole text of a row' in result.steps[0].observation

    def test_lost_session_while_reading_the_screen_stops_at_once(self):
        def elements(png):
            raise OpticsError(Code.E0106, message="session ended")

        llm = FakeLLM([])
        result = _agent(llm, _login_app(), screen_elements_provider=elements).run("sign in")
        assert (result.status, result.message) == ("failed", "Driver session lost: session ended")
        assert llm.calls == []

    def test_lost_session_stops_at_once(self):
        app = FakeApp(LOGIN, fail={"press_element 'css=#signin'": "[E0106] session ended"})
        llm = FakeLLM([_say("tap", "e3"), _say("tap", "e3")])
        result = _agent(llm, app).run("sign in")
        assert result.status == "failed"
        assert result.message == "Driver session lost: [E0106] session ended"
        assert len(llm.calls) == 1

    def test_no_progress_nudges_then_stops(self):
        app = FakeApp(LOGIN)
        llm = FakeLLM([
            _say("tap", "e1", present=["Password for all users"]), _say("tap", "e2"), _say("tap", "e3"),
            _say("key", text="back"),
            _say("scroll", direction="down"),
        ])
        result = _agent(llm, app).run("leave")
        assert "NO PROGRESS: the last 3 actions did not change the screen." in llm.calls[3]["prompt"]
        assert result.status == "failed"
        assert result.message == "No progress: the screen did not change after repeated actions."

    def test_going_back_to_an_earlier_screen_is_noted_once(self):
        app = FakeApp(LOGIN, {"press_element 'css=#signin'": HOME, "press_element 'css=#signout'": LOGIN})
        llm = FakeLLM([
            _say("tap", "e3", present=["Welcome"]),
            _say("tap", "e8", present=["Sign in"]),
            _say("type", "e1", text="a@b.c"),
            _say("fail"),
        ])
        _agent(llm, app).run("sign in as a@b.c")
        revisit = "REVISIT: back at the screen before step 1: the steps since went in a circle."
        assert [revisit in call["prompt"] for call in llm.calls] == [False, False, True, False]

    def test_label_rung_for_a_row_without_a_locator(self):
        app = FakeApp([_el("Add", act=("tap",)), _el("Add", act=("tap",), y=30), _el("Accept all", y=60)])
        llm = FakeLLM([_say("tap", "e2"), _say("tap", "e3"), _say("fail")])
        _agent(llm, app).run("add the second one")
        assert app.lines == [
            """press_element 'xpath=(//button[normalize-space()="Add"])[2]'""",
            "press_element 'Accept all'",
        ]


class TestLegacyKeywordReplies:
    """Replies in the keyword form (action=keyword) still run without a keyword map."""

    def test_keyword_steps_run_and_record(self):
        executed = []
        llm = FakeLLM([
            _kw("press_element", ["Search"]),
            _kw("enter_text", ["Search", "movies for kids"]),
            _say("done"), _say("done"),
        ])
        agent = NaturalLanguageAgent(llm, lambda: PNG, lambda raw: executed.append(raw) or ExecResult(ok=True),
                                     _catalog, settle_pause_s=0)
        result = agent.run("search for movies")
        assert result.status == "done_unverified"
        assert executed == ["press_element Search", "enter_text Search 'movies for kids'"]
        assert result.successful_steps == [("press_element", ["Search"]), ("enter_text", ["Search", "movies for kids"])]

    def test_unknown_keyword_cutoff(self):
        llm = FakeLLM([_kw("nonsense", [])] * 6)
        agent = NaturalLanguageAgent(llm, lambda: PNG, lambda raw: ExecResult(ok=True), _catalog,
                                     max_consecutive_failures=3, settle_pause_s=0)
        result = agent.run("do x")
        assert result.status == "failed"
        assert len(llm.calls) == 6
        assert result.steps[0].observation == 'REJECTED: unknown keyword "nonsense"'

    def test_keyword_failure_cutoff(self):
        llm = FakeLLM([_kw("press_element", [f"A{i}"]) for i in range(5)])
        agent = NaturalLanguageAgent(llm, lambda: PNG, lambda raw: ExecResult(ok=False, message="[E0201] nf"),
                                     _catalog, max_consecutive_failures=2, settle_pause_s=0)
        assert agent.run("go").status == "failed"
        assert len(llm.calls) == 2

    def test_abort_before_the_first_call(self):
        llm = FakeLLM([])
        agent = NaturalLanguageAgent(llm, lambda: PNG, lambda raw: ExecResult(ok=True), _catalog)
        assert agent.run("go", should_abort=lambda: True).status == "aborted"
        assert llm.calls == []

    def test_max_steps_exhausted(self):
        llm = FakeLLM([_kw("press_element", [f"A{i}"]) for i in range(2)])
        agent = NaturalLanguageAgent(llm, lambda: PNG, lambda raw: ExecResult(ok=True), _catalog,
                                     max_steps=2, settle_pause_s=0)
        result = agent.run("go")
        assert result.status == "exhausted"
        assert len(result.successful_steps) == 2

    def test_fail_action_returns_failed(self):
        llm = FakeLLM([_say("fail", thought="cannot proceed")])
        agent = NaturalLanguageAgent(llm, lambda: PNG, lambda raw: ExecResult(ok=True), _catalog)
        result = agent.run("go")
        assert (result.status, result.message) == ("failed", "cannot proceed")

    def test_screenshot_failure_ends_run(self):
        def boom():
            raise RuntimeError("no screen")

        agent = NaturalLanguageAgent(FakeLLM([]), boom, lambda raw: ExecResult(ok=True), _catalog)
        result = agent.run("go")
        assert result.status == "failed"
        assert result.message == "Screenshot failed: no screen"

    @pytest.mark.parametrize("keyword", ["press_by_percentage", "detect_and_press", "press_keycode"])
    def test_blind_keywords_are_bounded(self, keyword):
        executed = []
        llm = FakeLLM([_kw(keyword, [str(i)]) for i in range(6)])
        agent = NaturalLanguageAgent(
            llm, lambda: PNG, lambda raw: executed.append(raw) or ExecResult(ok=True),
            lambda: [KeywordSpec(keyword, f"{keyword} <p>")], max_blind_repeats=3, settle_pause_s=0,
        )
        result = agent.run("do it")
        assert result.status == "failed"
        assert len(executed) == 3

    def test_page_source_is_the_fallback_screen(self):
        llm = FakeLLM([_say("done"), _say("done")])
        agent = NaturalLanguageAgent(llm, lambda: PNG, lambda raw: ExecResult(ok=True), _catalog,
                                     pagesource_provider=lambda: "EditText id=search_edit_text",
                                     screen_elements_provider=lambda png: None)
        agent.run("go")
        assert "SCREEN:\n(raw UI hierarchy; no refs)\nEditText id=search_edit_text" in llm.calls[0]["prompt"]
        assert llm.calls[0]["images"] == [PNG]


class TestSchema:
    def test_schema_is_static_and_flat(self):
        assert ACTION_SCHEMA is DECISION_SCHEMA
        assert DECISION_SCHEMA["required"] == ["last_step", "thought", "action"]
        dumped = json.dumps(DECISION_SCHEMA)
        assert "anyOf" not in dumped and "$ref" not in dumped

    def test_null_fields_parse_to_empty_values(self):
        llm = FakeLLM([{"last_step": None, "thought": None, "action": "check", "target": None,
                        "expect_present": None, "expect_absent": ["Welcome"], "params": None}, _done("Sign in")])
        result = _agent(llm, _login_app()).run("check")
        assert result.steps[0].observation == "YES: \"not Welcome\""


class TestCurateOnDone:
    def _run(self, replies, curation_reply, curate=True, fail=None):
        app = FakeApp(LOGIN, fail=fail)
        llm = FakeLLM(replies, curation_reply)
        agent = NaturalLanguageAgent(llm, app.shot, app.execute, _catalog, curate_on_done=curate,
                                     screen_elements_provider=app.elements, settle_pause_s=0)
        return llm, agent.run("go")

    def test_curation_prunes_steps(self):
        _, result = self._run(
            [_kw("press_element", ["Menu"]), _kw("press_element", ["Search"]), _kw("enter_text", ["S", "k"]),
             _done("Sign in")],
            {"keep": [2, 3], "reason": "step 1 was a dead end"},
        )
        assert result.status == "done"
        assert result.successful_steps == [("press_element", ["Search"]), ("enter_text", ["S", "k"])]
        assert result.message.endswith("(kept 2 of 3 steps)")

    @pytest.mark.parametrize("bad_reply", [{"keep": [], "reason": "r"}, {"keep": "x"}, [1, 2], "garbage"])
    def test_bad_curation_keeps_every_step(self, bad_reply):
        _, result = self._run([_kw("press_element", ["A"]), _kw("press_element", ["B"]), _done("Sign in")], bad_reply)
        assert result.successful_steps == [("press_element", ["A"]), ("press_element", ["B"])]

    def test_curation_off(self):
        llm, result = self._run(
            [_kw("press_element", ["A"]), _kw("press_element", ["B"]), _done("Sign in")],
            {"keep": [1], "reason": "r"}, curate=False,
        )
        assert llm.curation_calls == 0
        assert len(result.successful_steps) == 2

    def test_failed_steps_are_not_candidates(self):
        _, result = self._run(
            [_kw("press_element", ["A"]), _kw("press_element", ["B"]), _kw("press_element", ["C"]), _done("Sign in")],
            {"keep": [1, 2], "reason": "both"}, fail={"press_element B": "[E0201] not found"},
        )
        assert result.successful_steps == [("press_element", ["A"]), ("press_element", ["C"])]


class TestGeminiMissingDependency:
    def test_instantiation_without_extra_raises_e0601(self, monkeypatch):
        monkeypatch.setattr(gemini, "genai", None)
        monkeypatch.setattr(gemini, "_IMPORT_ERROR", ImportError("No module named 'google'"))
        with pytest.raises(OpticsError) as exc_info:
            gemini.GeminiLLM({"capabilities": {}})
        assert exc_info.value.code == Code.E0601
        assert "optics-framework[gemini]" in exc_info.value.message


def _live(elements=None, heal=False, folder=""):
    ctrl = LiveController.__new__(LiveController)
    ctrl.folder_path = folder
    ctrl._artifacts_dir = os.path.join(folder, "no_artifacts")
    ctrl.recorded = []
    ctrl.saved = True
    ctrl._elements_loaded = True
    ctrl._nl_new_elements = {}
    ctrl._agent_elements = set()
    ctrl._action_keyword = types.SimpleNamespace(ai_self_heal_enabled=heal)
    ctrl.session = types.SimpleNamespace(elements=ElementData(elements=elements or {}))
    return ctrl


class _RegisteringAgent:
    """Registers the "Sign in" row like the real agent, then returns ``status``.

    With ``dead_end`` it first registers a "Help" row that curation leaves out of the steps.
    """

    def __init__(self, ctrl, status, dead_end=False):
        self.ctrl = ctrl
        self.status = status
        self.dead_end = dead_end
        self.heal_during_run = None

    def run(self, instruction, on_step=None, should_abort=None):
        self.heal_during_run = self.ctrl._action_keyword.ai_self_heal_enabled
        if self.dead_end:
            help_row = Row(ref="e5", label="Help", cls="button", act=("tap",), loc="css=#help")
            self.ctrl._register_agent_element(help_row, ["css=#help"], ["css=#help", "Help"])
        row = Row(ref="e3", label="Sign in", cls="button", act=("tap",), loc="css=#signin")
        name = self.ctrl._register_agent_element(row, ["css=#signin"], ["css=#signin", "Sign in"])
        steps = [("press_element", [name])]
        return AgentResult(self.status, [], "msg", steps if self.status.startswith("done") else [])


class TestLiveWiring:
    def test_done_keeps_the_element_and_adds_the_label_rung(self, monkeypatch):
        ctrl = _live()
        agent = _RegisteringAgent(ctrl, "done")
        monkeypatch.setattr(ctrl, "_get_nl_agent", lambda: agent)
        summary = ctrl.run_natural_language("sign in", on_step=lambda s: None)
        assert summary.status is NLRunStatus.PASS
        assert ctrl.session.elements.elements == {"sign_in_button": ["css=#signin", "Sign in"]}
        assert ctrl.recorded == [("press_element", ["${sign_in_button}"])]

    def test_failed_run_removes_the_registered_element(self, monkeypatch):
        ctrl = _live({"email": ["css=#email"]})
        monkeypatch.setattr(ctrl, "_get_nl_agent", lambda: _RegisteringAgent(ctrl, "failed"))
        ctrl.run_natural_language("sign in", on_step=lambda s: None)
        assert ctrl.session.elements.elements == {"email": ["css=#email"]}
        assert ctrl.recorded == []

    def test_unverified_done_is_recorded_as_unverified(self, monkeypatch):
        ctrl = _live()
        monkeypatch.setattr(ctrl, "_get_nl_agent", lambda: _RegisteringAgent(ctrl, "done_unverified"))
        summary = ctrl.run_natural_language("sign in", on_step=lambda s: None)
        assert summary.status is NLRunStatus.UNVERIFIED
        assert ctrl.recorded == [("press_element", ["${sign_in_button}"])]

    def test_existing_element_with_the_same_locator_is_reused(self, monkeypatch):
        ctrl = _live({"login": ["css=#signin"]})
        monkeypatch.setattr(ctrl, "_get_nl_agent", lambda: _RegisteringAgent(ctrl, "done"))
        ctrl.run_natural_language("sign in", on_step=lambda s: None)
        assert ctrl.recorded == [("press_element", ["${login}"])]
        assert ctrl.session.elements.elements == {"login": ["css=#signin"]}

    def test_an_element_the_kept_steps_do_not_use_is_dropped(self, monkeypatch):
        ctrl = _live()
        monkeypatch.setattr(ctrl, "_get_nl_agent", lambda: _RegisteringAgent(ctrl, "done", dead_end=True))
        ctrl.run_natural_language("sign in", on_step=lambda s: None)
        assert ctrl.session.elements.elements == {"sign_in_button": ["css=#signin", "Sign in"]}

    def test_save_without_an_elements_csv_writes_the_named_elements(self, monkeypatch, tmp_path):
        ctrl = _live(folder=str(tmp_path))
        monkeypatch.setattr(ctrl, "_get_nl_agent", lambda: _RegisteringAgent(ctrl, "done"))
        ctrl.run_natural_language("sign in", on_step=lambda s: None)
        result = ctrl.save("tc", "mod")
        with open(result.elements_path, encoding="utf-8") as fh:
            assert fh.read().splitlines() == [
                "Element_Name,Element_ID", "sign_in_button,css=#signin", "sign_in_button,Sign in",
            ]

    def test_self_heal_is_off_during_the_run(self, monkeypatch):
        ctrl = _live(heal=True)
        agent = _RegisteringAgent(ctrl, "done")
        monkeypatch.setattr(ctrl, "_get_nl_agent", lambda: agent)
        ctrl.run_natural_language("sign in", on_step=lambda s: None)
        assert agent.heal_during_run is False
        assert ctrl._action_keyword.ai_self_heal_enabled is True

    def test_escalation_engine_is_the_primary_engine_with_another_model(self):
        class Engine:
            def __init__(self, config):
                self.config = config

        ctrl = _live()
        ctrl.config = Config(llm_models=[{"gemini": DependencyConfig(enabled=True, capabilities={"model": "a"})}])
        primary = types.SimpleNamespace(instances=[Engine({})])
        strong = ctrl._escalation_llm(primary, "b")
        assert isinstance(strong, Engine)
        assert strong.config["capabilities"] == {"model": "b"}
        assert ctrl._escalation_llm(primary, "") is None


class TestNlAgentConfig:
    def test_defaults(self):
        settings = Config().nl_agent
        assert (settings.vision, settings.max_steps, settings.escalation_model) == ("auto", 15, "")

    def test_reads_the_yaml_block(self):
        settings = Config(nl_agent={"vision": "never", "max_steps": 8, "escalation_model": "big"}).nl_agent
        assert (settings.vision, settings.max_steps, settings.escalation_model) == ("never", 8, "big")

    def test_rejects_an_unknown_vision_mode(self):
        with pytest.raises(ValueError):
            Config(nl_agent={"vision": "sometimes"})
