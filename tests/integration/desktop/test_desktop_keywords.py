"""Each optics keyword on a desktop app through the Python library: it does what it says, or it fails with E0105."""

import base64

import pytest

from optics_framework import Optics
from optics_framework.common.error import Code, OpticsError
from tests.integration.desktop.support import (
    centre,
    driver_config,
    eventually,
    runs,
    scroll_position,
)

pytestmark = pytest.mark.black_box


def _session(host, app, tmp_path):
    optics = Optics()
    optics.setup(config={**driver_config(host, app), "execution_output_path": str(tmp_path)})
    optics.launch_app()
    return optics


@pytest.fixture
def editor(host, desktop, tmp_path):
    optics = _session(host, desktop.editor, tmp_path)
    try:
        yield optics
        if runs(host, desktop.editor_name):
            optics.close_and_terminate_app()
    finally:
        optics.quit()


@pytest.fixture
def calculator(host, desktop, tmp_path):
    optics = _session(host, desktop.calculator, tmp_path)
    try:
        optics.press_element(desktop.key_of("clear"))
        yield optics
        optics.force_terminate_app(desktop.calculator)
    finally:
        optics.quit()


def _window(optics):
    return next(w for w in optics.execute_script("xa11y: listWindows") if w.get("active"))["bounds"]


def _centre(optics, xpath):
    return centre(optics.capture_pagesource()["page_source"], xpath)


def _scroll_position(optics):
    return scroll_position(optics.capture_pagesource()["page_source"])


def test_types_reads_edits_and_clears_the_editor(editor, desktop):
    field = desktop.field
    editor.enter_text(field, "hello")
    editor.enter_text_direct(" world")
    assert editor.get_text(field) == "hello world"
    editor.assert_text(field, "hello world")

    editor.press_keycode(desktop.select_all)
    editor.enter_text_using_keyboard("<backspace>")
    assert editor.get_text(field) == ""

    editor.enter_number(field, "42")
    editor.enter_text(field, "<backspace>")
    assert editor.get_text(field) == "4"
    editor.enter_text_using_keyboard("<numpad5>")
    eventually(lambda: editor.get_text(field) == "45")
    editor.clear_element_text(field)
    assert editor.get_text(field) == ""


def test_the_checks_find_what_is_there_and_fail_on_what_is_not(editor, desktop):
    field = desktop.field
    editor.enter_text(field, "visible words")

    assert editor.assert_presence(f"{field}|visible words", "5", "all") is True
    assert editor.assert_visibility(field, "5") is True
    assert editor.validate_screen(field, "5") is True
    editor.validate_element(field, "5")
    editor.is_element(field, "enabled", "5")
    editor.is_element(field, "visible", "5")
    for check in (lambda: editor.assert_presence("words that are not there", "2"),
                  lambda: editor.validate_element('//button[@name="No such button"]', "2"),
                  lambda: editor.assert_text(field, "other words")):
        with pytest.raises(OpticsError):
            check()


def test_the_reads_describe_the_app_window(editor, desktop):
    editor.enter_text(desktop.field, "read me")

    assert "read me" in editor.capture_pagesource()["page_source"]
    assert base64.b64decode(editor.capture_screenshot()).startswith(b"\x89PNG")
    screen = editor.get_screen_elements()
    assert base64.b64decode(screen["screenshot"]).startswith(b"\x89PNG")
    assert any(e["text"] == "read me" for e in screen["elements"]), screen["elements"]
    assert any("input" in e["act"] for e in editor.get_interactive_elements(compact=True))
    assert _window(editor)["width"] > 0
    assert editor.get_driver_session_id()


def test_scrolls_and_swipes_move_a_long_file_both_ways(editor, desktop):
    editor.enter_text(desktop.field, "\n".join(f"line {i}" for i in range(150)))
    eventually(lambda: editor.get_text(desktop.field).rstrip().endswith("line 149"))
    editor.press_keycode(desktop.to_top)
    window = _window(editor)
    centre = (window["x"] + window["width"] // 2, window["y"] + window["height"] // 2)
    moves = [
        (lambda: editor.scroll("down"), 1), (lambda: editor.scroll("up"), -1),
        (lambda: editor.swipe(str(centre[0]), str(centre[1]), "up", "300"), 1),
        (lambda: editor.swipe_from_element(desktop.field, "down", "300"), -1),
        (lambda: editor.swipe_by_percentage("50", "50", "up", "40"), 1),
        (lambda: editor.scroll_from_element(desktop.field, "down", "300"), -1),
    ]
    for move, sign in moves:
        before = _scroll_position(editor)
        move()
        assert (_scroll_position(editor) - before) * sign > 0, (move, before)


def test_the_until_keywords_stop_at_a_shown_element_and_fail_on_a_missing_one(editor, desktop):
    editor.scroll_until_element_appears(desktop.field, "down", "5")
    editor.swipe_until_element_appears(desktop.field, "up", "5")
    for until in (editor.scroll_until_element_appears, editor.swipe_until_element_appears):
        with pytest.raises(OpticsError):
            until('//button[@name="No such button"]', "down", "3")


def test_presses_keys_by_element_point_percentage_and_detection(calculator, desktop):
    calculator.press_element(desktop.key_of("7"))
    window = _window(calculator)
    x, y = _centre(calculator, desktop.key_of("8"))
    calculator.press_by_percentage(str(100 * (x - window["x"]) // window["width"]),
                                   str(100 * (y - window["y"]) // window["height"]))
    calculator.press_by_coordinates(*map(str, _centre(calculator, desktop.key_of("+"))))
    calculator.detect_and_press(desktop.key_of("5"), "5")
    calculator.press_element(desktop.key_of("="))

    assert calculator.assert_presence("83", "10") is True


def test_opens_switches_and_stops_apps(editor, desktop, host):
    editor.launch_other_app(desktop.calculator)
    editor.press_element(desktop.key_of("clear"))
    assert runs(host, desktop.calculator_name)

    editor.launch_app()
    editor.enter_text(desktop.field, "back")
    assert editor.get_text(desktop.field) == "back"

    editor.force_terminate_app(desktop.calculator)
    assert not runs(host, desktop.calculator_name)
    editor.close_and_terminate_app()
    assert not runs(host, desktop.editor_name)


def test_a_stop_in_a_session_that_timed_out_fails_with_e0401(host, desktop, tmp_path):
    config = driver_config(host, desktop.editor)
    config["driver_sources"][0]["appium"]["capabilities"]["newCommandTimeout"] = 2
    optics = Optics()
    optics.setup(config={**config, "execution_output_path": str(tmp_path)})
    optics.launch_app()
    eventually(lambda: not runs(host, desktop.editor_name))
    with pytest.raises(OpticsError) as raised:
        optics.force_terminate_app(desktop.calculator)
    assert raised.value.code == Code.E0401, raised.value


def test_what_a_desktop_does_not_have_fails_with_e0105(editor, desktop):
    for call in (editor.go_back, editor.get_app_version, lambda: editor.enter_text(desktop.field, "<volumeup>"),
                 lambda: editor.enter_text_using_keyboard("<volumeup>"), lambda: editor.enter_text_using_keyboard("<pause>")):
        with pytest.raises(OpticsError) as raised:
            call()
        assert raised.value.code == Code.E0105, raised.value
