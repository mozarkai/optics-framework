from __future__ import annotations

import contextlib
import io
from unittest.mock import patch

import pytest

from optics_framework.helper import autocompletion

MODULE = "optics_framework.helper.autocompletion"


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
    def test_non_tty_never_asks_or_writes(self, home, shell, rc_name):
        out, asked = _run(home, shell=shell, tty=False)
        assert not _rc_path(home, shell).exists()
        assert not asked
        assert "source" in out


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
