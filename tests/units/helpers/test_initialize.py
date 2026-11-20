from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from optics_framework.common.config_handler import Config, load_project_yaml, resolve_config_file

from optics_framework.helper import initialize as init_mod
from optics_framework.helper.initialize import (
    _pick_template,
    create_project,
)


CLI = "optics_framework.helper.cli"


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
        with patch(f"{init_mod.__name__}.print_next_steps"):
            create_project(_args("checkout$UNSET", path=str(tmp_path), template=template))
        moved = (tmp_path / "checkout$UNSET").rename(tmp_path / "test-script")

        config = Config(**resolve_config_file(load_project_yaml(str(moved / "config.yaml")), str(moved)))
        assert config.name == "checkout$UNSET"
        assert config.driver_sources


class TestInteractivePicker:
    @staticmethod
    def _on_tty(monkeypatch):
        class FakeStdin:
            def isatty(self):
                return True

        monkeypatch.setattr(init_mod.sys, "stdin", FakeStdin())

    def test_cancelled_picker_creates_no_directory(self, tmp_path, monkeypatch):
        self._on_tty(monkeypatch)
        with patch(f"{init_mod.__name__}._pick_template", return_value=None):
            create_project(_args("ghost", path=str(tmp_path)))
        assert not (tmp_path / "ghost").exists()


class TestPickTemplate:

    def test_non_numeric_input_reprompts(self):
        with patch("builtins.input", side_effect=["abc", "2"]):
            assert _pick_template() == "contact"

    def test_out_of_range_input_reprompts(self):
        with patch("builtins.input", side_effect=["99", "1"]):
            assert _pick_template() == ""


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
        from optics_framework.helper.cli import InitCommand

        with patch(f"{CLI}.create_project") as create:
            InitCommand().execute(self._make_args(flag="legacy", path=str(tmp_path)))
        out = capsys.readouterr().out
        assert "deprecated" in out.lower()
        assert create.call_args.args[0].name == "legacy"
