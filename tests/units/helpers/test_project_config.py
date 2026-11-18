"""Unit tests for the project-config builder (``optics_framework/helper/project_config.py``).

The renderer is exercised as a pure function per platform and re-parsed with
``yaml.safe_load`` to prove the generated text is real, loadable YAML where
exactly one driver and its matching elements sources are enabled. The prompt
flow is tested against scripted rich prompts (no interaction).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml

from optics_framework.helper import project_config

pytestmark = pytest.mark.white_box

MODULE = "optics_framework.helper.project_config"

_DRIVER_BY_PLATFORM = {
    "android": "appium",
    "ios": "appium",
    "desktop": "appium",
    "web-selenium": "selenium",
    "web-playwright": "playwright",
    "bluetooth": "ble",
}


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


# --------------------------------------------------------------------------- #
# render_project_config                                                        #
# --------------------------------------------------------------------------- #

class TestRenderProjectConfig:

    @pytest.mark.parametrize("camera_url", ["http://127.0.0.1:9100/", ""])
    def test_bluetooth_answers_make_a_runnable_ble_entry(self, camera_url):
        from optics_framework.engines.drivers.ble import Capabilities

        answers = {**_answers_for("bluetooth"), "ble_serial": "ABC123", "camera_url": camera_url}
        config = yaml.safe_load(project_config.render_project_config(answers))
        ble = next(e["ble"] for e in config["driver_sources"] if "ble" in e)
        caps = Capabilities(**ble["capabilities"])
        assert (caps.serial, caps.platform, caps.frame.width, caps.frame.height) == ("ABC123", "ios", 1170, 2532)
        camera = next(e["camera_screenshot"] for e in config["elements_sources"] if "camera_screenshot" in e)
        assert camera["enabled"] is bool(camera_url)
        assert camera.get("capabilities", {}).get("url") == (camera_url or None)

    def test_desktop_answers_land_in_xa11y_capabilities(self):
        appium = _appium_entry(yaml.safe_load(project_config.render_project_config(_answers_for("desktop"))))
        assert appium["capabilities"] == {"platformName": "Linux", "automationName": "Xa11y", "app": "mousepad"}

    def test_unknown_platform_falls_back_to_android(self):
        text = project_config.render_project_config({"platform": "toaster"})
        assert _enabled_names(yaml.safe_load(text), "driver_sources") == ["appium"]


# --------------------------------------------------------------------------- #
# write_project_config                                                         #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# prompt_project_config                                                        #
# --------------------------------------------------------------------------- #

class TestPromptProjectConfig:
    @pytest.mark.parametrize(
        "domain, expected_choices, expected_default",
        [
            ("mobile", ["android", "ios"], "android"),
            ("web", ["web-playwright", "web-selenium"], "web-playwright"),
            (None, list(_DRIVER_BY_PLATFORM), "android"),
        ],
    )
    def test_platform_choices_follow_domain(
            self, domain, expected_choices, expected_default):
        with patch(f"{MODULE}.Prompt.ask", return_value="ios") as ask, \
                patch(f"{MODULE}.Confirm.ask", return_value=False):
            project_config.prompt_project_config(domain=domain)
        first_question = ask.call_args_list[0]
        assert first_question.kwargs["choices"] == expected_choices
        assert first_question.kwargs["default"] == expected_default

    def test_android_question_flow(self):
        ask_values = [
            "android",              # platform
            "emu-42",               # device name
            "Android",              # platform name
            "com.acme.app",         # app package
            "com.acme.MainActivity",  # app activity
            "http://127.0.0.1:4723",  # appium url
            "DEBUG",                # log level
        ]
        with patch(f"{MODULE}.Prompt.ask", side_effect=ask_values), \
                patch(f"{MODULE}.Confirm.ask", return_value=True):
            answers = project_config.prompt_project_config()
        assert answers == {
            "platform": "android",
            "device_name": "emu-42",
            "platform_name": "Android",
            "app_package": "com.acme.app",
            "app_activity": "com.acme.MainActivity",
            "appium_url": "http://127.0.0.1:4723",
            "ocr_engine": project_config.preferred_local_engine(),
            "ocr": True,
            "log_level": "DEBUG",
        }

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

    def test_web_playwright_question_flow(self):
        ask_values = ["web-playwright", "webkit", "WARNING"]
        confirm_values = [True, False]  # headless, ocr
        with patch(f"{MODULE}.Prompt.ask", side_effect=ask_values), \
                patch(f"{MODULE}.Confirm.ask", side_effect=confirm_values):
            answers = project_config.prompt_project_config()
        assert answers == {
            "platform": "web-playwright",
            "browser": "webkit",
            "headless": True,
            "ocr_engine": project_config.preferred_local_engine(),
            "ocr": False,
            "log_level": "WARNING",
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
