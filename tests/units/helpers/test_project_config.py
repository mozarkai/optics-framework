"""Unit tests for the project-config builder (``optics_framework/helper/project_config.py``).

The renderer is exercised as a pure function per platform and re-parsed with
``yaml.safe_load`` to prove the generated text is real, loadable YAML where
exactly one driver and its matching elements sources are enabled. The prompt
flow is tested against scripted rich prompts (no interaction).
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import yaml

from optics_framework.helper import project_config
from optics_framework.helper.project_config import (
    _ELEMENTS_SOURCES_BY_DRIVER,
)

pytestmark = pytest.mark.white_box

MODULE = "optics_framework.helper.project_config"

_DRIVER_BY_PLATFORM = {
    "android": "appium",
    "ios": "appium",
    "web-selenium": "selenium",
    "web-playwright": "playwright",
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
    elif platform == "web-selenium":
        answers["selenium_url"] = "http://127.0.0.1:4444/wd/hub"
    else:  # web-playwright
        answers.update(browser="firefox", headless=True)
    return answers


# --------------------------------------------------------------------------- #
# render_project_config                                                        #
# --------------------------------------------------------------------------- #

class TestRenderProjectConfig:
    @pytest.mark.parametrize("platform", list(_DRIVER_BY_PLATFORM))
    def test_exactly_one_driver_and_matching_sources_enabled(self, platform):
        text = project_config.render_project_config(_answers_for(platform))
        config = yaml.safe_load(text)
        driver = _DRIVER_BY_PLATFORM[platform]

        assert _enabled_names(config, "driver_sources") == [driver]
        expected = sorted(_ELEMENTS_SOURCES_BY_DRIVER[driver])
        assert sorted(_enabled_names(config, "elements_sources")) == expected
        # Every other listed source stays disabled, not deleted.
        all_sources = {s for group in _ELEMENTS_SOURCES_BY_DRIVER.values() for s in group}
        disabled = all_sources - set(expected)
        flat = [name for e in config["elements_sources"] for name in e]
        assert set(disabled) <= set(flat)

    def test_android_answers_land_in_appium_capabilities(self):
        answers = _answers_for("android")
        config = yaml.safe_load(project_config.render_project_config(answers))
        appium = _appium_entry(config)
        assert appium["url"] == answers["appium_url"]
        caps = appium["capabilities"]
        assert caps["deviceName"] == "emu-1"
        assert caps["platformName"] == "Android"
        assert caps["appPackage"] == "com.example.android"
        assert caps["appActivity"] == "com.example.MainActivity"
        assert caps["automationName"] == "UiAutomator2"

    def test_ios_answers_use_bundle_id(self):
        answers = _answers_for("ios")
        text = project_config.render_project_config(answers)
        # Android-only launch capabilities must not leak into an iOS config.
        assert "bundleId" in text
        assert "appPackage" not in text
        assert "appActivity" not in text
        appium = _appium_entry(yaml.safe_load(text))
        assert appium["url"] == answers["appium_url"]
        caps = appium["capabilities"]
        assert caps["bundleId"] == "com.example.ios"
        assert caps["automationName"] == "XCUITest"
        assert caps["deviceName"] == "emu-1"
        assert caps["platformName"] == "iOS"

    def test_ios_prompt_flow_asks_bundle_id_not_app_package(self):
        ask_values = [
            "ios",                    # platform
            "iPhone 16",              # device name
            "iOS",                    # platform name
            "com.acme.ios",           # bundle id
            "http://127.0.0.1:4723",  # appium url
            "INFO",                   # log level
        ]
        with patch(f"{MODULE}.Prompt.ask", side_effect=ask_values), \
                patch(f"{MODULE}.Confirm.ask", return_value=False):
            answers = project_config.prompt_project_config()
        assert answers == {
            "platform": "ios",
            "device_name": "iPhone 16",
            "platform_name": "iOS",
            "bundle_id": "com.acme.ios",
            "appium_url": "http://127.0.0.1:4723",
            "ocr": False,
            "log_level": "INFO",
        }

    def test_selenium_url_used(self):
        config = yaml.safe_load(
            project_config.render_project_config(_answers_for("web-selenium")))
        selenium = next(
            entry["selenium"] for entry in config["driver_sources"]
            if isinstance(entry, dict) and "selenium" in entry
        )
        assert selenium["url"] == "http://127.0.0.1:4444/wd/hub"

    def test_playwright_browser_and_headless_used(self):
        config = yaml.safe_load(
            project_config.render_project_config(_answers_for("web-playwright")))
        pw = next(
            entry["playwright"] for entry in config["driver_sources"]
            if isinstance(entry, dict) and "playwright" in entry
        )
        assert pw["capabilities"]["browser"] == "firefox"
        assert pw["capabilities"]["headless"] is True

    @pytest.mark.parametrize("ocr, expected", [(True, True), (False, False)])
    def test_ocr_toggles_easyocr_only(self, ocr, expected):
        text = project_config.render_project_config(_answers_for("android", ocr=ocr))
        config = yaml.safe_load(text)
        assert _enabled_names(config, "text_detection") == (["easyocr"] if expected else [])
        assert _enabled_names(config, "image_detection") == []

    def test_missing_keys_fall_back_to_defaults(self):
        text = project_config.render_project_config({})
        config = yaml.safe_load(text)
        assert _enabled_names(config, "driver_sources") == ["appium"]
        assert config["log_level"] == "INFO"

    def test_unknown_platform_falls_back_to_android(self):
        text = project_config.render_project_config({"platform": "toaster"})
        assert _enabled_names(yaml.safe_load(text), "driver_sources") == ["appium"]

    def test_output_keeps_explanatory_comments(self):
        text = project_config.render_project_config({})
        assert "# Optics Framework project configuration." in text
        assert "# Install:" in text or "optics setup --install" in text

    def test_header_does_not_name_the_calling_command(self):
        # Both `optics quickstart` and `optics configure` write rendered
        # configs, so the provenance line must not credit either command.
        text = project_config.render_project_config({})
        assert "quickstart" not in text
        assert "configure" not in text


# --------------------------------------------------------------------------- #
# write_project_config                                                         #
# --------------------------------------------------------------------------- #

class TestWriteProjectConfig:
    def test_writes_config_yaml_into_folder(self, tmp_path):
        folder = str(tmp_path / "nested" / "demo")
        path = project_config.write_project_config(folder, "hello: true\n")
        assert path == os.path.join(folder, "config.yaml")
        with open(path, encoding="utf-8") as fh:
            assert fh.read() == "hello: true\n"

    def test_overwrites_unconditionally(self, tmp_path):
        folder = str(tmp_path / "demo")
        project_config.write_project_config(folder, "old\n")
        project_config.write_project_config(folder, "new\n")
        with open(os.path.join(folder, "config.yaml"), encoding="utf-8") as fh:
            assert fh.read() == "new\n"


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
            "ocr": True,
            "log_level": "DEBUG",
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


class TestConfirmBlankLineDefault:
    """Regression pin for a QA observation: rich Confirm.ask was reported to
    reject an empty line with "Please enter Y or N" on piped stdin, even when
    a default was shown. Against the pinned rich (14.x) a blank line DOES
    return the default — this test fails if a future upgrade regresses it,
    which would justify replacing Confirm.ask with a local y/n helper."""

    @staticmethod
    def _blank_input(monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *_a, **_kw: "")

    def test_blank_line_returns_default_false(self, monkeypatch):
        self._blank_input(monkeypatch)
        from rich.prompt import Confirm
        assert Confirm.ask("Run headless?", default=False) is False

    def test_blank_line_returns_default_true(self, monkeypatch):
        self._blank_input(monkeypatch)
        from rich.prompt import Confirm
        assert Confirm.ask("Install now?", default=True) is True

    def test_y_and_n_still_parse_case_insensitively(self, monkeypatch):
        from rich.prompt import Confirm
        monkeypatch.setattr("builtins.input", lambda *_a, **_kw: "Y")
        assert Confirm.ask("x?", default=False) is True
        monkeypatch.setattr("builtins.input", lambda *_a, **_kw: "n")
        assert Confirm.ask("x?", default=True) is False
