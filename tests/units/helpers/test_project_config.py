from __future__ import annotations

from unittest.mock import patch

import yaml

from optics_framework.helper import project_config


MODULE = "optics_framework.helper.project_config"


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


    def test_desktop_question_flow_asks_no_platform_question(self):
        ask_values = ["Windows", "notepad.exe", "http://10.0.0.5:4723", "INFO"]
        with patch(f"{MODULE}.Prompt.ask", side_effect=ask_values) as ask, \
                patch(f"{MODULE}.Confirm.ask", return_value=False):
            answers = project_config.prompt_project_config(domain="desktop")
        assert ask.call_args_list[0].kwargs["choices"] == ["Mac", "Linux", "Windows"]
        assert answers == {
            "platform": "desktop",
            "platform_name": "Windows",
            "app": "notepad.exe",
            "appium_url": "http://10.0.0.5:4723",
            "ocr_engine": project_config.preferred_local_engine(),
            "ocr": False,
            "log_level": "INFO",
        }

    def test_prompted_answers_round_trip_through_renderer(self):
        ask_values = [
            "web-selenium", "http://grid.local:4444/wd/hub", "ERROR",
        ]
        with patch(f"{MODULE}.Prompt.ask", side_effect=ask_values), \
                patch(f"{MODULE}.Confirm.ask", return_value=False):
            answers = project_config.prompt_project_config()
        config = yaml.safe_load(project_config.render_project_config(answers))
        assert _enabled_names(config, "driver_sources") == ["selenium"]
        assert config["log_level"] == "ERROR"
