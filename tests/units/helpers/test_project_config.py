from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
import yaml

from optics_framework.helper import project_config


def _enabled_names(config: dict, key: str) -> list[str]:
    """Names whose entry carries ``enabled: true`` under a dependency section."""
    return [
        name
        for entry in config.get(key) or []
        if isinstance(entry, dict)
        for name, cfg in entry.items()
        if isinstance(cfg, dict) and cfg.get("enabled") is True
    ]


def _appium_entry(config: dict) -> dict:
    """The appium entry (url + capabilities) under driver_sources."""
    return next(
        entry["appium"] for entry in config["driver_sources"]
        if isinstance(entry, dict) and "appium" in entry
    )


def _answers_for(platform: str, *, ocr: bool = False) -> dict:
    answers = {"platform": platform, "ocr": ocr, "log_level": "INFO"}
    if platform == "android":
        answers.update(
            device_name="emu-1",
            platform_name="Android",
            app_package=f"com.example.{platform}",
            app_activity="com.example.MainActivity",
            appium_url="http://127.0.0.1:4723",
        )
    elif platform == "ios":
        answers.update(
            device_name="emu-1",
            platform_name="iOS",
            bundle_id=f"com.example.{platform}",
            appium_url="http://127.0.0.1:4723",
        )
    elif platform == "desktop":
        answers.update(platform_name="Linux", app="mousepad", appium_url="http://127.0.0.1:4723")
    elif platform == "web-selenium":
        answers["selenium_url"] = "http://127.0.0.1:4444/wd/hub"
    elif platform == "bluetooth":
        answers.update(ble_platform="ios", ble_serial="", width=1170, height=2532, camera_url="http://127.0.0.1:9100/")
    else:  # web-playwright
        answers.update(browser="firefox", headless=True)
    return answers


class TestRenderProjectConfig:

    def test_desktop_answers_land_in_xa11y_capabilities(self):
        appium = _appium_entry(yaml.safe_load(project_config.render_project_config(_answers_for("desktop"))))
        assert appium["capabilities"] == {"platformName": "Linux", "automationName": "Xa11y", "app": "mousepad"}


class TestPromptProjectConfig:
    def test_desktop_question_flow_asks_no_platform_question(self, typed, capsys):
        typed("Windows", "notepad.exe", "http://10.0.0.5:4723", "n", "INFO")
        answers = project_config.prompt_project_config(domain="desktop")
        assert "What are you automating?" not in capsys.readouterr().out
        assert answers == {
            "platform": "desktop",
            "platform_name": "Windows",
            "app": "notepad.exe",
            "appium_url": "http://10.0.0.5:4723",
            "ocr_engine": project_config.preferred_local_engine(),
            "ocr": False,
            "log_level": "INFO",
        }

    def test_a_bluetooth_phone_on_usb_reads_its_screen_with_usb_screenshots(self, typed, monkeypatch):
        monkeypatch.setitem(sys.modules, "airhand", SimpleNamespace(serial_numbers=list))
        typed("bluetooth", "android", "", "1272", "2772", "usb", "y", "INFO")
        config = yaml.safe_load(project_config.render_project_config(project_config.prompt_project_config()))
        assert _enabled_names(config, "driver_sources") == ["ble"]
        assert _enabled_names(config, "elements_sources") == ["usb_screenshot"]
        ble = next(entry["ble"] for entry in config["driver_sources"] if "ble" in entry)
        assert ble["capabilities"]["frame"] == {"width": 1272, "height": 2772}

    @pytest.mark.parametrize(("typed_lines", "platform", "driver", "log_level"), [
        (("web-selenium", "http://grid.local:4444/wd/hub", "n", "ERROR"), "web-selenium", "selenium", "ERROR"),
        (("",) * 8, "android", "appium", "INFO"),
    ], ids=["selenium", "every default"])
    def test_prompted_answers_round_trip_through_renderer(self, typed, typed_lines, platform, driver, log_level):
        typed(*typed_lines)
        answers = project_config.prompt_project_config()
        config = yaml.safe_load(project_config.render_project_config(answers))
        assert (answers["platform"], _enabled_names(config, "driver_sources")) == (platform, [driver])
        assert config["log_level"] == log_level
