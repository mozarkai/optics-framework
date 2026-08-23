import os

import yaml
from rich.prompt import Confirm
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, ListView, ListItem, Static

from optics_framework.common.config_handler import Config, ConfigHandler, DependencyConfig

_CONFIG_LIST_ID = "#config-list"


def _project_config_path(folder: str) -> str:
    """Absolute path to a project's ``config.yaml``."""
    return os.path.join(folder, "config.yaml")


def _load_project_config(folder: str) -> Config:
    """Read ``<folder>/config.yaml`` into a :class:`Config`.

    A missing file yields the built-in defaults (every source present but
    disabled); a malformed file logs and also falls back to defaults so the
    editor stays usable instead of crashing on a broken project."""
    path = _project_config_path(folder)
    if not os.path.exists(path):
        return Config()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError):
        return Config()
    return Config(**data)


class QuitConfirmScreen(ModalScreen[bool]):
    """Modal screen to confirm quitting without saving."""

    BINDINGS = [
        ("y", "confirm_yes", "Yes"),
        ("n", "confirm_no", "No"),
        ("escape", "confirm_no", "No"),
    ]

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

    def action_confirm_yes(self) -> None:
        self.dismiss(True)

    def action_confirm_no(self) -> None:
        self.dismiss(False)


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


class ProjectConfigTUI(App):
    """A Textual-based UI for editing a PROJECT's ``config.yaml``.

    Unlike the old global-file editor, this reads and writes the project's own
    ``config.yaml`` — the file the runner actually loads — so edits here take
    effect on the next ``optics execute``/``dry_run``."""

    TITLE = "optics configure"

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
        ("space", "edit", "Edit value"),
        ("s", "save", "Save config"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, folder: str):
        super().__init__()
        self.folder = folder
        self.config_path = _project_config_path(folder)
        self.config = _load_project_config(folder)
        self.options = list(self.config.model_fields.keys())
        self._editing_key: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            f"Editing PROJECT config: {self.config_path}\n"
            "This is the file `optics execute`/`dry_run` read.",
            classes="option-label",
        )
        yield Static(
            "↑/↓ move · Space edits the highlighted field · S saves · Q quits",
            classes="option-label",
        )
        yield ListView(*[ListItem(Label(f"{key}: {self.get_value(key)}", classes="option-label"))
                       for key in self.options], id="config-list")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(_CONFIG_LIST_ID).focus()

    def get_value(self, key: str) -> str:
        """Fetch and format the current config value.

        Dependency-list keys show their enabled source names (e.g.
        ``['appium']``) instead of the full nested structure — the part a
        user is actually tuning."""
        value = getattr(self.config, key)
        if key in ConfigHandler.DEPENDENCY_KEYS and isinstance(value, list):
            enabled = [
                name for item in value
                for name, details in item.items()
                if getattr(details, "enabled", False)
            ]
            return str(enabled)
        return str(value)

    def _current_index(self) -> int:
        """The row the user has highlighted — ListView owns navigation."""
        index = self.query_one(_CONFIG_LIST_ID, ListView).index
        return index if index is not None else 0

    def refresh_list(self) -> None:
        list_view = self.query_one(_CONFIG_LIST_ID, ListView)
        for idx, key in enumerate(self.options):
            list_view.children[idx].query_one(Label).update(
                f"{key}: {self.get_value(key)}")

    async def action_edit(self) -> None:
        key = self.options[self._current_index()]
        current_value = getattr(self.config, key)

        if isinstance(current_value, bool):
            setattr(self.config, key, not current_value)
            self.refresh_list()
        else:
            self._editing_key = key
            input_widget = Input(placeholder=str(
                current_value), id="edit-input")
            confirm_button = Button(
                "Confirm", variant="success", id="confirm-edit")
            self.mount(
                Container(input_widget, confirm_button, classes="editing"))
            input_widget.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.handle_edit_confirm(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-edit":
            input_value = self.query_one("#edit-input", Input).value
            self.handle_edit_confirm(input_value)

    def handle_edit_confirm(self, new_value: str) -> None:
        key = self._editing_key or self.options[self._current_index()]
        current_value = getattr(self.config, key)

        try:
            if isinstance(current_value, list) and key in ConfigHandler.DEPENDENCY_KEYS:
                import ast
                parsed = ast.literal_eval(new_value)
                if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
                    raise ValueError("Must be a list of strings")
                # Each entry must be keyed by the actual source name (e.g. "appium"),
                # not the literal "name" — otherwise every source collapses to one
                # unresolvable key.
                setattr(self.config, key,
                        [{source: DependencyConfig(enabled=True)} for source in parsed])
            else:
                parsed = type(current_value)(new_value)
                setattr(self.config, key, parsed)
            self.refresh_list()
        except Exception as e:
            self.push_screen(ErrorScreen(
                f"Invalid input: {e}"), lambda _: None)
        finally:
            self.query_one(".editing").remove()
            self._editing_key = None

    def action_save(self) -> None:
        try:
            self._save_to_disk()
            self.exit(0)
        except Exception as e:
            self.push_screen(ErrorScreen(
                f"Error saving config: {e}"), lambda _: None)

    def _save_to_disk(self) -> None:
        # NOTE: rewriting config.yaml via yaml.safe_dump loses any YAML comments
        # the user wrote. This is acceptable for the --edit power-user path; the
        # beginner path (`optics configure`) writes a freshly rendered config.
        os.makedirs(self.folder, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.config.model_dump(), f, default_flow_style=False)

    async def action_quit(self) -> None:
        self.push_screen(QuitConfirmScreen(), self.handle_quit)

    def handle_quit(self, confirmed: bool | None) -> None:
        if confirmed is True:
            self.exit(0)


def configure(folder: str | None = None, edit: bool = False) -> None:
    """Edit a project's ``config.yaml``.

    ``edit=False`` (the default, beginner path) confirms before overwriting an
    existing ``config.yaml`` — BEFORE any question is asked, so declining
    wastes no answers — then walks the user through
    :func:`prompt_project_config`, writes the rendered result and prints next
    steps.

    ``edit=True`` launches the full-field Textual editor on the project's
    ``config.yaml`` for power users.
    """
    target = folder if folder is not None else os.getcwd()

    if edit:
        ProjectConfigTUI(target).run()
        return

    from optics_framework.helper.onboarding import print_next_steps
    from optics_framework.helper.project_config import (
        prompt_project_config,
        render_project_config,
        write_project_config,
    )

    config_path = _project_config_path(target)
    if os.path.exists(config_path) and not Confirm.ask(
        f"{config_path} already exists. Overwrite it?", default=False
    ):
        print("Aborted; existing config left unchanged.")
        return

    answers = prompt_project_config()
    text = render_project_config(answers)

    write_project_config(target, text)
    print(f"Wrote {config_path}")
    print_next_steps(target, configured=True)
