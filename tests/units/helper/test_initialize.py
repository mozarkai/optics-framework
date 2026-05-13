from optics_framework.common import device_config
from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.helper import initialize


def test_get_connected_android_device_prefers_market_name(monkeypatch):
    outputs = {
        ("devices",): "List of devices attached\nemulator-5554\tdevice\nABC123\tdevice\n",
        ("-s", "ABC123", "shell", "getprop", "ro.product.marketname"): "Redmi Note 12\n",
    }

    def fake_run_adb(args):
        return outputs.get(tuple(args), "").strip()

    monkeypatch.setattr(device_config, "_run_adb", fake_run_adb)

    device = device_config.get_connected_android_device()

    assert device is not None
    assert device.serial == "ABC123"
    assert device.name == "Redmi Note 12"


def test_get_connected_android_device_falls_back_to_brand_and_model(monkeypatch):
    outputs = {
        ("devices",): "List of devices attached\nABC123\tdevice\n",
        ("-s", "ABC123", "shell", "getprop", "ro.product.model"): "Pixel 8\n",
        ("-s", "ABC123", "shell", "getprop", "ro.product.brand"): "Google\n",
    }

    def fake_run_adb(args):
        return outputs.get(tuple(args), "").strip()

    monkeypatch.setattr(device_config, "_run_adb", fake_run_adb)

    device = device_config.get_connected_android_device()

    assert device is not None
    assert device.serial == "ABC123"
    assert device.name == "Google Pixel 8"


def test_apply_appium_device_info_sets_device_name_and_udid():
    config = {
        "driver_sources": [
            {
                "appium": {
                    "enabled": True,
                    "capabilities": {
                        "deviceName": "emulator-5554",
                        "platformName": "Android",
                    },
                }
            }
        ]
    }
    device = initialize.AndroidDeviceInfo(serial="ABC123", name="Pixel 8")

    initialize._apply_appium_device_info(config, device)

    capabilities = config["driver_sources"][0]["appium"]["capabilities"]
    assert capabilities["deviceName"] == "Pixel 8"
    assert capabilities["udid"] == "ABC123"


def test_refresh_appium_device_config_persists_project_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("driver_sources: []\n", encoding="utf-8")
    config = Config(
        project_path=str(tmp_path),
        driver_sources=[
            {
                "appium": DependencyConfig(
                    enabled=True,
                    url="http://localhost:4723",
                    capabilities={
                        "deviceName": "emulator-5554",
                        "platformName": "Android",
                    },
                )
            }
        ],
    )
    monkeypatch.setattr(
        device_config,
        "get_connected_android_device",
        lambda: device_config.AndroidDeviceInfo(serial="ABC123", name="Pixel 8"),
    )

    device = device_config.refresh_appium_device_config(config)

    assert device is not None
    assert config.driver_sources[0]["appium"].capabilities["deviceName"] == "Pixel 8"
    written = config_path.read_text(encoding="utf-8")
    assert "deviceName: Pixel 8" in written
    assert "udid: ABC123" in written
