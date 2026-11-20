from __future__ import annotations

import asyncio

import pytest

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


def _editor_pressing(*keys):
    async def drive(app):
        async with app.run_test() as pilot:
            await pilot.press(*keys)
    return lambda app: asyncio.run(drive(app))


@pytest.mark.parametrize("permissions, shown", [
    ({"telepathy": "allow"}, "Unknown permission 'telepathy'"),
    ({"camera": "yes_please"}, "permissions.camera: Input should be 'allow' or 'deny' (got 'yes_please')"),
])
class TestConfigureOpensAConfigWithABadValue:
    def test_edit_warns_then_saves_the_bad_value_as_written(self, tmp_path, monkeypatch, capsys, permissions, shown):
        path = tmp_path / "config.yaml"
        path.write_text(f"log_level: INFO\npermissions: {permissions}\n", encoding="utf-8")
        monkeypatch.setattr(config_manager.ProjectConfigTUI, "run", _editor_pressing("s"))
        config_manager.configure(str(tmp_path), edit=True)
        warning = capsys.readouterr().err
        assert "E0501" in warning
        assert shown in " ".join(warning.split())
        assert load_project_yaml(str(path))["permissions"] == permissions

    def test_the_guided_path_warns_before_it_asks_to_overwrite(self, tmp_path, monkeypatch, capsys, permissions, shown):
        (tmp_path / "config.yaml").write_text(f"permissions: {permissions}\n", encoding="utf-8")
        monkeypatch.setattr(config_manager.Confirm, "ask", lambda *a, **k: False)
        config_manager.configure(str(tmp_path))
        assert shown in " ".join(capsys.readouterr().err.split())
