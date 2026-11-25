"""Run the optics CLI in this process, as a shell would: arguments in, exit code and output back."""
import subprocess  # nosec B404
import sys

import pytest

from optics_framework.helper.cli import main


@pytest.fixture
def cli(monkeypatch, capfd):
    def run(*args: str) -> subprocess.CompletedProcess:
        monkeypatch.setattr(sys, "argv", ["optics", *args])
        monkeypatch.setenv("OPTICS_NO_UPDATE_CHECK", "1")
        capfd.readouterr()
        try:
            main()
            code = 0
        except SystemExit as stop:
            code = stop.code if isinstance(stop.code, int) else int(stop.code is not None)
            if isinstance(stop.code, str):
                print(stop.code, file=sys.stderr)
        out = capfd.readouterr()
        return subprocess.CompletedProcess(["optics", *args], code, out.out, out.err)

    return run
