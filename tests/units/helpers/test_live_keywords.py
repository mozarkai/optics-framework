"""Unit tests for ``LiveController.run_keyword`` keyword resolution.

Covers display-form -> snake_case matching ("Launch App" -> ``launch_app``),
shlex-correct argument parsing (quoted/spaced args), the actionable
unknown-keyword message (with closest-match suggestion), the friendly
signature hint for ValueError/TypeError failures, and the graceful
empty-input guard — all against a bare controller shell (no device/session),
like the other live unit tests. The TUI-side command routing (bare quit/exit
aliases and the unknown-/command hint) and the scrollable /help popup are
tested here too against a stubbed prompt_toolkit app.
"""
import asyncio
from types import SimpleNamespace

import pytest
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.utils import get_cwidth

from optics_framework.common.models import ElementData
from optics_framework.helper import live_tui
from optics_framework.helper.live import ActionStatus, LiveController

pytestmark = pytest.mark.white_box


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def _validate_stub(element, timeout="10"):
    """Stand-in for Verifier.validate_element: fails like int(timeout_str) would."""
    raise ValueError("invalid literal for int() with base 10: 'Domain'")


def _launch_stub(url, timeout="30"):
    """Stand-in whose signature can't accept three positional args."""
    raise AssertionError("should never be called")


def _controller(keyword_map):
    ctrl = LiveController.__new__(LiveController)
    ctrl.keyword_map = keyword_map
    ctrl.recorded = []
    ctrl.saved = True
    return ctrl


def test_display_form_resolves_to_snake_case():
    rec = _Recorder()
    ctrl = _controller({"launch_app": rec})

    result = ctrl.run_keyword("Launch App https://example.com")

    assert result.status is ActionStatus.PASS
    assert result.keyword == "launch_app"
    assert rec.calls == [(("https://example.com",), {})]
    assert ctrl.recorded == [("launch_app", ["https://example.com"])]
    assert ctrl.saved is False


def test_snake_case_still_resolves():
    rec = _Recorder()
    ctrl = _controller({"launch_app": rec})

    result = ctrl.run_keyword("launch_app https://example.com")

    assert result.status is ActionStatus.PASS
    assert result.keyword == "launch_app"
    assert rec.calls == [(("https://example.com",), {})]


def test_longest_match_keeps_params_intact():
    single, multi = _Recorder(), _Recorder()
    ctrl = _controller({"scroll": single, "scroll_from_element": multi})

    result = ctrl.run_keyword("Scroll From Element up")

    assert result.keyword == "scroll_from_element"
    assert multi.calls == [(("up",), {})]
    assert single.calls == []


def test_unknown_keyword_error_is_actionable_and_untruncated():
    ctrl = _controller({"launch_app": _Recorder()})

    result = ctrl.run_keyword("Frobnicate Now hard")

    assert result.status is ActionStatus.FAIL
    assert result.message == (
        "Unknown keyword: 'Frobnicate Now'. Keywords use snake_case — "
        "run `optics list` (e.g. launch_app, validate_element)."
    )
    assert ctrl.recorded == []


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_input_fails_gracefully(raw):
    ctrl = _controller({"launch_app": _Recorder()})

    result = ctrl.run_keyword(raw)

    assert result.status is ActionStatus.FAIL
    assert result.message == "Empty input"


def test_equals_locator_from_element_is_positional():
    """A ${element} resolving to 'text=...' must be dispatched as the positional
    locator, not split into a keyword argument (the runner/live '=' bug)."""
    rec = _Recorder()
    ctrl = _controller({"validate_element": rec})
    ctrl._elements_loaded = True
    ctrl.session = SimpleNamespace(
        elements=ElementData(elements={"Heading": ["text=Example Domain"]}))

    result = ctrl.run_keyword("validate_element ${Heading}")

    assert result.status is ActionStatus.PASS
    assert rec.calls == [(("text=Example Domain",), {})]


def test_quoted_argument_with_space_stays_one_param():
    """shlex parsing: a quoted multi-word argument reaches the keyword as one value."""
    rec = _Recorder()
    ctrl = _controller({"enter_text": rec})
    ctrl._elements_loaded = True
    ctrl.session = SimpleNamespace(
        elements=ElementData(elements={"user": ["id=user_field"]}))

    result = ctrl.run_keyword('enter_text ${user} "hello world"')

    assert result.status is ActionStatus.PASS
    assert rec.calls == [(("id=user_field", "hello world"), {})]


def test_unbalanced_quote_falls_back_to_whitespace_split():
    """shlex ValueError (unbalanced quote) degrades to str.split instead of a parse error."""
    rec = _Recorder()
    ctrl = _controller({"launch_app": rec})

    result = ctrl.run_keyword('launch_app https://example.com "quoted')

    assert result.status is ActionStatus.PASS
    assert rec.calls == [(("https://example.com", '"quoted'), {})]


def test_value_error_surfaces_as_friendly_signature_hint():
    """A spaced-out argument landing in an int-typed param reads as usage, not a crash."""
    ctrl = _controller({"validate_element": _validate_stub})

    result = ctrl.run_keyword("validate_element text=Example Domain")

    assert result.status is ActionStatus.FAIL
    assert result.message == (
        "Couldn't run 'validate_element': expected <element> [timeout] — "
        "check `optics list` or Tab-complete for the signature."
    )
    assert ctrl.recorded == []


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


def test_unknown_slash_command_hints_available_commands(monkeypatch):
    tui, app, messages = _tui_with_stubbed_app(monkeypatch)

    tui._submit("/frobnicate")

    assert app.exited is False
    assert len(messages) == 1
    assert "/help" in messages[0]
    assert "/quit" in messages[0]


def _popup_text(tui) -> str:
    return "".join(text for _style, text in tui._render_popup())


def _press_popup_key(tui, key) -> None:
    """Route ``key`` through the real application so the popup's bindings fire."""
    async def _run() -> None:
        processor = tui.app.key_processor
        processor.feed(KeyPress(key))
        processor.process_keys()

    asyncio.run(_run())


def test_help_popup_reveals_content_below_the_first_page(monkeypatch):
    """The tail of the reference (/device, /quit, Keys) used to be clipped away."""
    tui, _app, _messages = _tui_with_stubbed_app(monkeypatch)

    tui._submit("/help")
    first_page = _popup_text(tui)
    tui._scroll_popup_page(1)
    second_page = _popup_text(tui)

    for marker in ("/device", "/quit", "Keys", "Ctrl-C", "Press Esc to close this help."):
        assert marker not in first_page
        assert marker in second_page


def test_help_popup_shows_a_scroll_indicator(monkeypatch):
    tui, _app, _messages = _tui_with_stubbed_app(monkeypatch)

    tui._submit("/help")
    layout = tui._popup_layout()
    text = _popup_text(tui)

    assert layout.max_scroll > 0
    assert "PgUp/PgDn scroll" in text
    assert f"1-{layout.visible}/{len(layout.lines)}" in text


def test_help_popup_scroll_clamps_at_both_ends(monkeypatch):
    tui, _app, _messages = _tui_with_stubbed_app(monkeypatch)

    tui._submit("/help")
    layout = tui._popup_layout()
    for _ in range(layout.max_scroll + 10):
        tui._scroll_popup(1)
    assert tui.popup_scroll == layout.max_scroll
    assert "Press Esc to close this help." in _popup_text(tui)

    for _ in range(layout.max_scroll + 10):
        tui._scroll_popup(-1)
    assert tui.popup_scroll == 0


def test_help_popup_fits_a_short_terminal_by_scrolling(monkeypatch):
    tui, _app, _messages = _tui_with_stubbed_app(monkeypatch, rows=12)

    tui._submit("/help")
    layout = tui._popup_layout()
    assert layout.visible == tui._popup_viewport()[0] - 1  # one row for the indicator
    for _ in range(layout.max_scroll):
        tui._scroll_popup(1)

    assert "Press Esc to close this help." in _popup_text(tui)


def test_arrow_keys_scroll_the_popup_instead_of_recalling_history(monkeypatch):
    tui, _app, _messages = _tui_with_stubbed_app(monkeypatch)
    tui._submit("/help")

    _press_popup_key(tui, Keys.Down)
    assert tui.popup_scroll == 1
    _press_popup_key(tui, Keys.PageDown)
    assert tui.popup_scroll > 1
    _press_popup_key(tui, Keys.Up)
    _press_popup_key(tui, Keys.PageUp)
    assert tui.popup_scroll == 0


def test_popup_scroll_reclamps_when_the_viewport_grows(monkeypatch):
    """An offset from a smaller terminal must not freeze scrolling on resize."""
    tui, app, _messages = _tui_with_stubbed_app(monkeypatch, rows=12)
    tui._submit("/help")
    for _ in range(tui._popup_layout().max_scroll):
        tui._scroll_popup(1)

    app.output = SimpleNamespace(get_size=lambda: SimpleNamespace(rows=40, columns=100))
    _popup_text(tui)
    grown_max = tui._popup_layout().max_scroll
    assert tui.popup_scroll == grown_max

    _press_popup_key(tui, Keys.Up)
    assert tui.popup_scroll == grown_max - 1


def test_short_popup_renders_without_a_scroll_indicator(monkeypatch):
    tui, _app, _messages = _tui_with_stubbed_app(monkeypatch)

    tui.open_popup("Elements", " No named elements found in this project. ")

    assert _popup_text(tui) == " No named elements found in this project. "


def test_wide_characters_wrap_by_display_width(monkeypatch):
    """CJK locators draw two cells per character; row counts must match that."""
    tui, _app, _messages = _tui_with_stubbed_app(monkeypatch)

    tui.open_popup("Elements", "宽" * 100)

    layout = tui._popup_layout()
    assert len(layout.lines) == 3
    assert all(get_cwidth(line) <= 72 for line in layout.lines)


def test_popup_wraps_to_a_narrow_terminal(monkeypatch):
    tui, _app, _messages = _tui_with_stubbed_app(monkeypatch, columns=30)

    tui.open_popup("Elements", "x" * 100)

    layout = tui._popup_layout()
    assert len(layout.lines) == 4
    assert all(get_cwidth(line) <= 28 for line in layout.lines)


def test_status_hint_leads_with_help_and_quit():
    """On an 80-column terminal the hint truncates from the right — exit/help must survive."""
    hint = live_tui._STATUS_HINT

    assert hint.startswith("/help · /quit · ")
    assert hint.index("/help") < hint.index("Tab complete") < hint.index("Ctrl-N AI mode")
