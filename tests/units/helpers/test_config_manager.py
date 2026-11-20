from __future__ import annotations


from optics_framework.common.config_handler import load_project_yaml
from optics_framework.helper import config_manager


class TestEditorSetsAnUnsetField:
    async def test_editor_sets_a_name_the_config_does_not_have(self, tmp_path):
        (tmp_path / "config.yaml").write_text("log_level: INFO\n", encoding="utf-8")
        app = config_manager.ProjectConfigTUI(str(tmp_path))
        async with app.run_test() as pilot:
            app.query_one("#config-list").index = app.options.index("name")
            await pilot.press("space", *"checkout_suite", "enter", "s")

        assert load_project_yaml(str(tmp_path / "config.yaml"))["name"] == "checkout_suite"
