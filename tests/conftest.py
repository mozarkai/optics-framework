"""Root test configuration shared across the whole suite."""
from __future__ import annotations

import subprocess  # nosec B404
import sys

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from optics_framework.common import logging_config
from optics_framework.helper.cli import main
from tests.mock_servers.single_server import start_server

_BOX_MARKERS = {"white_box", "black_box", "hybrid"}


def pytest_collection_modifyitems(config, items):
    """Tag unmarked unit tests ``white_box`` based on their location."""
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "/tests/units/" not in path:
            continue
        if not any(m.name in _BOX_MARKERS for m in item.iter_markers()):
            item.add_marker(pytest.mark.white_box)


@pytest.fixture
def cli(monkeypatch, capfd):
    """Run the optics CLI in this process, as a shell would: arguments in, exit code and output back."""
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


@pytest.fixture(autouse=True)
def _optics_home(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("OPTICS_HOME", str(tmp_path_factory.mktemp("optics_home")))
    monkeypatch.setenv("OPTICS_NO_UPDATE_CHECK", "1")


@pytest.fixture(autouse=True)
def _no_secrets_from_other_tests(monkeypatch):
    for name in ("_config_secrets", "_unscoped_secrets", "_process_order"):
        monkeypatch.setattr(logging_config, name, getattr(logging_config, name))
    logging_config._active_secrets.set(None)


def set_home(monkeypatch, path) -> None:
    """Point the user's home directory at ``path`` on every platform.

    ``os.path.expanduser`` ignores HOME on Windows — it reads USERPROFILE, then
    HOMEDRIVE/HOMEPATH — so patching HOME alone leaves the runner's real home in
    play and tests silently assert against it."""
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))


@pytest.fixture
def pt_app_session():
    with create_pipe_input() as pipe_input:
        with create_app_session(input=pipe_input, output=DummyOutput()):
            yield


@pytest.fixture(scope="session")
def mock_api_server():
    server = start_server()
    try:
        yield server.base_url
    finally:
        server.stop()
