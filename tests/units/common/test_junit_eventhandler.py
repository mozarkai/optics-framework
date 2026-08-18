"""Unit tests for ``JUnitEventHandler.close`` idempotency (Phase 0, finding B3).

``SessionManager.terminate_session`` calls ``cleanup_junit(session_id)`` (which
closes the ``JUnitEventHandler`` and evicts it from ``JUnitHandlerRegistry``)
and then ``EventManagerRegistry.remove_session`` (which now calls
``EventManager.shutdown()``, which in turn calls ``close()`` on every
subscriber still registered on the manager -- including the JUnit handler,
since ``cleanup_junit`` never unsubscribes it from the EventManager). So the
handler's ``close()`` is called twice on every session teardown that has JUnit
logging enabled. It must be safe to call twice.

Source under test: optics_framework/common/Junit_eventhandler.py
"""
from unittest.mock import patch

import pytest

from optics_framework.common.Junit_eventhandler import JUnitEventHandler

pytestmark = pytest.mark.white_box


def test_close_is_idempotent(tmp_path):
    """A second close() must not re-flush the XML to disk."""
    handler = JUnitEventHandler(tmp_path / "junit_output.xml")

    with patch.object(handler, "flush") as mock_flush:
        handler.close()
        handler.close()

        mock_flush.assert_called_once()


def test_close_writes_file_once_on_repeated_calls(tmp_path):
    """End-to-end: the XML file is written by the first close(); the second
    close() is a true no-op (no re-write, no exception)."""
    output_path = tmp_path / "junit_output.xml"
    handler = JUnitEventHandler(output_path)

    handler.close()
    assert output_path.exists()
    first_mtime = output_path.stat().st_mtime_ns

    handler.close()

    assert output_path.stat().st_mtime_ns == first_mtime
