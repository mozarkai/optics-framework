import inspect

import pytest

from optics_framework.api import ActionKeyword, AppManagement, Verifier
from optics_framework.common.agent_verbs import (
    KeywordCall,
    error_kind,
    plan_call,
    validate,
)
from optics_framework.common.error import Code
from optics_framework.common.screen_snapshot import Row, Snapshot

LABEL = Row(ref="e2", label="Email", cls="label", act=("tap",), bounds=(8, 80, 46, 98))
EMAIL = Row(ref="e3", label="Email", cls="input", act=("tap", "input"), rid="email",
            loc="css=#email", bounds=(50, 79, 203, 100))
PASSWORD = Row(ref="e5", label="Password", cls="input", act=("tap", "input"), rid="password",
               loc='css=[data-testid="password"]', bounds=(272, 79, 425, 100))
NOTE = Row(ref="e6", label="Password for all users: secret", cls="p", act=())
LIST = Row(ref="e9", label="", cls="RecyclerView", act=("scroll",))
SCREEN = Snapshot(rows=(LABEL, EMAIL, PASSWORD, NOTE), digest="3f9c01")


def _catalog() -> dict:
    catalog = {}
    for cls in (ActionKeyword, AppManagement, Verifier):
        for name, func in inspect.getmembers(cls, inspect.isfunction):
            if not name.startswith("_"):
                catalog[name] = func
    return catalog


CATALOG = _catalog()


def test_tap_presses_the_ladder():
    calls = plan_call("tap", EMAIL, "e3", ladder=["css=#email", 'css=[id="email"]'])
    assert calls == [KeywordCall("press_element", {"element": ["css=#email", 'css=[id="email"]']}, "element")]


def test_tap_with_repeat_and_a_raw_locator():
    calls = plan_call("tap", None, "xpath=//button", repeat=3)
    assert calls == [KeywordCall("press_element", {"element": "xpath=//button", "repeat": "3"}, "element")]


def test_type_with_clear_first_clears_then_enters():
    calls = plan_call("type", EMAIL, "e3", text="a@b.c", clear_first=True, ladder=["css=#email"])
    assert calls == [
        KeywordCall("clear_element_text", {"element": ["css=#email"]}, "element"),
        KeywordCall("enter_text", {"element": ["css=#email"], "text": "a@b.c"}, "element"),
    ]


def test_select_chooses_the_option():
    calls = plan_call("select", None, "Country", text="India")
    assert calls == [KeywordCall("select_dropdown_option", {"element": "Country", "option": "India"}, "element")]


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("down", KeywordCall("scroll", {"direction": "down"}, None)),
        ("up", KeywordCall("scroll", {"direction": "up"}, None)),
        ("right", KeywordCall(
            "swipe_by_percentage",
            {"percent_x": "50", "percent_y": "50", "direction": "left", "swipe_length": "50"}, None)),
        ("left", KeywordCall(
            "swipe_by_percentage",
            {"percent_x": "50", "percent_y": "50", "direction": "right", "swipe_length": "50"}, None)),
    ],
)
def test_scroll_moves_the_content_and_swipes_the_inverse_way_sideways(direction, expected):
    assert plan_call("scroll", None, "", direction=direction) == [expected]
    assert plan_call("scroll", None, "", direction=direction, driver="appium") == [expected]


@pytest.mark.parametrize("driver", ["playwright", "selenium"])
@pytest.mark.parametrize("direction", ["left", "right"])
def test_web_drivers_scroll_sideways_with_scroll(driver, direction):
    calls = plan_call("scroll", None, "", direction=direction, driver=driver)
    assert calls == [KeywordCall("scroll", {"direction": direction}, None)]


def test_scroll_until_waits_for_the_text():
    calls = plan_call("scroll", None, "", direction="down", until="Settings", timeout_s=20)
    assert calls == [KeywordCall(
        "scroll_until_element_appears",
        {"element": "Settings", "direction": "down", "timeout": "20"}, "element")]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("back", KeywordCall("go_back", {}, None)),
        ("Back", KeywordCall("go_back", {}, None)),
        ("66", KeywordCall("press_keycode", {"keycode": "66"}, None)),
    ],
)
def test_key_goes_back_or_presses_a_keycode(key, expected):
    assert plan_call("key", None, "", text=key) == [expected]


def test_open_launches_the_app_or_the_configured_one():
    assert plan_call("open", None, "", text="https://shop.test") == [
        KeywordCall("launch_app", {"app_identifier": "https://shop.test"}, None)]
    assert plan_call("open", None, "") == [KeywordCall("launch_app", {}, None)]


def test_check_records_present_items_as_one_assertion():
    assert plan_call("check", None, "", present=["Welcome", "Orders"], absent=["Sign in"]) == [
        KeywordCall("assert_presence", {"elements": "Welcome|Orders", "rule": "all"}, None)]
    assert plan_call("check", None, "", absent=["Sign in"]) == []


def test_keyword_puts_the_target_in_the_element_slot_and_drops_an_echo():
    calls = plan_call(
        "keyword", EMAIL, "e3", ladder=["css=#email"], keyword="enter_text",
        params=["e3", "a@b.c"], keywords=CATALOG,
    )
    assert calls == [KeywordCall("enter_text", {"element": ["css=#email"], "text": "a@b.c"}, "element")]


def test_keyword_with_named_params_and_no_target():
    calls = plan_call(
        "keyword", None, "", keyword="assert_presence",
        params={"elements": "Welcome", "timeout_str": "5"}, keywords=CATALOG,
    )
    assert calls == [KeywordCall("assert_presence", {"elements": "Welcome", "timeout_str": "5"}, None)]


def test_keyword_without_an_element_slot_ignores_the_target():
    calls = plan_call("keyword", None, "e3", keyword="sleep", params=["2"], keywords=CATALOG)
    assert calls == [KeywordCall("sleep", {"duration": "2"}, None)]


@pytest.mark.parametrize(
    "calls",
    [
        plan_call("tap", None, "Sign in", repeat=2),
        plan_call("type", None, "Email", text="a@b.c", clear_first=True),
        plan_call("select", None, "Country", text="India"),
        plan_call("scroll", None, "", direction="down"),
        plan_call("scroll", None, "", direction="left"),
        plan_call("scroll", None, "", direction="up", until="Top"),
        plan_call("key", None, "", text="back"),
        plan_call("key", None, "", text="4"),
        plan_call("open", None, "", text="com.example"),
        plan_call("check", None, "", present=["Welcome"]),
    ],
)
def test_every_planned_call_binds_to_the_real_keyword(calls):
    for call in calls:
        inspect.signature(CATALOG[call.keyword]).bind(None, **call.params)


def test_tap_on_text_is_allowed_but_not_on_a_scroll_only_row():
    assert validate("tap", NOTE, "e6") is None
    assert plan_call("tap", NOTE, "e6") == [KeywordCall("press_element", {"element": "e6"}, "element")]
    assert validate("tap", LIST, "e9") == "e9 cannot be tapped; it accepts scroll"


def test_typing_into_a_label_names_the_nearest_text_fields():
    hint = validate("type", LABEL, "e2", "a@b.c", snap=SCREEN)
    assert hint == 'e2 is not a text field; text fields: e3 "Email", e5 "Password"'
    assert validate("type", LABEL, "e2", "a@b.c") == "e2 is not a text field"
    assert validate("type", EMAIL, "e3", "") == "type needs the text to enter"


def test_scroll_until_works_up_and_down_only():
    assert validate("scroll", None, "", "", "left", "Settings") == "until works with up/down only"
    assert validate("scroll", None, "", "", "none") == "scroll needs a direction: up, down, left, right"


def test_other_hints():
    assert validate("fly", None) == 'unknown action "fly"; use one of tap, type, select, scroll, key, open, check, keyword'
    assert validate("tap", None, " ") == "tap needs a target"
    assert validate("select", None, "Country") == "select needs the option to choose"
    assert validate("key", None) == "key needs a key: back, or a driver key such as 66"
    assert validate("check", None) == "check needs at least one text in present or absent"
    assert validate("open", None) is None


def test_keyword_hints():
    assert validate("keyword", None, keyword="pres_element", keywords=CATALOG) == (
        'unknown keyword "pres_element"; closest: press_element, is_element, validate_element')
    assert validate("keyword", None, keyword="initialise_setup", keywords=CATALOG) == (
        'unknown keyword "initialise_setup"')
    assert validate("keyword", None, keyword="enter_text", params=["a@b.c"], keywords=CATALOG) == (
        "enter_text: missing text; params: element, text, index")
    assert validate("keyword", None, keyword="press_element", params={"event_name": "x"}, keywords=CATALOG) == (
        'press_element: no param "event_name"; params: element, repeat, offset_x, offset_y, index')
    assert validate("keyword", None, "e3", keyword="go_back", params=["now"], keywords=CATALOG) == (
        "go_back: too many params (1); it takes 0 besides the element; params: none")
    assert validate("keyword", None) == "keyword needs a keyword name"


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (Code.E0201, "not_found"),
        ("E0202", "not_found"),
        (Code.X0201, "not_found"),
        (Code.E0105, "unsupported"),
        (Code.E0106, "session_lost"),
        ("E0101", "session_lost"),
        ("E0102", "other"),
        ("E0103", "session_lost"),
        (Code.E0402, "invalid_params"),
        ("E0403", "invalid_params"),
        ("E0205", "invalid_params"),
        ("E0901", "invalid_params"),
        (Code.E0401, "silent_failure"),
        ("E0801", "other"),
        (None, "other"),
    ],
)
def test_error_kind(code, kind):
    assert error_kind(code) == kind


def test_deprecated_keywords_are_unknown_to_the_keyword_verb():
    from optics_framework.api.action_keyword import ActionKeyword

    keywords = {"press_checkbox": ActionKeyword.press_checkbox, "press_element": ActionKeyword.press_element}
    hint = validate("keyword", None, keyword="press_checkbox", params={}, keywords=keywords)
    assert hint is not None and hint.startswith('unknown keyword "press_checkbox"')
