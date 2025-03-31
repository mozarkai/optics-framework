from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, ListView, ListItem, Label, Input, Button
from textual.containers import Vertical, Horizontal, Container
from textual.screen import ModalScreen
from optics_framework.common.config_handler import ConfigHandler, DependencyConfig
import ast


class QuitConfirmScreen(ModalScreen[bool]):
    """Modal screen to confirm quitting without saving."""

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Quit without saving? (y/n)", classes="modal-title"),
            Horizontal(
                Button("Yes", variant="error", id="yes"),
                Button("No", variant="primary", id="no"),
                classes="modal-buttons"
            ),
            classes="modal"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class ErrorScreen(ModalScreen[None]):
    """Modal screen to display error messages."""

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self.message, classes="error-message"),
            Button("OK", variant="primary", id="ok"),
            classes="modal"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(None)


class LoggerTUI(App):
    """A Textual-based UI for editing logger configuration."""
    CSS = """
    Screen {
        align: center middle;
        background: $background;
    }
    Header {
        background: $primary;
    }
    Footer {
        background: $secondary;
    }
    ListView {
        height: 80%;
        width: 80%;
        border: solid $accent;
        padding: 1;
    }
    ListItem {
        padding: 0 1;
    }
    ListItem.--highlight {
        background: $primary-darken-1;
    }
    .option-label {
        color: $text;
    }
    .editing {
        height: 3;
        margin: 1 0;
    }
    .modal {
        width: 40;
        height: 10;
        background: $panel;
        border: solid $accent;
        padding: 1;
    }
    .modal-title {
        color: $warning;
        text-align: center;
    }
    .modal-buttons {
        margin-top: 1;
        align: center middle;
    }
    .error-message {
        color: $error;
        text-align: center;
    }
    """

    BINDINGS = [
        ("up", "move_up", "Move up"),
        ("down", "move_down", "Move down"),
        ("space", "edit", "Edit value"),
        ("s", "save", "Save config"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.config_handler = ConfigHandler.get_instance()
        self.options = list(self.config_handler.config.model_fields.keys())
        self.selected_index = 0  # Changed to plain int

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListView(*[ListItem(Label(f"{key}: {self.get_value(key)}", classes="option-label"))
                       for key in self.options], id="config-list")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#config-list").focus()

    def get_value(self, key: str) -> str:
        """Fetch and format the current config value."""
        value = getattr(self.config_handler.config, key)
        if key in self.config_handler.DEPENDENCY_KEYS:
            return str(self.config_handler.get(key))
        return str(value)

    def action_move_up(self) -> None:
        self.selected_index = max(0, self.selected_index - 1)
        self.refresh_list()

    def action_move_down(self) -> None:
        self.selected_index = min(
            len(self.options) - 1, self.selected_index + 1)
        self.refresh_list()

    def refresh_list(self) -> None:
        list_view = self.query_one("#config-list", ListView)
        for idx, key in enumerate(self.options):
            list_view.children[idx].query_one(Label).update(
                f"{key}: {self.get_value(key)}")
        list_view.index = self.selected_index

    async def action_edit(self) -> None:
        key = self.options[self.selected_index]
        current_value = getattr(self.config_handler.config, key)

        if isinstance(current_value, bool):
            setattr(self.config_handler.config, key, not current_value)
            self.refresh_list()
        else:
            input_widget = Input(placeholder=str(
                current_value), id="edit-input")
            confirm_button = Button(
                "Confirm", variant="success", id="confirm-edit")
            self.mount(
                Container(input_widget, confirm_button, classes="editing"))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.handle_edit_confirm(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-edit":
            input_value = self.query_one("#edit-input", Input).value
            self.handle_edit_confirm(input_value)

    def handle_edit_confirm(self, new_value: str) -> None:
        key = self.options[self.selected_index]
        current_value = getattr(self.config_handler.config, key)

        try:
            if isinstance(current_value, list) and key in self.config_handler.DEPENDENCY_KEYS:
                parsed = ast.literal_eval(new_value)
                if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
                    raise ValueError("Must be a list of strings")
                setattr(self.config_handler.config, key,
                        [{"name": DependencyConfig(enabled=True)} for name in parsed])
            else:
                parsed = type(current_value)(new_value)
                setattr(self.config_handler.config, key, parsed)
            self.refresh_list()
        except Exception as e:
            self.push_screen(ErrorScreen(
                f"Invalid input: {e}"), lambda _: None)
        finally:
            self.query_one(".editing").remove()

    def action_save(self) -> None:
        try:
            self.config_handler.save_config()
            self.exit(0)
        except Exception as e:
            self.push_screen(ErrorScreen(
                f"Error saving config: {e}"), lambda _: None)

    async def action_quit(self) -> None:
        self.push_screen(QuitConfirmScreen(), self.handle_quit)

    def handle_quit(self, confirmed: bool | None) -> None:
        if confirmed is True:
            self.exit(0)


def main():
    LoggerTUI().run()
