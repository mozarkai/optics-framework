from types import SimpleNamespace

import pytest

from optics_framework.common import test_context
from optics_framework.common.agent_core import Focus
from optics_framework.common.models import ElementData
from optics_framework.helper import live_tui
from optics_framework.helper.live import ActionStatus, LiveController

pytestmark = [pytest.mark.white_box, pytest.mark.usefixtures("pt_app_session")]


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def _launch_stub(url, timeout="30"):
    """Stand-in whose signature can't accept three positional args."""
    raise AssertionError("should never be called")


def _controller(keyword_map):
    ctrl = LiveController.__new__(LiveController)
    ctrl.keyword_map = keyword_map
    ctrl.recorded = []
    ctrl.saved = True
    ctrl._focus = Focus()
    return ctrl


def test_a_live_keyword_sees_the_suite_name_of_its_locator():
    seen = []
    ctrl = _controller({"press_element": lambda element: seen.append(test_context.element_names.get({}).get(element))})
    ctrl._elements_loaded = True
    ctrl.session = SimpleNamespace(elements=ElementData(elements={"login_button": ["//login"]}))

    assert ctrl.run_keyword("press_element ${login_button}").status is ActionStatus.PASS
    assert seen == ["login_button"]


def test_an_agent_call_keeps_the_suite_name_when_other_params_follow():
    seen = []
    ctrl = _controller({"press_element": lambda element, timeout: seen.append(test_context.element_names.get({}).get(element))})
    ctrl._elements_loaded = True
    ctrl.session = SimpleNamespace(elements=ElementData(elements={"login_button": ["//login"]}))

    ctrl._agent_call("press_element", {"element": "${login_button}", "timeout": "5"})

    assert seen == ["login_button"]


def test_type_error_surfaces_as_friendly_signature_hint():
    ctrl = _controller({"launch_app": _launch_stub})

    result = ctrl.run_keyword("launch_app one two three")

    assert result.status is ActionStatus.FAIL
    assert result.message == (
        "Couldn't run 'launch_app': expected <url> [timeout] — "
        "check `optics list` or Tab-complete for the signature."
    )


def test_unknown_keyword_suggests_closest_match():
    ctrl = _controller({"launch_app": _Recorder()})

    result = ctrl.run_keyword("launche_app now")

    assert result.status is ActionStatus.FAIL
    assert result.message.endswith(" Did you mean 'launch_app'?")
    assert result.message.startswith("Unknown keyword: 'launche_app now'")


class _FakeApp:
    def __init__(self, rows=40, columns=100):
        self.exited = False
        self.output = SimpleNamespace(
            get_size=lambda: SimpleNamespace(rows=rows, columns=columns)
        )

    def exit(self):
        self.exited = True

    def invalidate(self):
        pass


def _tui_with_stubbed_app(monkeypatch, rows=40, columns=100):
    """A real LiveTUI over a bare controller; get_app/_info stubbed so no tty is needed."""
    controller = SimpleNamespace(saved=True, recorded=[])
    tui = live_tui.LiveTUI(controller)
    app = _FakeApp(rows=rows, columns=columns)
    messages: list[str] = []
    monkeypatch.setattr(live_tui, "get_app", lambda: app)
    monkeypatch.setattr(tui, "_info", lambda msg, raw="": messages.append(msg))
    return tui, app, messages


@pytest.mark.parametrize("word", ["quit", "exit", "Quit"])
def test_bare_quit_and_exit_behave_like_slash_quit(monkeypatch, word):
    tui, app, messages = _tui_with_stubbed_app(monkeypatch)

    tui._submit(word)

    assert app.exited is True
    assert messages == []


def _popup_text(tui) -> str:
    return "".join(text for _style, text in tui._render_popup())


def test_help_popup_fits_a_short_terminal_by_scrolling(monkeypatch):
    tui, _app, _messages = _tui_with_stubbed_app(monkeypatch, rows=12)

    tui._submit("/help")
    layout = tui._popup_layout()
    assert layout.visible == tui._popup_viewport()[0] - 1  # one row for the indicator
    for _ in range(layout.max_scroll):
        tui._scroll_popup(1)

    assert "Press Esc to close this help." in _popup_text(tui)


def test_short_popup_renders_without_a_scroll_indicator(monkeypatch):
    tui, _app, _messages = _tui_with_stubbed_app(monkeypatch)

    tui.open_popup("Elements", " No named elements found in this project. ")

    assert _popup_text(tui) == " No named elements found in this project. "
