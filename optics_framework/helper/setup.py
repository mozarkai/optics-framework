import subprocess  # nosec B404
from importlib.metadata import PackageNotFoundError, version
from typing import Dict, List, Optional
import sys
from textual.app import App, ComposeResult
from textual.widgets import Checkbox, Button, Header, Footer, Static
from pydantic import BaseModel


DISTRIBUTION_NAME = "optics-framework"


class Driver(BaseModel):
    """A selectable engine backend, installed via an `optics-framework` extra."""
    name: str          # human-friendly display name, e.g. "Google Vision"
    extra: str         # pyproject extra name, e.g. "google-vision"
    packages: List[str]  # concrete packages the extra pulls in (shown to the user)
    aliases: List[str] = []  # extra tokens accepted on the CLI


class DriverCategory(BaseModel):
    name: str
    drivers: Dict[str, Driver]


# Driver definitions. The `extra` matches the pyproject extra name, which in turn
# matches the config.yaml source key, so the word a user installs is the word they
# enable in config.
ACTION_DRIVERS = DriverCategory(
    name="Action Driver",
    drivers={
        "Appium": Driver(name="Appium", extra="appium", packages=["appium-python-client"]),
        "BLE": Driver(name="BLE", extra="ble", packages=["pyserial"]),
        "Selenium": Driver(name="Selenium", extra="selenium", packages=["selenium"]),
        "Playwright": Driver(name="Playwright", extra="playwright", packages=["playwright"]),
    }
)

TEXT_DRIVERS = DriverCategory(
    name="Text Driver",
    drivers={
        "EasyOCR": Driver(name="EasyOCR", extra="easyocr", packages=["easyocr"]),
        "Pytesseract": Driver(name="Pytesseract", extra="pytesseract", packages=["pytesseract", "pillow"]),
        "Google Vision": Driver(
            name="Google Vision", extra="google-vision", packages=["google-cloud-vision"],
            aliases=["google_vision", "googlevision"],
        ),
    }
)

LLM_DRIVERS = DriverCategory(
    name="LLM Driver",
    drivers={
        "Gemini": Driver(name="Gemini", extra="llm", packages=["google-genai"], aliases=["llm"]),
    }
)

ALL_DRIVERS: Dict[str, Driver] = {
    **ACTION_DRIVERS.drivers, **TEXT_DRIVERS.drivers, **LLM_DRIVERS.drivers
}

# Maps a checkbox-id prefix to its driver category.
_CATEGORY_BY_PREFIX = {"action": ACTION_DRIVERS, "text": TEXT_DRIVERS, "llm": LLM_DRIVERS}


def _norm(token: str) -> str:
    return token.strip().lower().replace(" ", "_").replace("-", "_")


def _alias_index() -> Dict[str, Driver]:
    """Build a lookup from every accepted token (display name, extra, config key,
    explicit aliases) — all normalised to lowercase/underscore — to its Driver."""
    index: Dict[str, Driver] = {}
    for driver in ALL_DRIVERS.values():
        for token in [driver.name, driver.extra, *driver.aliases]:
            index[_norm(token)] = driver
    return index


def resolve_drivers(tokens: List[str]) -> tuple[List[Driver], List[str]]:
    """Resolve user-supplied tokens to Drivers. Returns (resolved, invalid).

    Accepts display names ("Appium", "Google Vision") and config/extra keys
    ("appium", "google-vision", "google_vision"), case-insensitively."""
    index = _alias_index()
    resolved: List[Driver] = []
    invalid: List[str] = []
    for token in tokens:
        driver = index.get(_norm(token))
        if driver is None:
            invalid.append(token)
        elif driver not in resolved:
            resolved.append(driver)
    return resolved, invalid


class DriverInstallerApp(App):
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
        self.selected_drivers: Dict[str, Driver] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Select Drivers to Install:", classes="title")

        yield Static("Action Drivers:")
        for name, driver in ACTION_DRIVERS.drivers.items():
            yield Checkbox(f"{name} ({', '.join(driver.packages)})", id=f"action_{name.lower().replace(' ', '_')}")

        yield Static("Text Drivers:")
        for name, driver in TEXT_DRIVERS.drivers.items():
            yield Checkbox(f"{name} ({', '.join(driver.packages)})", id=f"text_{name.lower().replace(' ', '_')}")

        yield Static("LLM Drivers:")
        for name, driver in LLM_DRIVERS.drivers.items():
            yield Checkbox(f"{name} ({', '.join(driver.packages)})", id=f"llm_{name.lower().replace(' ', '_')}")

        yield Button("Install Selected", id="install", variant="primary")
        yield Button("Quit", id="quit", variant="error")
        yield Footer()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id is None:
            return
        category, driver_key = event.checkbox.id.split("_", 1)
        drivers_source = _CATEGORY_BY_PREFIX.get(category, ACTION_DRIVERS)
        driver_name = next(
            name for name in drivers_source.drivers.keys()
            if name.lower().replace(' ', '_') == driver_key
        )
        driver = drivers_source.drivers[driver_name]

        if event.checkbox.value:
            self.selected_drivers[driver_name] = driver
        elif driver_name in self.selected_drivers:
            del self.selected_drivers[driver_name]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "install":
            self.install_drivers()
        elif event.button.id == "quit":
            self.exit()

    def install_drivers(self) -> None:
        if not self.selected_drivers:
            self.notify("No drivers selected!", severity="warning")
            return
        install_extras(list(self.selected_drivers.values()))


def _installed_version() -> Optional[str]:
    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return None


def install_extras(drivers: List[Driver]) -> None:
    """Install the selected engine backends by pulling the matching
    `optics-framework` extras, pinned to the installed version so the CLI is
    never upgraded out from under the user."""
    if not drivers:
        print("No drivers selected.")
        return

    extras = sorted({driver.extra for driver in drivers})
    installed = _installed_version()
    spec = f"{DISTRIBUTION_NAME}[{','.join(extras)}]"
    if installed:
        spec = f"{spec}=={installed}"

    try:
        subprocess.run(  # nosec B603
            [sys.executable, "-m", "pip", "install", spec],
            check=True, shell=False)

        if any(driver.extra == "playwright" for driver in drivers):
            print("Installing Playwright Chromium browser and system dependencies...")
            # Per https://playwright.dev/python/docs/browsers this must run after
            # the pip install. Chromium is the most common target; --with-deps
            # pulls the required OS libraries.
            subprocess.run(  # nosec B603
                [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                check=True, shell=False)

        print("Drivers installed successfully!")
    except subprocess.CalledProcessError as e:
        print(f"Installation failed: {e}")


def list_drivers() -> None:
    print("Available drivers (install with `optics setup --install <name>`):\n")
    for category in (ACTION_DRIVERS, TEXT_DRIVERS, LLM_DRIVERS):
        print(f"{category.name}s:")
        for driver in category.drivers.values():
            print(f"  {driver.extra:<15} ({', '.join(driver.packages)})")
        print()
