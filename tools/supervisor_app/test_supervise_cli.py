#!/usr/bin/env python3
"""
Tests for the `optics supervise` CLI subcommand: flag -> env -> default
resolution and registration alongside `optics serve`.

Run with: python -m pytest test_supervise_cli.py -v
"""

import argparse
import os
from unittest.mock import patch

import pytest

from optics_framework.helper import cli

SUPERVISOR_ENV_VARS = (
    "SUPERVISOR_STORE",
    "SUPERVISOR_REDIS_URL",
    "SUPERVISOR_WORKER_MODE",
    "SUPERVISOR_LAUNCHER",
    "SUPERVISOR_MAX_SESSIONS",
)


def _parse(argv):
    parser = argparse.ArgumentParser(prog="optics")
    subparsers = parser.add_subparsers(dest="command")
    cli.SupervisorCommand().register(subparsers)
    return parser.parse_args(argv)


@pytest.fixture(autouse=True)
def clean_supervisor_env():
    """Snapshot/restore the env: the command under test writes os.environ
    directly, so plain monkeypatch would leak flags into later tests."""
    saved = {var: os.environ.pop(var, None) for var in SUPERVISOR_ENV_VARS}
    yield
    for var, value in saved.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value


class TestFlagResolution:
    @patch("optics_framework.helper.supervisor.supervisor_tool.run_supervisor")
    def test_defaults_match_original_supervisor(self, mock_run, monkeypatch):
        """`optics supervise` with no flags == `python supervisor_tool.py --workers 2`:
        no env var is touched, so every SUPERVISOR_* default applies."""
        args = _parse(["supervise"])
        args.func(args)

        config = mock_run.call_args.args[0]
        assert config.num_workers == 2
        assert config.base_port == 9000
        assert config.host == "127.0.0.1"
        assert config.port == 8000
        assert "SUPERVISOR_STORE" not in os.environ
        assert "SUPERVISOR_WORKER_MODE" not in os.environ
        assert "SUPERVISOR_LAUNCHER" not in os.environ
        assert "SUPERVISOR_MAX_SESSIONS" not in os.environ

    @patch("optics_framework.helper.supervisor.supervisor_tool.run_supervisor")
    def test_flags_are_written_to_env(self, mock_run):
        args = _parse([
            "supervise",
            "--store", "redis",
            "--redis-url", "redis://redis.internal:6379/0",
            "--worker-mode", "per_session",
            "--launcher", "docker",
            "--max-sessions", "8",
            "--port", "9999",
        ])
        args.func(args)

        assert os.environ["SUPERVISOR_STORE"] == "redis"
        assert os.environ["SUPERVISOR_REDIS_URL"] == "redis://redis.internal:6379/0"
        assert os.environ["SUPERVISOR_WORKER_MODE"] == "per_session"
        assert os.environ["SUPERVISOR_LAUNCHER"] == "docker"
        assert os.environ["SUPERVISOR_MAX_SESSIONS"] == "8"
        assert mock_run.call_args.args[0].port == 9999

    @patch("optics_framework.helper.supervisor.supervisor_tool.run_supervisor")
    def test_flag_overrides_env(self, mock_run, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_STORE", "memory")
        args = _parse(["supervise", "--store", "redis"])
        args.func(args)

        assert os.environ["SUPERVISOR_STORE"] == "redis"

    @patch("optics_framework.helper.supervisor.supervisor_tool.run_supervisor")
    def test_env_survives_when_flag_absent(self, mock_run, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_WORKER_MODE", "per_session")
        args = _parse(["supervise"])
        args.func(args)

        assert os.environ["SUPERVISOR_WORKER_MODE"] == "per_session"

    def test_invalid_choice_rejected_by_parser(self):
        with pytest.raises(SystemExit):
            _parse(["supervise", "--store", "sqlite"])


class TestRegistration:
    def test_supervise_help(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["optics", "supervise", "--help"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        for flag in ("--store", "--redis-url", "--worker-mode", "--launcher", "--max-sessions"):
            assert flag in out

    def test_serve_still_registered_and_unchanged(self, monkeypatch, capsys):
        """`optics serve` keeps its exact surface (host/port/workers only)."""
        monkeypatch.setattr("sys.argv", ["optics", "serve", "--help"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "--host" in out and "--port" in out and "--workers" in out
        assert "--store" not in out and "--worker-mode" not in out


if __name__ == "__main__":
    pytest.main([__file__])
