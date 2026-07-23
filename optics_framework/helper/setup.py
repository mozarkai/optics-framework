import subprocess  # nosec B404
from importlib.metadata import PackageNotFoundError, version
from typing import Dict, List, Optional
import sys
from textual.app import App, ComposeResult
from textual.widgets import Checkbox, Button, Header, Footer, Static
from pydantic import BaseModel


DISTRIBUTION_NAME = "optics-framework"


class EngineBackend(BaseModel):
    """A selectable engine backend, installed via an `optics-framework` extra.

    Covers every backend type the framework can load from ``optics_framework/
    engines/`` — action *drivers* (Appium/Selenium/…), OCR engines, and LLM
    engines. "Driver" is reserved for the action-driver subtype; this record is
    the generic install descriptor for any of them."""
    name: str          # human-friendly display name, e.g. "Google Vision"
    extra: str         # pyproject extra name, e.g. "google-vision"
    packages: List[str]  # concrete packages the extra pulls in (shown to the user)
    aliases: List[str] = []  # extra tokens accepted on the CLI


class EngineCategory(BaseModel):
    name: str
    engines: Dict[str, EngineBackend]


# Engine-backend definitions. The `extra` matches the pyproject extra name, which
# in turn matches the config.yaml source key, so the word a user installs is the
# word they enable in config.
#
# ACTION_DRIVERS keeps the "driver" label deliberately: these implement
# DriverInterface and are configured under `driver_sources`. The OCR and LLM
# groups are engines, not drivers, and are named accordingly.
ACTION_DRIVERS = EngineCategory(
    name="Action Driver",
    engines={
        "Appium": EngineBackend(name="Appium", extra="appium", packages=["appium-python-client"]),
        "BLE": EngineBackend(name="BLE", extra="ble", packages=["pyserial"]),
        "Selenium": EngineBackend(name="Selenium", extra="selenium", packages=["selenium"]),
        "Playwright": EngineBackend(name="Playwright", extra="playwright", packages=["playwright"]),
    }
)

TEXT_ENGINES = EngineCategory(
    name="OCR Engine",
    engines={
        "EasyOCR": EngineBackend(name="EasyOCR", extra="easyocr", packages=["easyocr"]),
        "Pytesseract": EngineBackend(name="Pytesseract", extra="pytesseract", packages=["pytesseract", "pillow"]),
        "Google Vision": EngineBackend(
            name="Google Vision", extra="google-vision", packages=["google-cloud-vision"],
            aliases=["google_vision", "googlevision"],
        ),
    }
)

LLM_ENGINES = EngineCategory(
    name="LLM Engine",
    engines={
        "Gemini": EngineBackend(name="Gemini", extra="llm", packages=["google-genai"], aliases=["llm"]),
    }
)

ALL_ENGINES: Dict[str, EngineBackend] = {
    **ACTION_DRIVERS.engines, **TEXT_ENGINES.engines, **LLM_ENGINES.engines
}

# Maps a checkbox-id prefix to its engine category.
_CATEGORY_BY_PREFIX = {"action": ACTION_DRIVERS, "text": TEXT_ENGINES, "llm": LLM_ENGINES}


def _norm(token: str) -> str:
    return token.strip().lower().replace(" ", "_").replace("-", "_")


def _alias_index() -> Dict[str, EngineBackend]:
    """Build a lookup from every accepted token (display name, extra, config key,
    explicit aliases) — all normalised to lowercase/underscore — to its
    EngineBackend."""
    index: Dict[str, EngineBackend] = {}
    for engine in ALL_ENGINES.values():
        for token in [engine.name, engine.extra, *engine.aliases]:
            index[_norm(token)] = engine
    return index


def resolve_engines(tokens: List[str]) -> tuple[List[EngineBackend], List[str]]:
    """Resolve user-supplied tokens to EngineBackends. Returns (resolved, invalid).

    Accepts display names ("Appium", "Google Vision") and config/extra keys
    ("appium", "google-vision", "google_vision"), case-insensitively."""
    index = _alias_index()
    resolved: List[EngineBackend] = []
    invalid: List[str] = []
    for token in tokens:
        engine = index.get(_norm(token))
        if engine is None:
            invalid.append(token)
        elif engine not in resolved:
            resolved.append(engine)
    return resolved, invalid


class EngineInstallerApp(App):
    CSS = """
    Checkbox {
        margin: 1;
    }
    Button {
        width: 20;
        margin: 1;
    }
    Static {
        padding: 1;
    }
    """

    def __init__(self):
        super().__init__()
        self.selected_engines: Dict[str, EngineBackend] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Select engines to install:", classes="title")

        yield Static("Action Drivers:")
        for name, engine in ACTION_DRIVERS.engines.items():
            yield Checkbox(f"{name} ({', '.join(engine.packages)})", id=f"action_{name.lower().replace(' ', '_')}")

        yield Static("OCR Engines:")
        for name, engine in TEXT_ENGINES.engines.items():
            yield Checkbox(f"{name} ({', '.join(engine.packages)})", id=f"text_{name.lower().replace(' ', '_')}")

        yield Static("LLM Engines:")
        for name, engine in LLM_ENGINES.engines.items():
            yield Checkbox(f"{name} ({', '.join(engine.packages)})", id=f"llm_{name.lower().replace(' ', '_')}")

        yield Button("Install Selected", id="install", variant="primary")
        yield Button("Quit", id="quit", variant="error")
        yield Footer()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id is None:
            return
        category_prefix, engine_key = event.checkbox.id.split("_", 1)
        category = _CATEGORY_BY_PREFIX.get(category_prefix, ACTION_DRIVERS)
        engine_name = next(
            name for name in category.engines.keys()
            if name.lower().replace(' ', '_') == engine_key
        )
        engine = category.engines[engine_name]

        if event.checkbox.value:
            self.selected_engines[engine_name] = engine
        elif engine_name in self.selected_engines:
            del self.selected_engines[engine_name]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "install":
            self.install_engines()
        elif event.button.id == "quit":
            self.exit()

    def install_engines(self) -> None:
        if not self.selected_engines:
            self.notify("No engines selected!", severity="warning")
            return
        install_extras(list(self.selected_engines.values()))


def _installed_version() -> Optional[str]:
    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return None


def install_extras(engines: List[EngineBackend]) -> None:
    """Install the selected engine backends by pulling the matching
    `optics-framework` extras, pinned to the installed version so the CLI is
    never upgraded out from under the user."""
    if not engines:
        print("No engines selected.")
        return

    extras = sorted({engine.extra for engine in engines})
    installed = _installed_version()
    spec = f"{DISTRIBUTION_NAME}[{','.join(extras)}]"
    if installed:
        spec = f"{spec}=={installed}"

    try:
        subprocess.run(  # nosec B603
            [sys.executable, "-m", "pip", "install", spec],
            check=True, shell=False)

        if any(engine.extra == "playwright" for engine in engines):
            print("Installing Playwright Chromium browser and system dependencies...")
            # Per https://playwright.dev/python/docs/browsers this must run after
            # the pip install. Chromium is the most common target; --with-deps
            # pulls the required OS libraries.
            subprocess.run(  # nosec B603
                [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                check=True, shell=False)

        print("Engines installed successfully!")
    except subprocess.CalledProcessError as e:
        print(f"Installation failed: {e}")


def list_engines() -> None:
    print("Available engines (install with `optics setup --install <name>`):\n")
    for category in (ACTION_DRIVERS, TEXT_ENGINES, LLM_ENGINES):
        print(f"{category.name}s:")
        for engine in category.engines.values():
            print(f"  {engine.extra:<15} ({', '.join(engine.packages)})")
        print()
