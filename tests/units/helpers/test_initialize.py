"""Unit tests for ``optics_framework.helper.initialize`` and the CLI init flow.

Covers:
- ``create_project`` scaffolding with an already-resolved name (the positional
  vs ``--name`` resolution happens in ``cli.InitCommand.execute``; create_project
  itself just consumes ``args.name``).
- The interactive template picker: ``_template_choices`` is built from
  ``samples/metadata.json`` (the ``displayName`` field), with a "Blank project"
  sentinel; stdin-driven ``_pick_template`` runs against patched input.
- The CLI positional-name resolution contract in ``cli.InitCommand.execute``
  (positional wins; positional and ``--name`` that differ raise; ``--name``
  alone soft-deprecates).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from optics_framework.common.config_handler import Config, load_project_yaml, resolve_config_file

from optics_framework.helper import initialize as init_mod
from optics_framework.helper.initialize import (
    _pick_template,
    _template_choices,
    create_project,
)

pytestmark = pytest.mark.white_box

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


class TestBlankProjectGuidance:
    """A blank project's test_cases.csv ships empty; its guidance output must
    say where the first steps go and how to discover them (one line appended
    to the existing next-steps block — no new block)."""

    def test_blank_config_shows_how_to_drive_a_desktop_app(self, tmp_path):
        with patch(f"{init_mod.__name__}.print_next_steps"):
            create_project(_args("blank", path=str(tmp_path)))
        assert "automationName: Xa11y" in (tmp_path / "blank" / "config.yaml").read_text()

    def test_force_overwrites_existing(self, tmp_path):
        project = tmp_path / "exists"
        project.mkdir()
        (project / "old.txt").write_text("old", encoding="utf-8")
        with patch(f"{init_mod.__name__}.print_next_steps"):
            create_project(_args("exists", path=str(tmp_path), force=True))
        assert not (project / "old.txt").exists()
        assert (project / "config.yaml").exists()

    def test_unknown_template_raises_before_creating_anything(self, tmp_path):
        args = _args("tmpl2", path=str(tmp_path), template="nope")
        with patch(f"{init_mod.__name__}._check_and_prepare_directory") as prepare, \
                patch(f"{init_mod.__name__}.print_next_steps"):
            with pytest.raises(ValueError,
                               match="Template 'nope' not found.*Available templates"):
                create_project(args)
        prepare.assert_not_called()
        assert not (tmp_path / "tmpl2").exists()


class TestInteractivePicker:
    """create_project's own picker: TTY-gated and opt-out via pick_template."""

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


class TestTemplateChoices:


    def test_choices_fallback_on_malformed_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setattr(init_mod, "_samples_dir", lambda: tmp_path)
        (tmp_path / "metadata.json").write_text("{ not valid json", encoding="utf-8")
        choices = _template_choices()
        assert choices[0] == ("(Blank project)", "")


class TestPickTemplate:
    """stdin-driven picker; ``input`` is patched so no real interaction occurs."""

    def test_cancel_returns_none(self):
        with patch("builtins.input", return_value=""):
            assert _pick_template() is None

    def test_non_numeric_input_reprompts(self):
        with patch("builtins.input", side_effect=["abc", "2"]):
            assert _pick_template() == "contact"

    def test_out_of_range_input_reprompts(self):
        with patch("builtins.input", side_effect=["99", "1"]):
            assert _pick_template() == ""


# --------------------------------------------------------------------------- #
# CLI positional-name resolution                                               #
# --------------------------------------------------------------------------- #


class TestInitPositionalResolution:
    """The positional vs ``--name`` resolution lives in ``cli.InitCommand.execute``."""

    def _make_args(self, positional=None, flag=None, **kw):
        return SimpleNamespace(
            name_positional=positional,
            name_flag=flag,
            path=kw.get("path"),
            force=kw.get("force", False),
            template=kw.get("template"),
            git_init=kw.get("git_init", False),
        )

    def test_positional_and_flag_differ_raises(self, tmp_path):
        from optics_framework.helper.cli import InitCommand

        cmd = InitCommand()
        args = self._make_args(positional="a", flag="b", path=str(tmp_path))
        with patch(f"{CLI}.create_project"):
            with pytest.raises(ValueError, match="Conflicting project names"):
                cmd.execute(args)

    def test_flag_only_soft_deprecates(self, tmp_path, capsys):
        from optics_framework.helper.cli import InitCommand

        with patch(f"{CLI}.create_project") as create:
            InitCommand().execute(self._make_args(flag="legacy", path=str(tmp_path)))
        out = capsys.readouterr().out
        assert "deprecated" in out.lower()
        assert create.call_args.args[0].name == "legacy"

    def test_no_name_anywhere_noninteractive_raises(self, tmp_path):
        from optics_framework.helper.cli import InitCommand

        cmd = InitCommand()
        args = self._make_args(path=str(tmp_path))
        with patch(f"{CLI}.create_project"), \
                patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            stdin.readline.return_value = ""
            with pytest.raises(ValueError, match="project name is required"):
                cmd.execute(args)

    def test_existing_project_without_force_raises(self, tmp_path):
        # The CLI-level refusal must raise (main() maps ValueError → exit 3),
        # not print and return with exit 0.
        from optics_framework.helper.cli import InitCommand

        (tmp_path / "dup").mkdir()
        cmd = InitCommand()
        args = self._make_args(positional="dup", path=str(tmp_path))
        with patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            stdin.readline.return_value = ""
            with pytest.raises(ValueError, match="already exists"):
                cmd.execute(args)
