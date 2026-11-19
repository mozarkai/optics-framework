from __future__ import annotations

from unittest.mock import patch

import pytest

from optics_framework.common.runner.event_stream import OutputMode
from optics_framework.helper import abort, cli
from optics_framework.helper.environment import EnvKind, Environment


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


class TestExitCodeMapping:


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


def _env(kind, *, manager=None, has_pip=True):
    return Environment(kind=kind, python="/py", prefix="/prefix",
                       has_pip=has_pip, writable=True, manager=manager,
                       uv="/usr/bin/uv")


class TestReinstallGuidance:
    @pytest.mark.parametrize("env, expected", [
        (_env(EnvKind.VENV), "pip install --force-reinstall"),
        (_env(EnvKind.VENV, has_pip=False), "uv pip install"),
        (_env(EnvKind.TOOL, manager="pipx", has_pip=False), "pipx reinstall"),
        (_env(EnvKind.TOOL, manager="uv", has_pip=False), "uv tool install --reinstall"),
        (_env(EnvKind.PROJECT, manager="poetry"), "poetry add"),
    ])
    def test_command_matches_the_environment(self, monkeypatch, env, expected):
        monkeypatch.setattr(abort, "detect", lambda: env)
        assert abort.reinstall_guidance()[-1].strip().startswith(expected)


class TestStartupImportGuard:
    def test_failure_shows_the_real_error_and_how_to_recover(self, capsys):
        error = ImportError("No module named 'pydantic'")
        with pytest.raises(SystemExit) as exc:
            cli._abort_on_import_error(error)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "pydantic" in err
        assert "Reinstall the package" in err
        assert "optics-framework" in err


class TestCommandForwarding:
    def test_configure_forwards_folder_and_edit_flag(self):
        with patch.object(cli, "configure_project") as configure:
            _run(["configure", "myproj"])
        configure.assert_called_once_with(folder="myproj", edit=False)

    @pytest.mark.parametrize(
        ("flags", "expected"),
        [
            ([], OutputMode.AUTO),
            (["--output", "plain"], OutputMode.PLAIN),
            (["--output", "jsonl"], OutputMode.JSONL),
            (["--use-printer"], OutputMode.TREE),
            (["--no-use-printer"], OutputMode.NONE),
        ],
    )
    @pytest.mark.parametrize(("command", "entry"), [("execute", "execute_main"), ("dry_run", "dryrun_main")])
    def test_execute_and_dry_run_forward_the_output_mode(self, command, entry, flags, expected):
        with patch.object(cli, entry) as run:
            _run([command, "myproj", *flags])
        assert run.call_args.kwargs["output"] is expected
