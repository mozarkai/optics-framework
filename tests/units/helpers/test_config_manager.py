from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from optics_framework.common.config_handler import load_project_yaml
from optics_framework.helper import config_manager

MODULE = "optics_framework.helper.config_manager"
PROJECT_CONFIG = "optics_framework.helper.project_config"
ONBOARDING = "optics_framework.helper.onboarding"
CLI = "optics_framework.helper.cli"


@pytest.fixture
def configured_helpers():
    with patch(f"{PROJECT_CONFIG}.prompt_project_config",
               return_value={"driver": "appium"}) as prompt, \
            patch(f"{PROJECT_CONFIG}.render_project_config",
                  return_value="rendered: yaml\n") as render, \
            patch(f"{PROJECT_CONFIG}.write_project_config") as write, \
            patch(f"{ONBOARDING}.print_next_steps") as next_steps:
        yield SimpleNamespace(
            prompt=prompt, render=render, write=write, next_steps=next_steps)


class TestOverwriteAskedBeforeQuestions:
    def test_no_existing_file_skips_confirm_entirely(self, tmp_path,
                                                     configured_helpers):
        with patch(f"{MODULE}.Confirm.ask") as ask:
            config_manager.configure(folder=str(tmp_path), edit=False)
        ask.assert_not_called()


class TestEditorSetsAnUnsetField:
    async def test_editor_sets_a_name_the_config_does_not_have(self, tmp_path):
        (tmp_path / "config.yaml").write_text("log_level: INFO\n", encoding="utf-8")
        app = config_manager.ProjectConfigTUI(str(tmp_path))
        async with app.run_test() as pilot:
            app.query_one("#config-list").index = app.options.index("name")
            await pilot.press("space", *"checkout_suite", "enter", "s")

        assert load_project_yaml(str(tmp_path / "config.yaml"))["name"] == "checkout_suite"
