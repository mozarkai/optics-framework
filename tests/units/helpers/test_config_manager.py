from __future__ import annotations

import asyncio

import pytest
import yaml

from optics_framework.common.config_handler import config_from, load_project_yaml
from optics_framework.helper import config_manager

NOTICE = {"name": "notice", "detect": {"text_contains": ["Notice"]}, "action": {"type": "back"}}
NOTICE_YAML = "{name: notice, detect: {text_contains: [Notice]}, action: {type: back}}"


class TestEditorSetsAnUnsetField:
    async def test_editor_sets_a_name_the_config_does_not_have(self, tmp_path):
        (tmp_path / "config.yaml").write_text("log_level: INFO\n", encoding="utf-8")
        app = config_manager.ProjectConfigTUI(str(tmp_path))
        async with app.run_test() as pilot:
            app.query_one("#config-list").index = app.options.index("name")
            await pilot.press("space", *"checkout_suite", "enter", "s")

        assert load_project_yaml(str(tmp_path / "config.yaml"))["name"] == "checkout_suite"

    @pytest.mark.parametrize("field, kept, entered, reason", [
        ("permissions", {"camera": "allow"}, "{camera: maybe}", "Input should be 'allow' or 'deny'"),
        ("popup_rules", [NOTICE], f"[{NOTICE_YAML}, {NOTICE_YAML}]", "popup_rules: rule names must be unique"),
    ], ids=["field type", "whole config"])
    async def test_editor_shows_why_it_refuses_a_value_and_keeps_the_old_one(self, tmp_path, field, kept, entered, reason):
        (tmp_path / "config.yaml").write_text(yaml.safe_dump({field: kept}), encoding="utf-8")
        app = config_manager.ProjectConfigTUI(str(tmp_path))
        async with app.run_test() as pilot:
            app.query_one("#config-list").index = app.options.index(field)
            await pilot.press("space")
            app.query_one("#edit-input").value = entered
            await pilot.press("enter")
            assert reason in str(app.screen.query_one(".error-message").render())
            await pilot.click("#ok")
            await pilot.press("s")

        saved = config_from(load_project_yaml(str(tmp_path / "config.yaml")))
        assert getattr(saved, field) == getattr(config_from({field: kept}), field)

def _editor_setting(field, value):
    async def drive(app):
        async with app.run_test() as pilot:
            app.query_one("#config-list").index = app.options.index(field)
            await pilot.press("space")
            app.query_one("#edit-input").value = value
            await pilot.press("enter", "s")
    return lambda app: asyncio.run(drive(app))


@pytest.mark.parametrize("permissions, shown", [
    ({"telepathy": "allow"}, "Unknown permission 'telepathy'"),
    ({"camera": "yes_please"}, "permissions.camera: Input should be 'allow' or 'deny' (got 'yes_please')"),
    ({"camera": "[/]"}, "permissions.camera: Input should be 'allow' or 'deny' (got '[/]')"),
])
class TestConfigureOpensAConfigWithABadValue:
    def test_edit_warns_then_saves_the_bad_value_as_written(self, tmp_path, monkeypatch, capsys, permissions, shown):
        path = tmp_path / "config.yaml"
        path.write_text(f"log_level: INFO\npermissions: {permissions}\n", encoding="utf-8")
        monkeypatch.setattr(config_manager.ProjectConfigTUI, "run", _editor_setting("name", "checkout_suite"))
        config_manager.configure(str(tmp_path), edit=True)
        warning = capsys.readouterr().err
        assert "E0501" in warning
        assert shown in " ".join(warning.split())
        saved = load_project_yaml(str(path))
        assert (saved["name"], saved["permissions"]) == ("checkout_suite", permissions)

    def test_edit_repairs_the_bad_value(self, tmp_path, monkeypatch, permissions, shown):
        path = tmp_path / "config.yaml"
        path.write_text(f"permissions: {permissions}\n", encoding="utf-8")
        monkeypatch.setattr(config_manager.ProjectConfigTUI, "run", _editor_setting("permissions", "{camera: allow}"))
        config_manager.configure(str(tmp_path), edit=True)
        assert load_project_yaml(str(path))["permissions"] == {"camera": "allow"}

    def test_the_guided_path_warns_before_it_asks_to_overwrite(self, tmp_path, monkeypatch, capsys, permissions, shown):
        (tmp_path / "config.yaml").write_text(f"permissions: {permissions}\n", encoding="utf-8")
        monkeypatch.setattr(config_manager.Confirm, "ask", lambda *a, **k: False)
        config_manager.configure(str(tmp_path))
        assert shown in " ".join(capsys.readouterr().err.split())


def test_configure_offers_to_replace_a_config_that_is_not_a_mapping(tmp_path, monkeypatch, capsys):
    (tmp_path / "config.yaml").write_text("- camera\n", encoding="utf-8")
    monkeypatch.setattr(config_manager.Confirm, "ask", lambda *a, **k: False)
    config_manager.configure(str(tmp_path))
    assert "Aborted; existing config left unchanged." in capsys.readouterr().out
