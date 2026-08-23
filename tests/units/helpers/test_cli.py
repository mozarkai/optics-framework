"""Unit tests for the CLI dispatch layer (``optics_framework/helper/cli.py``).

Covers the argparse-level behaviours the per-command modules don't: the
bare-``optics`` welcome branch, ``main()``'s exception-to-exit-code mapping,
SystemExit propagation from ``DoctorCommand`` (which must bypass the generic
``except Exception`` handler so CI can gate on ``--check``), and the thin
forwarding wrappers (configure / quickstart / doctor). Command bodies are
patched at cli's import-time bindings; nothing real runs.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from optics_framework.helper import cli

pytestmark = pytest.mark.white_box


def _run(argv):
    """Run cli.main() against a fake argv."""
    with patch.object(cli.sys, "argv", ["optics", *argv]):
        cli.main()


class TestBareInvocation:
    def test_bare_optics_welcomes_and_marks_onboarded(self):
        with patch.object(cli, "welcome") as welcome, \
                patch.object(cli, "is_first_run", return_value=True), \
                patch.object(cli, "mark_onboarded") as mark:
            _run([])
        welcome.assert_called_once_with(first_run=True)
        mark.assert_called_once_with()

    def test_subcommand_never_welcomes(self):
        with patch.object(cli, "welcome") as welcome, \
                patch.object(cli, "mark_onboarded") as mark, \
                patch.object(cli, "run_quickstart"):
            _run(["quickstart"])
        welcome.assert_not_called()
        mark.assert_not_called()

    def test_returning_command_skips_welcome_branch(self):
        # A command that returns normally must fall through main() without
        # tripping the elif len(sys.argv) == 1 welcome branch.
        with patch.object(cli, "welcome") as welcome, \
                patch.object(cli, "run_doctor", return_value=0):
            with pytest.raises(SystemExit):
                _run(["doctor"])
        welcome.assert_not_called()

    def test_bare_optics_returning_user_gets_one_line_hint(self, capsys):
        # Returning users (is_first_run False) don't get the full panel; they
        # get a one-line hint and no onboarding marker write.
        with patch.object(cli, "welcome") as welcome, \
                patch.object(cli, "is_first_run", return_value=False), \
                patch.object(cli, "mark_onboarded") as mark:
            _run([])
        welcome.assert_not_called()
        mark.assert_not_called()
        out = capsys.readouterr().out
        assert "optics quickstart" in out
        assert "optics --help" in out


class TestExitCodeMapping:
    def test_doctor_check_failure_exits_nonzero_via_systemexit(self):
        # DoctorCommand raises SystemExit itself; it is not an Exception, so
        # main()'s generic handler must leave the code untouched.
        with patch.object(cli, "run_doctor", return_value=1):
            with pytest.raises(SystemExit) as exc:
                _run(["doctor", "--check"])
        assert exc.value.code == 1

    def test_keyboard_interrupt_maps_to_130(self):
        with patch.object(cli, "run_doctor", side_effect=KeyboardInterrupt):
            with pytest.raises(SystemExit) as exc:
                _run(["doctor"])
        assert exc.value.code == 130

    def test_value_error_maps_to_3(self):
        with patch.object(cli, "run_doctor", side_effect=ValueError("bad")):
            with pytest.raises(SystemExit) as exc:
                _run(["doctor"])
        assert exc.value.code == 3

    def test_value_error_message_reads_cleanly(self, capsys):
        with patch.object(cli, "run_doctor", side_effect=ValueError("bad")), \
                pytest.raises(SystemExit):
            _run(["doctor"])
        err = capsys.readouterr().err
        assert err.startswith("Error: bad")
        assert "Value error" not in err

    def test_unexpected_error_maps_to_1(self):
        with patch.object(cli, "run_doctor", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc:
                _run(["doctor"])
        assert exc.value.code == 1


class TestSetupInstallErrors:
    def test_invalid_engine_token_exits_nonzero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _run(["setup", "--install", "banana"])
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "Invalid engine(s): banana" in out
        assert "optics setup --list" in out

    def test_malformed_version_specifier_exits_nonzero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _run(["setup", "--install", "appium=4.2"])
        assert exc.value.code == 1
        assert "invalid version" in capsys.readouterr().out


class TestCommandForwarding:
    def test_doctor_forwards_folder_and_check(self):
        with patch.object(cli, "run_doctor", return_value=0) as run_doctor:
            with pytest.raises(SystemExit):
                _run(["doctor", "myproj", "--check"])
        run_doctor.assert_called_once_with(folder="myproj", check=True)

    def test_configure_forwards_folder_and_edit_flag(self):
        with patch.object(cli, "configure_project") as configure:
            _run(["configure", "myproj"])
        configure.assert_called_once_with(folder="myproj", edit=False)

    def test_configure_defaults_to_cwd_without_edit(self):
        with patch.object(cli, "configure_project") as configure:
            _run(["configure"])
        configure.assert_called_once_with(folder=None, edit=False)

    def test_quickstart_runs_the_wizard(self):
        with patch.object(cli, "run_quickstart") as quickstart:
            _run(["quickstart"])
        quickstart.assert_called_once_with()
