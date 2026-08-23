"""Unit tests for shell-autocompletion installation (``autocompletion.py``).

The completion scripts under ``~/.optics`` must be generated unconditionally,
but the rc-file mutation is consent-gated: asked only on an interactive
stdin, and a decline — or a non-TTY stdin, which cannot answer — leaves the
rc file untouched and prints the manual ``source`` line instead.
"""
from __future__ import annotations

import contextlib
import io
from unittest.mock import patch

import pytest

from optics_framework.helper import autocompletion

MODULE = "optics_framework.helper.autocompletion"

pytestmark = pytest.mark.white_box


class FakeStdin:
    """stdin stand-in with a switchable TTY flag."""

    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return tmp_path


def _run(home, *, shell="zsh", tty=True, confirm=True):
    """Run update_shell_rc in isolation; returns (stdout, confirm_asked)."""
    out = io.StringIO()
    with patch(f"{MODULE}.sys.stdin", FakeStdin(tty)), \
            patch(f"{MODULE}.Confirm.ask", return_value=confirm) as ask:
        with contextlib.redirect_stdout(out):
            autocompletion.update_shell_rc(shell=shell)
    return out.getvalue(), ask.called


def _rc_path(home, shell):
    return home / (".zshrc" if "zsh" in shell else ".bashrc")


@pytest.mark.parametrize("shell, rc_name", [("zsh", ".zshrc"), ("bash", ".bashrc")])
class TestConsentGate:
    def test_accepted_on_tty_appends_source_line(self, home, shell, rc_name):
        out, asked = _run(home, shell=shell, tty=True, confirm=True)
        rc = _rc_path(home, shell)
        content = rc.read_text(encoding="utf-8")
        assert "Optics CLI autocompletion" in content
        assert f"source {home / '.optics'}/optics_completion." in content
        assert "Added autocompletion" in out
        assert asked

    def test_declined_on_tty_touches_nothing_and_prints_manual_line(
            self, home, shell, rc_name):
        out, asked = _run(home, shell=shell, tty=True, confirm=False)
        assert not _rc_path(home, shell).exists()
        assert asked
        expected = f"source {home / '.optics'}"
        assert expected in out
        assert any(line.strip().startswith("source ")
                   for line in out.splitlines())

    def test_non_tty_never_asks_or_writes(self, home, shell, rc_name):
        out, asked = _run(home, shell=shell, tty=False)
        assert not _rc_path(home, shell).exists()
        assert not asked
        assert "source" in out


class TestScriptsAlwaysGenerated:
    @pytest.mark.parametrize("tty, confirm", [(True, True), (True, False),
                                              (False, False)])
    def test_completion_scripts_written_regardless_of_consent(
            self, home, tty, confirm):
        _run(home, tty=tty, confirm=confirm)
        optics_dir = home / ".optics"
        assert (optics_dir / "optics_completion.zsh").exists()
        assert (optics_dir / "optics_completion.sh").exists()


class TestDuplicateDetection:
    def test_already_enabled_skips_the_question(self, home):
        source = (home / ".optics" / "optics_completion.zsh")
        autocompletion.write_completion_scripts()
        rc = home / ".zshrc"
        rc.write_text(f"source {source}\n", encoding="utf-8")
        out, asked = _run(home, shell="zsh", tty=True, confirm=True)
        assert not asked
        assert "already enabled" in out
        assert rc.read_text(encoding="utf-8").count("source ") == 1


class TestUnsupportedShell:
    def test_unknown_shell_reports_without_writing(self, home):
        out, asked = _run(home, shell="fish", tty=True, confirm=True)
        assert "Unsupported shell" in out
        assert asked is False  # never reached the consent question
        assert not (home / ".zshrc").exists()
        assert not (home / ".bashrc").exists()
