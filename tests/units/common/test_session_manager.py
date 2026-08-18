"""Unit tests for ``optics_framework.common.session_manager.SessionManager``.

Hermetic and device-less: sessions are injected directly into
``SessionManager.sessions`` as lightweight stand-ins (``MagicMock``s with
just the attributes ``terminate_session`` touches) rather than built via
``Session.__init__``, which requires a real driver/config. No driver,
engine, or Appium/Selenium session is ever instantiated.

Source under test: optics_framework/common/session_manager.py
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from optics_framework.common import session_manager as session_manager_module
from optics_framework.common.session_manager import SessionManager


def _make_bare_session(driver=None):
    """Minimal stand-in for ``Session`` carrying only the attributes
    ``SessionManager.terminate_session`` reads/mutates."""
    session = MagicMock()
    session.driver = driver
    session.inline_templates = {"btn": "/tmp/btn.png"}
    session._inline_templates_dir = tempfile.mkdtemp(prefix="optics_test_session_")
    return session


def test_terminate_session_cleans_up_even_when_driver_terminate_fails():
    """G1 follow-up: a driver that refuses to quit must not leak the inline
    templates temp dir or the event-manager registry entry, and the caller
    (expose_api.delete_session) must still see the failure."""
    mgr = SessionManager()
    session_id = "sess-broken"

    driver = MagicMock()
    driver.terminate.side_effect = RuntimeError("device rebooted")
    session = _make_bare_session(driver=driver)
    mgr.sessions[session_id] = session

    temp_dir = session._inline_templates_dir
    assert os.path.isdir(temp_dir)

    with patch.object(session_manager_module, "cleanup_junit") as mock_cleanup_junit, \
            patch.object(session_manager_module, "get_event_manager_registry") as mock_get_registry:
        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry

        with pytest.raises(RuntimeError, match="device rebooted"):
            mgr.terminate_session(session_id)

        # Driver teardown was attempted exactly once...
        driver.terminate.assert_called_once_with()
        # ...but every other cleanup step still ran despite that failure.
        assert session.inline_templates == {}
        assert not os.path.isdir(temp_dir)
        mock_cleanup_junit.assert_called_once_with(session_id)
        mock_registry.remove_session.assert_called_once_with(session_id)

    # The session is evicted from the manager regardless of the driver error.
    assert session_id not in mgr.sessions


def test_terminate_session_success_path_runs_all_cleanup_once():
    """Sanity check: a well-behaved driver still gets the same cleanup,
    exactly once, with no exception raised."""
    mgr = SessionManager()
    session_id = "sess-ok"

    driver = MagicMock()
    session = _make_bare_session(driver=driver)
    mgr.sessions[session_id] = session
    temp_dir = session._inline_templates_dir

    with patch.object(session_manager_module, "cleanup_junit") as mock_cleanup_junit, \
            patch.object(session_manager_module, "get_event_manager_registry") as mock_get_registry:
        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry

        mgr.terminate_session(session_id)

        driver.terminate.assert_called_once_with()
        assert session.inline_templates == {}
        assert not os.path.isdir(temp_dir)
        mock_cleanup_junit.assert_called_once_with(session_id)
        mock_registry.remove_session.assert_called_once_with(session_id)

    assert session_id not in mgr.sessions


def test_terminate_session_unknown_session_id_still_runs_registry_cleanup():
    """Even when the session_id is unknown, cleanup_junit/remove_session
    still run exactly once (pre-existing behavior, unaffected by hardening)."""
    mgr = SessionManager()
    session_id = "sess-does-not-exist"

    with patch.object(session_manager_module, "cleanup_junit") as mock_cleanup_junit, \
            patch.object(session_manager_module, "get_event_manager_registry") as mock_get_registry:
        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry

        mgr.terminate_session(session_id)

        mock_cleanup_junit.assert_called_once_with(session_id)
        mock_registry.remove_session.assert_called_once_with(session_id)
