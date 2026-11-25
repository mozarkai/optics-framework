from __future__ import annotations

import io
import sys

import pytest

from optics_framework.helper import autocompletion


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return tmp_path


def _run(capsys, shell):
    """Run update_shell_rc; returns (stdout, whether it asked to edit the shell's rc file)."""
    autocompletion.update_shell_rc(shell=shell)
    out = capsys.readouterr().out
    return out, "Add autocompletion to" in out


def _rc_path(home, shell):
    return home / (".zshrc" if "zsh" in shell else ".bashrc")


@pytest.mark.parametrize("shell, rc_name", [("zsh", ".zshrc"), ("bash", ".bashrc")])
class TestConsentGate:
    def test_non_tty_never_asks_or_writes(self, home, shell, rc_name, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
        out, asked = _run(capsys, shell)
        assert not _rc_path(home, shell).exists()
        assert not asked
        assert "source" in out


class TestDuplicateDetection:
    def test_already_enabled_skips_the_question(self, home, typed, capsys):
        source = (home / ".optics" / "optics_completion.zsh")
        autocompletion.write_completion_scripts()
        rc = home / ".zshrc"
        rc.write_text(f"source {source}\n", encoding="utf-8")
        typed("y")
        out, asked = _run(capsys, "zsh")
        assert not asked
        assert sys.stdin.read() == "y\n"
        assert "already enabled" in out
        assert rc.read_text(encoding="utf-8").count("source ") == 1
