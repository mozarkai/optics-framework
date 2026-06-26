import ast

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame

from optics_framework.common.config_handler import Config, ConfigHandler, DependencyConfig

_STYLE = Style.from_dict(
    {
        "header": "bold #ffffff",
        "selected": "reverse",
        "meta": "#999999",
        "sep": "#444444",
        "status": "#888888",
        "frame.border": "#5588cc",
        "error": "fg:#ff5555 bold",
        "confirm": "fg:#ffcc00 bold",
    }
)

_STATUS_HINT = "↑↓ navigate · Space edit · s save · q quit"


class ConfigTUI:
    def __init__(self) -> None:
        self.config_handler = ConfigHandler(config=Config())
        self.config_handler.load()
        self.options: list[str] = list(self.config_handler.config.model_fields.keys())
        self.selected_index: int = 0

        self.editing: bool = False
        self.confirming_quit: bool = False
        self.error_message: str = ""

        self.edit_buffer = Buffer(multiline=False)
        self.app = self._build_application()

    def _get_value(self, key: str) -> str:
        if key in self.config_handler.DEPENDENCY_KEYS:
            return str(self.config_handler.get(key))
        return str(getattr(self.config_handler.config, key))

    def _is_bool(self, key: str) -> bool:
        return isinstance(getattr(self.config_handler.config, key), bool)

    def _render_list(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        for i, key in enumerate(self.options):
            text = f"  {key}: {self._get_value(key)}\n"
            style = "class:selected" if i == self.selected_index else ""
            fragments.append((style, text))
        return fragments

    def _render_status(self) -> StyleAndTextTuples:
        return [("class:status", _STATUS_HINT)]

    def _render_confirm(self) -> StyleAndTextTuples:
        return [
            ("class:confirm", "  Quit without saving? "),
            ("", "  y"),
            ("class:meta", " yes  "),
            ("", "n"),
            ("class:meta", " no  "),
        ]

    def _render_error(self) -> StyleAndTextTuples:
        return [
            ("class:error", f"  Error: {self.error_message}  "),
            ("class:meta", "  Press Enter or Esc to dismiss  "),
        ]

    def _move(self, delta: int) -> None:
        self.selected_index = max(0, min(len(self.options) - 1, self.selected_index + delta))

    def _start_edit(self) -> None:
        key = self.options[self.selected_index]
        if self._is_bool(key):
            current = getattr(self.config_handler.config, key)
            setattr(self.config_handler.config, key, not current)
            get_app().invalidate()
            return
        self.edit_buffer.set_document(
            Document(text=self._get_value(key)),
            bypass_readonly=True,
        )
        self.editing = True
        get_app().layout.focus(self.edit_buffer)

    def _confirm_edit(self) -> None:
        key = self.options[self.selected_index]
        new_value = self.edit_buffer.text
        current_value = getattr(self.config_handler.config, key)
        try:
            if isinstance(current_value, list) and key in self.config_handler.DEPENDENCY_KEYS:
                parsed = ast.literal_eval(new_value)
                if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
                    raise ValueError("Must be a list of strings")
                setattr(
                    self.config_handler.config,
                    key,
                    [{"name": DependencyConfig(enabled=True)} for _ in parsed],
                )
            else:
                setattr(self.config_handler.config, key, type(current_value)(new_value))
        except Exception as exc:
            self.error_message = str(exc)
        self.editing = False

    def _cancel_edit(self) -> None:
        self.editing = False

    def _save(self) -> None:
        try:
            self.config_handler.save_config()
            get_app().exit()
        except Exception as exc:
            self.error_message = str(exc)

    def _build_application(self) -> Application:
        list_window = Window(
            content=FormattedTextControl(text=self._render_list, focusable=False),
        )

        header_window = Window(
            content=FormattedTextControl([("class:header", "  Optics Config\n")]),
            height=1,
        )

        status_window = Window(
            content=FormattedTextControl(self._render_status),
            height=1,
        )

        body = HSplit(
            [
                header_window,
                Window(height=1, char="─", style="class:sep"),
                list_window,
                Window(height=1, char="─", style="class:sep"),
                status_window,
            ]
        )

        edit_float = Float(
            content=ConditionalContainer(
                content=Frame(
                    body=Window(
                        content=BufferControl(buffer=self.edit_buffer),
                        height=1,
                        width=Dimension(min=40, preferred=60),
                    ),
                    title=lambda: f"Edit: {self.options[self.selected_index]}",
                ),
                filter=Condition(lambda: self.editing),
            ),
        )

        confirm_float = Float(
            content=ConditionalContainer(
                content=Frame(
                    body=Window(
                        content=FormattedTextControl(self._render_confirm, focusable=False),
                        height=1,
                        width=Dimension(min=40, preferred=50),
                    ),
                    title="Quit",
                ),
                filter=Condition(lambda: self.confirming_quit),
            ),
        )

        error_float = Float(
            content=ConditionalContainer(
                content=Frame(
                    body=Window(
                        content=FormattedTextControl(self._render_error, focusable=False),
                        height=1,
                        width=Dimension(min=50, preferred=70),
                    ),
                    title="Error",
                ),
                filter=Condition(lambda: bool(self.error_message)),
            ),
        )

        root = FloatContainer(
            content=body,
            floats=[edit_float, confirm_float, error_float],
        )

        return Application(
            layout=Layout(root),
            key_bindings=self._build_key_bindings(),
            style=_STYLE,
            full_screen=True,
            mouse_support=False,
        )

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        not_editing = Condition(lambda: not self.editing)
        not_confirming = Condition(lambda: not self.confirming_quit)
        no_error = Condition(lambda: not self.error_message)
        is_editing = Condition(lambda: self.editing)
        is_confirming = Condition(lambda: self.confirming_quit)
        has_error = Condition(lambda: bool(self.error_message))

        idle = not_editing & not_confirming & no_error

        @kb.add("up", filter=idle)
        def _up(_event):
            self._move(-1)

        @kb.add("down", filter=idle)
        def _down(_event):
            self._move(1)

        @kb.add("space", filter=idle)
        def _edit(_event):
            self._start_edit()

        @kb.add("s", filter=idle)
        def _save(_event):
            self._save()

        @kb.add("q", filter=idle)
        def _quit(_event):
            self.confirming_quit = True

        @kb.add("enter", filter=is_editing)
        def _confirm(_event):
            self._confirm_edit()

        @kb.add("escape", filter=is_editing)
        def _cancel(_event):
            self._cancel_edit()

        @kb.add("y", filter=is_confirming)
        @kb.add("enter", filter=is_confirming)
        def _confirm_quit(_event):
            get_app().exit()

        @kb.add("n", filter=is_confirming)
        @kb.add("escape", filter=is_confirming)
        def _deny_quit(_event):
            self.confirming_quit = False

        @kb.add("enter", filter=has_error)
        @kb.add("escape", filter=has_error)
        def _dismiss_error(_event):
            self.error_message = ""

        @kb.add("c-c")
        def _force_quit(_event):
            get_app().exit()

        return kb


def main() -> None:
    ConfigTUI().app.run()
