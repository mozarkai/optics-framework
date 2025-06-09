import subprocess  # nosec B404
from typing import Dict, List
import os
import sys
from textual.app import App, ComposeResult
from textual.widgets import Checkbox, Button, Header, Footer, Static
from pydantic import BaseModel


class DriverPackage(BaseModel):
    name: str
    packages: List[str]


class DriverCategory(BaseModel):
    name: str
    drivers: Dict[str, DriverPackage]


# Driver definitions
ACTION_DRIVERS = DriverCategory(
    name="Action Driver",
    drivers={
        "Appium": DriverPackage(name="Appium", packages=["appium-python-client"]),
        "BLE": DriverPackage(name="BLE", packages=["pyserial"]),
        "Selenium": DriverPackage(name="Selenium", packages=["selenium", "beautifulsoup4"])
    }
)

TEXT_DRIVERS = DriverCategory(
    name="Text Driver",
    drivers={
        "EasyOCR": DriverPackage(name="EasyOCR", packages=["easyocr"]),
        "Pytesseract": DriverPackage(name="Pytesseract", packages=["pytesseract", "pillow"]),
        "Google Vision": DriverPackage(name="Google Vision", packages=["google-cloud-vision"])
    }
)

ALL_DRIVERS = {**ACTION_DRIVERS.drivers, **TEXT_DRIVERS.drivers}


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
        self.selected_drivers: Dict[str, List[str]] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Select Drivers to Install:", classes="title")

        yield Static("Action Drivers:")
        for name, driver in ACTION_DRIVERS.drivers.items():
            yield Checkbox(f"{name} ({', '.join(driver.packages)})", id=f"action_{name.lower().replace(' ', '_')}")

        yield Static("Text Drivers:")
        for name, driver in TEXT_DRIVERS.drivers.items():
            yield Checkbox(f"{name} ({', '.join(driver.packages)})", id=f"text_{name.lower().replace(' ', '_')}")

        yield Button("Install Selected", id="install", variant="primary")
        yield Button("Quit", id="quit", variant="error")
        yield Footer()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id is None:
            return
        category, driver_key = event.checkbox.id.split("_", 1)
        drivers_source = ACTION_DRIVERS if category == "action" else TEXT_DRIVERS
        driver_name = next(
            name for name in drivers_source.drivers.keys()
            if name.lower().replace(' ', '_') == driver_key
        )
        packages = drivers_source.drivers[driver_name].packages

        if event.checkbox.value:
            self.selected_drivers[driver_name] = packages
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

        requirements = []
        for packages in self.selected_drivers.values():
            requirements.extend(packages)
        install_packages(requirements)


def install_packages(requirements: List[str]) -> None:
    if not all(isinstance(pkg, str) and pkg.strip() and not pkg.startswith('-') for pkg in requirements):
        print("Error: Invalid package specifications detected!")
        return

    req_file = "requirements.txt"
    try:
        with open(req_file, "w") as f:
            f.write("\n".join(requirements))

        result = subprocess.run( # nosec B603
            [sys.executable, "-m","pip", "install", "-r", req_file], capture_output=True, text=True, check=True, shell=False)  # nosec B603
        if result.returncode == 0:
            print("Drivers installed successfully!")
        else:
            print("Installation failed!")

        if os.path.exists(req_file):
            os.remove(req_file)
    except Exception as e:
        print(f"Installation failed: {str(e)}")


def list_drivers() -> None:
    print("Available Drivers:")
    print("\nAction Drivers:")
    for name, _ in ACTION_DRIVERS.drivers.items():
        print(f"  {name}")
    print("\nText Drivers:")
    for name, _ in TEXT_DRIVERS.drivers.items():
        print(f"  {name}")
