from __future__ import annotations

from types import SimpleNamespace

import pytest

from optics_framework.common.config_handler import Config, load_project_yaml, resolve_config_file
from optics_framework.helper.cli import InitCommand
from optics_framework.helper.initialize import create_project


def _args(name, **kw):
    """A minimal args namespace matching the create_project attribute contract."""
    return SimpleNamespace(
        name=name,
        path=kw.get("path"),
        force=kw.get("force", False),
        template=kw.get("template"),
        git_init=kw.get("git_init", False),
    )


class TestCreateProject:
    @pytest.mark.parametrize("template", [None, "contact", "mobile_web"])
    def test_config_keeps_the_project_name_after_the_folder_is_renamed(self, tmp_path, monkeypatch, template):
        monkeypatch.delenv("UNSET", raising=False)
        create_project(_args("checkout$UNSET", path=str(tmp_path), template=template))
        moved = (tmp_path / "checkout$UNSET").rename(tmp_path / "test-script")

        config = Config(**resolve_config_file(load_project_yaml(str(moved / "config.yaml")), str(moved)))
        assert config.name == "checkout$UNSET"
        assert config.driver_sources


class TestInteractivePicker:
    def test_cancelled_picker_creates_no_directory(self, tmp_path, typed):
        typed("")
        create_project(_args("ghost", path=str(tmp_path)))
        assert not (tmp_path / "ghost").exists()

    @pytest.mark.parametrize(("typed_lines", "from_sample"), [
        (("abc", "2"), True),
        (("99", "1"), False),
    ], ids=["a word, then Contacts", "out of range, then Blank"])
    def test_an_answer_that_is_not_a_choice_asks_again(self, tmp_path, typed, capsys, typed_lines, from_sample):
        typed(*typed_lines)
        create_project(_args("app", path=str(tmp_path)))
        assert (tmp_path / "app" / "test_data" / "error_definitions.csv").exists() is from_sample
        assert ("Please enter a number." in capsys.readouterr().out) is typed_lines[0].isalpha()


class TestInitPositionalResolution:
    def _make_args(self, positional=None, flag=None, **kw):
        return SimpleNamespace(
            name_positional=positional,
            name_flag=flag,
            path=kw.get("path"),
            force=kw.get("force", False),
            template=kw.get("template"),
            git_init=kw.get("git_init", False),
        )

    def test_flag_only_soft_deprecates(self, tmp_path, capsys):
        InitCommand().execute(self._make_args(flag="legacy", path=str(tmp_path)))
        assert "deprecated" in capsys.readouterr().out.lower()
        assert (tmp_path / "legacy" / "config.yaml").is_file()
