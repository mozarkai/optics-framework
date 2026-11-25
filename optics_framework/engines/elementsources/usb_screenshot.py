import json
import shutil
import subprocess  # nosec B404 - fixed adb and devicectl argument lists, never a shell
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from optics_framework.common.elementsource_interface import EncodedScreenshotSource
from optics_framework.common.error import Code, OpticsError

CAPTURE_TIMEOUT_S = 20
LIST_TIMEOUT_S = 15
CAPTURE_ONLY = "usb_screenshot only takes screenshots"


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # An Android serial or an iOS UDID; optional when one device is attached.
    serial: Optional[str] = None
    # Taken from the driver when unset.
    platform: Optional[Literal["android", "ios"]] = None


@dataclass(frozen=True)
class UsbPlatform:
    tool: str
    missing: str
    hint: str
    devices: Callable[[], list[str]]
    capture: Callable[[str], bytes]


class UsbScreenshot(EncodedScreenshotSource):
    """Reads the screen of a phone on USB with adb or devicectl; it never acts, so taps stay with the driver."""

    DEPENDENCY_TYPE = "elements_sources"
    NAME = "usb_screenshot"
    ACCEPTS_ANY_DRIVER = True

    def __init__(self, config: Optional[dict[str, Any]] = None, driver: Optional[Any] = None) -> None:
        try:
            caps = Capabilities(**(config or {}).get("capabilities", {}))
        except ValidationError as exc:
            raise OpticsError(Code.E0503, message=f"Invalid usb_screenshot capabilities: {exc}") from exc
        self.driver = driver
        name = caps.platform or (driver.device_platform() if driver is not None else None)
        self.platform = usb_platform(name)
        if shutil.which(self.platform.tool) is None:
            raise OpticsError(Code.E0501, message=self.platform.missing)
        self.serial = caps.serial

    def capture_screenshot_bytes(self) -> bytes:
        if self.serial is None:
            self.serial = only_device(self.platform)
        return self.platform.capture(self.serial)

    def locate(self, element, index=None) -> tuple:
        raise NotImplementedError(CAPTURE_ONLY)

    def assert_elements(self, elements, timeout=30, rule="any") -> None:
        raise NotImplementedError(CAPTURE_ONLY)

    def get_interactive_elements(self, filter_config=None, compact=False) -> list:
        raise NotImplementedError(CAPTURE_ONLY)


def usb_platform(name: Optional[str]) -> UsbPlatform:
    if name not in PLATFORMS:
        found = f"the driver's platform is {name}" if name else "the driver names no platform"
        raise OpticsError(Code.E0501, message=f"usb_screenshot reads Android and iOS phones, but {found}; set capabilities.platform")
    return PLATFORMS[name]


def only_device(platform: UsbPlatform) -> str:
    """The serial of the one device of ``platform`` attached to this computer."""
    attached = platform.devices()
    if len(attached) != 1:
        found = ", ".join(attached) or "none"
        raise OpticsError(Code.E0501, message=f"usb_screenshot needs exactly one attached device or capabilities.serial; attached: {found}")
    return attached[0]


def _android_devices() -> list[str]:
    lines = _run(["adb", "devices"], "adb", LIST_TIMEOUT_S, Code.E0501).stdout.decode().splitlines()[1:]
    return [line.split()[0] for line in lines if line.endswith("\tdevice")]


def _android_capture(serial: str) -> bytes:
    return _run(["adb", "-s", serial, "exec-out", "screencap", "-p"], "adb").stdout


def _ios_devices() -> list[str]:
    with tempfile.TemporaryDirectory() as folder:
        listing = Path(folder) / "devices.json"
        _run(["xcrun", "devicectl", "list", "devices", "--quiet", "--json-output", str(listing)], "devicectl", LIST_TIMEOUT_S, Code.E0501)
        devices = json.loads(listing.read_text())["result"]["devices"]
    return [
        device["hardwareProperties"]["udid"]
        for device in devices
        if device["hardwareProperties"].get("reality") == "physical"
        and device["connectionProperties"].get("tunnelState") not in (None, "unavailable")
    ]


def _ios_capture(serial: str) -> bytes:
    with tempfile.TemporaryDirectory() as folder:
        shot = Path(folder) / "screen.png"
        _run(["xcrun", "devicectl", "device", "capture", "screenshot", "--quiet",
              "--device", serial, "--destination", str(shot)], "devicectl")
        return shot.read_bytes()


def _run(command: list[str], tool: str, timeout: float = CAPTURE_TIMEOUT_S, code: Code = Code.E0303) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)  # nosec B603
    except subprocess.TimeoutExpired as exc:
        raise OpticsError(code, message=f"{tool} did not answer within {timeout:g} s") from exc
    if result.returncode != 0:
        said = [line.strip() for line in result.stderr.decode(errors="replace").splitlines() if line.strip()]
        reason = next((line for line in said if line.lower().startswith(("error", "adb: error"))), said[0] if said else f"exit {result.returncode}")
        raise OpticsError(code, message=f"{tool} failed: {reason}")
    return result


PLATFORMS = {
    "android": UsbPlatform(
        tool="adb",
        missing="usb_screenshot needs adb: install the Android platform tools and put adb on PATH",
        hint="Plug the phone in, turn on USB debugging and accept the prompt on the phone; check with adb devices.",
        devices=_android_devices,
        capture=_android_capture,
    ),
    "ios": UsbPlatform(
        tool="xcrun",
        missing="usb_screenshot needs devicectl: run on a Mac with Xcode installed",
        hint="Plug the phone in, turn on Developer Mode and trust this Mac; check with xcrun devicectl list devices.",
        devices=_ios_devices,
        capture=_ios_capture,
    ),
}
