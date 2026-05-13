import os
import subprocess  # nosec
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from optics_framework.common.config_handler import Config
from optics_framework.common.logging_config import internal_logger


@dataclass(frozen=True)
class AndroidDeviceInfo:
    """Connected Android device details used for generated Appium config."""

    serial: str
    name: str


def _run_adb(args: list[str]) -> str:
    """Run adb and return text output."""
    return subprocess.check_output(["adb", *args], text=True, stderr=subprocess.STDOUT).strip()  # nosec


def _parse_adb_devices(output: str) -> list[str]:
    """Return connected device serials from `adb devices` output."""
    serials: list[str] = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def _getprop(serial: str, prop_name: str) -> Optional[str]:
    """Read one Android system property for a device serial."""
    try:
        value = _run_adb(["-s", serial, "shell", "getprop", prop_name])
        return value.strip() or None
    except Exception:
        return None


def _first_device_prop(serial: str, prop_names: list[str]) -> Optional[str]:
    for prop_name in prop_names:
        value = _getprop(serial, prop_name)
        if value:
            return value
    return None


def _normalized_part(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    return value or None


def _friendly_device_name(serial: str) -> str:
    """
    Return a user-facing Android device name.

    `ro.product.model` is often a model code such as 22111317I. Marketing
    names, when exposed by the OEM, are usually in marketname props.
    """
    market_name = _first_device_prop(
        serial,
        [
            "ro.product.marketname",
            "ro.product.vendor.marketname",
            "ro.product.odm.marketname",
            "ro.product.system.marketname",
            "ro.vendor.product.marketname",
        ],
    )
    if market_name:
        return market_name

    brand = _normalized_part(_getprop(serial, "ro.product.brand"))
    manufacturer = _normalized_part(_getprop(serial, "ro.product.manufacturer"))
    model = _normalized_part(_getprop(serial, "ro.product.model"))

    prefix = brand or manufacturer
    if prefix and model and prefix.lower() not in model.lower():
        return f"{prefix} {model}"
    return model or prefix or serial


def get_connected_android_device() -> Optional[AndroidDeviceInfo]:
    """Get the first physical Android device, falling back to any connected device."""
    try:
        serials = _parse_adb_devices(_run_adb(["devices"]))
    except Exception as exc:
        internal_logger.debug("Unable to inspect connected Android devices: %s", exc)
        return None

    if not serials:
        return None

    serial = next((item for item in serials if not item.startswith("emulator-")), serials[0])
    return AndroidDeviceInfo(serial=serial, name=_friendly_device_name(serial))


def _capability_value(capabilities: dict, key: str) -> Optional[str]:
    value = capabilities.get(key) or capabilities.get(f"appium:{key}")
    return str(value) if value not in (None, "") else None


def _is_android_appium_enabled(config: Config) -> bool:
    for item in config.driver_sources or []:
        appium = item.get("appium")
        if appium is None or not appium.enabled:
            continue
        platform = _capability_value(appium.capabilities, "platformName")
        return platform is None or platform.lower() == "android"
    return False


def apply_appium_device_info(config: Config, device: Optional[AndroidDeviceInfo]) -> bool:
    """Apply connected Android device metadata to enabled Appium capabilities."""
    if device is None:
        return False

    changed = False
    for item in config.driver_sources or []:
        appium = item.get("appium")
        if appium is None or not appium.enabled:
            continue
        capabilities = appium.capabilities
        before = dict(capabilities)
        capabilities["deviceName"] = device.name
        capabilities["udid"] = device.serial
        if "appium:deviceName" in capabilities:
            capabilities["appium:deviceName"] = device.name
        if "appium:udid" in capabilities:
            capabilities["appium:udid"] = device.serial
        changed = changed or capabilities != before
    return changed


def _project_config_path(config: Config) -> Optional[Path]:
    project_path = getattr(config, "project_path", None)
    if not project_path:
        return None
    config_path = Path(os.path.abspath(project_path)) / "config.yaml"
    return config_path if config_path.exists() else None


def persist_config(config: Config) -> None:
    """Write the current Config back to project config.yaml when available."""
    config_path = _project_config_path(config)
    if config_path is None:
        return
    with config_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config.model_dump(exclude_none=True), fh, sort_keys=False)
    internal_logger.info("Updated Appium device capabilities in %s", config_path)


def refresh_appium_device_config(config: Config, persist: bool = True) -> Optional[AndroidDeviceInfo]:
    """Refresh Appium Android deviceName/udid from adb and optionally persist config.yaml."""
    if not _is_android_appium_enabled(config):
        return None

    device = get_connected_android_device()
    if device is None:
        internal_logger.warning("No connected Android device found via adb; keeping configured Appium deviceName.")
        return None

    changed = apply_appium_device_info(config, device)
    if changed and persist:
        try:
            persist_config(config)
        except Exception as exc:
            internal_logger.warning("Could not persist refreshed Appium device config: %s", exc)
    return device
