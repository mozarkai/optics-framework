"""Root test configuration shared across the whole suite.

Two responsibilities live here:

1. **Marker auto-application** — every test under ``tests/units/`` is tagged
   ``white_box`` unless it already carries an explicit box marker, so
   ``pytest -m white_box`` reliably selects the full hermetic unit suite instead
   of the handful of files that happened to declare the marker by hand.
2. **The in-process mock API server** — a session-scoped, ephemeral-port fixture
   consumed by the API-invocation tests (kept hermetic; no external network).
"""
from __future__ import annotations

import pytest

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
