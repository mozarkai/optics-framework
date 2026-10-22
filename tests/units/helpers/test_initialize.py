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
    def test_scaffolds_blank_project_when_no_template(self, tmp_path):
        project = tmp_path / "myproj"
        # pytest's stdin is not a TTY, so the interactive picker is skipped and
        # the blank scaffold is built directly.
        with patch(f"{init_mod.__name__}._pick_template") as pick, \
                patch(f"{init_mod.__name__}.print_next_steps") as next_steps:
            create_project(_args("myproj", path=str(tmp_path)))

        pick.assert_not_called()
        assert project.is_dir()
        assert (project / "config.yaml").exists()
        next_steps.assert_called_once_with(str(project), configured=False)

    def test_template_copy_marks_configured(self, tmp_path):
        project = tmp_path / "fromsample"
        with patch(f"{init_mod.__name__}._copy_template", return_value=True) as copy, \
                patch(f"{init_mod.__name__}.print_next_steps") as next_steps:
            create_project(_args("fromsample", path=str(tmp_path), template="contact"))

        copy.assert_called_once_with(str(project), "contact")
        next_steps.assert_called_once_with(str(project), configured=True)

    def test_show_next_steps_false_suppresses_block(self, tmp_path):
        project = tmp_path / "quiet"
        with patch(f"{init_mod.__name__}.print_next_steps") as next_steps:
            create_project(_args("quiet", path=str(tmp_path)),
                           show_next_steps=False)

        assert (project / "config.yaml").exists()
        next_steps.assert_not_called()

    def test_show_next_steps_defaults_to_true(self, tmp_path):
        with patch(f"{init_mod.__name__}.print_next_steps") as next_steps:
            create_project(_args("loud", path=str(tmp_path)))

        next_steps.assert_called_once_with(str(tmp_path / "loud"),
                                           configured=False)


class TestBlankProjectGuidance:
    """A blank project's test_cases.csv ships empty; its guidance output must
    say where the first steps go and how to discover them (one line appended
    to the existing next-steps block — no new block)."""

    def test_blank_project_prints_csv_and_list_hint(self, tmp_path, capsys):
        with patch(f"{init_mod.__name__}.print_next_steps"):
            create_project(_args("blank", path=str(tmp_path)))
        out = capsys.readouterr().out
        assert "test_cases/test_cases.csv" in out
        assert "`optics list`" in out

    def test_template_project_prints_no_extra_hint(self, tmp_path, capsys):
        with patch(f"{init_mod.__name__}._copy_template", return_value=True), \
                patch(f"{init_mod.__name__}.print_next_steps"):
            create_project(_args("templated", path=str(tmp_path),
                                 template="contact"))
        assert "test_cases/test_cases.csv" not in capsys.readouterr().out

    def test_suppressed_next_steps_skips_the_hint_too(self, tmp_path, capsys):
        with patch(f"{init_mod.__name__}.print_next_steps"):
            create_project(_args("quiet", path=str(tmp_path)),
                           show_next_steps=False)
        assert "test_cases/test_cases.csv" not in capsys.readouterr().out

    def test_force_overwrites_existing(self, tmp_path):
        project = tmp_path / "exists"
        project.mkdir()
        (project / "old.txt").write_text("old", encoding="utf-8")
        with patch(f"{init_mod.__name__}.print_next_steps"):
            create_project(_args("exists", path=str(tmp_path), force=True))
        assert not (project / "old.txt").exists()
        assert (project / "config.yaml").exists()

    def test_existing_dir_without_force_raises_and_keeps_contents(self, tmp_path):
        project = tmp_path / "exists"
        project.mkdir()
        (project / "keep.txt").write_text("keep", encoding="utf-8")
        args = _args("exists", path=str(tmp_path))
        with patch(f"{init_mod.__name__}.print_next_steps"):
            with pytest.raises(ValueError, match="already exists"):
                create_project(args)
        assert (project / "keep.txt").exists()

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

    def test_tty_without_template_uses_picker_result(self, tmp_path, monkeypatch):
        self._on_tty(monkeypatch)
        project = tmp_path / "picked"
        with patch(f"{init_mod.__name__}._pick_template", return_value="") as pick, \
                patch(f"{init_mod.__name__}._copy_template", return_value=True) as copy, \
                patch(f"{init_mod.__name__}.print_next_steps"):
            create_project(_args("picked", path=str(tmp_path)))
        pick.assert_called_once()
        copy.assert_not_called()  # "" means blank → scaffold, not copy
        assert (project / "config.yaml").exists()

    def test_pick_template_false_never_prompts_even_on_tty(self, tmp_path, monkeypatch):
        # Regression pin for the quickstart double-prompt: an embedded caller
        # that already asked its own template question passes
        # pick_template=False, so template=None deterministically means blank.
        self._on_tty(monkeypatch)
        project = tmp_path / "embedded"
        with patch(f"{init_mod.__name__}._pick_template") as pick, \
                patch(f"{init_mod.__name__}.print_next_steps"):
            create_project(_args("embedded", path=str(tmp_path)),
                           pick_template=False)
        pick.assert_not_called()
        assert (project / "config.yaml").exists()

    def test_cancelled_picker_creates_no_directory(self, tmp_path, monkeypatch):
        self._on_tty(monkeypatch)
        with patch(f"{init_mod.__name__}._pick_template", return_value=None):
            create_project(_args("ghost", path=str(tmp_path)))
        assert not (tmp_path / "ghost").exists()


class TestTemplateChoices:
    def test_choices_built_from_metadata_display_names(self):
        choices = _template_choices()
        # First entry is always the Blank-project sentinel.
        assert choices[0] == ("(Blank project)", "")
        # The rest are (displayName, folderName) pairs from metadata.json.
        names = [display for display, _ in choices]
        assert "Contacts" in names  # displayName for the contact sample

    def test_choices_include_folder_name_mapping(self):
        mapping = dict(_template_choices())
        assert mapping["Contacts"] == "contact"

    def test_choices_fallback_when_metadata_missing(self, tmp_path, monkeypatch):
        # Point _samples_dir at an empty dir: no metadata.json, no samples.
        monkeypatch.setattr(init_mod, "_samples_dir", lambda: tmp_path)
        choices = _template_choices()
        # Only the Blank sentinel survives when there's no metadata/samples.
        assert choices == [("(Blank project)", "")]

    def test_choices_fallback_on_malformed_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setattr(init_mod, "_samples_dir", lambda: tmp_path)
        (tmp_path / "metadata.json").write_text("{ not valid json", encoding="utf-8")
        choices = _template_choices()
        assert choices[0] == ("(Blank project)", "")


class TestPickTemplate:
    """stdin-driven picker; ``input`` is patched so no real interaction occurs."""

    def test_blank_selection_returns_empty_string(self):
        with patch("builtins.input", return_value="1"):
            assert _pick_template() == ""

    def test_cancel_returns_none(self):
        with patch("builtins.input", return_value=""):
            assert _pick_template() is None

    def test_selecting_a_sample_returns_its_folder(self):
        # Index 2 is the first real sample (after Blank at index 1).
        with patch("builtins.input", return_value="2"):
            assert _pick_template() == "contact"

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

    def test_positional_wins_over_flag_when_equal(self, tmp_path):
        from optics_framework.helper.cli import InitCommand

        with patch(f"{CLI}.create_project") as create:
            InitCommand().execute(self._make_args(positional="x", flag="x",
                                                  path=str(tmp_path)))
        create.assert_called_once()
        assert create.call_args.args[0].name == "x"

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

    def test_interactive_prompt_when_tty(self, tmp_path):
        from optics_framework.helper.cli import InitCommand

        args = self._make_args(path=str(tmp_path))
        with patch(f"{CLI}.create_project") as create, \
                patch("builtins.input", return_value="talked"), \
                patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            InitCommand().execute(args)
        assert create.call_args.args[0].name == "talked"

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

    def test_piped_stdin_supplies_project_name(self, tmp_path):
        # Regression pin: `printf 'demo\n' | optics init` must read the name
        # from the pipe instead of failing with exit 3.
        from optics_framework.helper.cli import InitCommand

        with patch(f"{CLI}.create_project") as create, \
                patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            stdin.readline.return_value = "demo\n"
            InitCommand().execute(self._make_args(path=str(tmp_path)))
        assert create.call_args.args[0].name == "demo"

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
