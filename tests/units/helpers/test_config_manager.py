"""Unit tests for the project-scoped config editor (``config_manager.configure``).

The old global-file editor is gone; ``configure`` now reads/writes a project's
own ``config.yaml``. These tests pin:

- the beginner path (prompt → render → write → next steps), including the
  confirm-before-overwrite gate,
- ``--edit`` launching the Textual editor,
- the deprecated ``optics config`` alias forwarding to the project editor.

Collaborators from the onboarding package (``project_config``, ``onboarding``)
are patched at their source modules — ``configure`` imports them lazily inside
the function body.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from optics_framework.helper import config_manager

MODULE = "optics_framework.helper.config_manager"
PROJECT_CONFIG = "optics_framework.helper.project_config"
ONBOARDING = "optics_framework.helper.onboarding"
CLI = "optics_framework.helper.cli"


@pytest.fixture
def configured_helpers():
    """Patch the onboarding-package collaborators configure() lazily imports."""
    with patch(f"{PROJECT_CONFIG}.prompt_project_config",
               return_value={"driver": "appium"}) as prompt, \
            patch(f"{PROJECT_CONFIG}.render_project_config",
                  return_value="rendered: yaml\n") as render, \
            patch(f"{PROJECT_CONFIG}.write_project_config") as write, \
            patch(f"{ONBOARDING}.print_next_steps") as next_steps:
        yield SimpleNamespace(
            prompt=prompt, render=render, write=write, next_steps=next_steps)


@pytest.mark.white_box
class TestConfigureOverwriteConfirm:
    def test_writes_config_when_no_existing_file(self, tmp_path, capsys,
                                                 configured_helpers):
        folder = str(tmp_path)
        config_manager.configure(folder=folder, edit=False)

        configured_helpers.prompt.assert_called_once()
        configured_helpers.write.assert_called_once_with(folder, "rendered: yaml\n")
        configured_helpers.next_steps.assert_called_once_with(folder, configured=True)
        assert "Wrote" in capsys.readouterr().out

    def test_confirms_before_overwriting_existing_file(self, tmp_path,
                                                       configured_helpers):
        folder = str(tmp_path)
        (tmp_path / "config.yaml").write_text("old: true\n", encoding="utf-8")

        with patch(f"{MODULE}.Confirm.ask", return_value=True) as ask:
            config_manager.configure(folder=folder, edit=False)

        ask.assert_called_once()
        configured_helpers.write.assert_called_once_with(folder, "rendered: yaml\n")

    def test_aborts_without_writing_when_overwrite_declined(self, tmp_path,
                                                            capsys,
                                                            configured_helpers):
        folder = str(tmp_path)
        (tmp_path / "config.yaml").write_text("old: true\n", encoding="utf-8")

        with patch(f"{MODULE}.Confirm.ask", return_value=False):
            config_manager.configure(folder=folder, edit=False)

        configured_helpers.write.assert_not_called()
        configured_helpers.next_steps.assert_not_called()
        assert "Aborted" in capsys.readouterr().out

    def test_defaults_to_current_directory(self, monkeypatch, tmp_path,
                                           configured_helpers):
        monkeypatch.chdir(tmp_path)
        config_manager.configure()
        configured_helpers.write.assert_called_once_with(str(tmp_path),
                                                         "rendered: yaml\n")

    def test_edit_true_launches_project_tui(self, tmp_path):
        with patch(f"{MODULE}.ProjectConfigTUI") as tui_cls:
            config_manager.configure(folder=str(tmp_path), edit=True)
        tui_cls.assert_called_once_with(str(tmp_path))
        tui_cls.return_value.run.assert_called_once()


@pytest.mark.white_box
class TestOverwriteAskedBeforeQuestions:
    """The overwrite gate must come FIRST — a decline must waste no answers
    (regression: every Q&A used to be asked, THEN discarded on decline)."""

    def test_decline_never_prompts_any_question(self, tmp_path,
                                                configured_helpers):
        folder = str(tmp_path)
        (tmp_path / "config.yaml").write_text("old: true\n", encoding="utf-8")

        with patch(f"{MODULE}.Confirm.ask", return_value=False):
            config_manager.configure(folder=folder, edit=False)

        configured_helpers.prompt.assert_not_called()

    def test_confirm_fires_before_the_question_block(self, tmp_path,
                                                     configured_helpers):
        folder = str(tmp_path)
        order = []

        def record_confirm(*_a, **_kw):
            order.append("confirm")
            return True

        configured_helpers.prompt.side_effect = (
            lambda *_a, **_kw: order.append("prompt") or {"driver": "appium"})
        (tmp_path / "config.yaml").write_text("old: true\n", encoding="utf-8")

        with patch(f"{MODULE}.Confirm.ask", side_effect=record_confirm):
            config_manager.configure(folder=folder, edit=False)

        assert order == ["confirm", "prompt"]

    def test_no_existing_file_skips_confirm_entirely(self, tmp_path,
                                                     configured_helpers):
        with patch(f"{MODULE}.Confirm.ask") as ask:
            config_manager.configure(folder=str(tmp_path), edit=False)
        ask.assert_not_called()

    def test_defaults_to_current_directory(self, monkeypatch, tmp_path,
                                           configured_helpers):
        monkeypatch.chdir(tmp_path)
        config_manager.configure()
        configured_helpers.write.assert_called_once_with(str(tmp_path),
                                                         "rendered: yaml\n")

    def test_edit_true_launches_project_tui(self, tmp_path):
        with patch(f"{MODULE}.ProjectConfigTUI") as tui_cls:
            config_manager.configure(folder=str(tmp_path), edit=True)
        tui_cls.assert_called_once_with(str(tmp_path))
        tui_cls.return_value.run.assert_called_once()


@pytest.mark.white_box
class TestConfigAliasDeprecation:
    """``optics config`` now forwards to the guided project configuration.

    cli.py binds ``configure`` at import time (``configure_project``), so the
    patch target is cli's binding, not config_manager's."""

    def test_prints_deprecation_and_forwards_to_guided_config(self, capsys):
        from optics_framework.helper.cli import ConfigCommand

        with patch(f"{CLI}.configure_project") as configure:
            ConfigCommand().execute(SimpleNamespace())
        out = capsys.readouterr().out
        assert "deprecated" in out.lower()
        assert "configure" in out
        configure.assert_called_once()
        _, kwargs = configure.call_args
        assert kwargs.get("edit") is False
        assert kwargs.get("folder") == os.getcwd()
