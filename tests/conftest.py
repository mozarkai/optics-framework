"""Root test configuration shared across the whole suite.

Three responsibilities live here:

1. **Marker auto-application** — every test under ``tests/units/`` is tagged
   ``white_box`` unless it already carries an explicit box marker, so
   ``pytest -m white_box`` reliably selects the full hermetic unit suite instead
   of the handful of files that happened to declare the marker by hand.
2. **The in-process mock API server** — a session-scoped, ephemeral-port fixture
   consumed by the API-invocation tests (kept hermetic; no external network).
3. **Cross-platform test isolation** — redirecting the home directory, and
   giving prompt_toolkit somewhere to render that is not the real console.
"""
from __future__ import annotations

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

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


def set_home(monkeypatch, path) -> None:
    """Point the user's home directory at ``path`` on every platform.

    ``os.path.expanduser`` ignores HOME on Windows — it reads USERPROFILE, then
    HOMEDRIVE/HOMEPATH — so patching HOME alone leaves the runner's real home in
    play and tests silently assert against it."""
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))


@pytest.fixture
def pt_app_session():
    """Run a test inside a prompt_toolkit session bound to a dummy terminal.

    Constructing an ``Application`` resolves an output at once, and on Windows
    with no attached console that raises ``NoConsoleScreenBufferError``. POSIX
    happens to fall back to a vt100 writer, which is why this only ever bit the
    Windows runner."""
    with create_pipe_input() as pipe_input:
        with create_app_session(input=pipe_input, output=DummyOutput()):
            yield


@pytest.fixture(scope="session")
def mock_api_server():
    """Start the in-process mock API on a free port for the whole session.

    Yields the base URL (``http://127.0.0.1:<port>``); tears the server down
    afterwards. Session scope keeps startup cost to a single bind per run.
    """
    server = start_server()
    try:
        yield server.base_url
    finally:
        server.stop()
