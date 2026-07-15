#!/usr/bin/env python3
"""
Unit tests for supervisor_tool.py

Run with: python -m pytest test_supervisor_tool.py -v
"""

import json
import signal
import subprocess
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import supervisor_tool as st

# Create a test app without lifespan
test_app = FastAPI(title="Optics Supervisor Test")
test_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and add routes to test app
test_app.get("/health")(st.health_check)
test_app.post("/v1/sessions/start")(st.create_session)
test_app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])(st.forward_to_worker)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset supervisor global state before each test."""
    st.workers.clear()
    st.session_map.clear()
    st.worker_index = 0
    yield
    st.workers.clear()
    st.session_map.clear()
    st.worker_index = 0


class TestSupervisorConfig:
    """Test SupervisorConfig class."""

    def test_default_config(self):
        """Test default configuration values."""
        config = st.SupervisorConfig()
        assert config.num_workers == 2
        assert config.base_port == 9000
        assert config.host == "127.0.0.1"
        assert config.port == 8000

    def test_custom_config(self):
        """Test custom configuration values."""
        config = st.SupervisorConfig(num_workers=4, base_port=8000, host="0.0.0.0", port=8080)
        assert config.num_workers == 4
        assert config.base_port == 8000
        assert config.host == "0.0.0.0"
        assert config.port == 8080

    def test_module_level_default_config(self):
        """Importing the module must always provide a usable config (regression:
        it used to be assigned only under __main__)."""
        assert isinstance(st.config, st.SupervisorConfig)


class TestWorkerManagement:
    """Test worker management functions."""

    @patch("supervisor_tool.time.sleep")
    @patch("supervisor_tool.start_worker_process")
    def test_start_workers(self, mock_start, _mock_sleep):
        """Test starting worker processes."""
        st.config.num_workers = 2
        st.config.base_port = 9000
        mock_start.side_effect = lambda port, log_path=None: (Mock(), None)

        st.start_workers()

        assert len(st.workers) == 2
        assert st.workers[0]["port"] == 9000
        assert st.workers[1]["port"] == 9001
        assert all(w["active"] for w in st.workers)

    @patch("supervisor_tool.time.sleep")
    @patch("supervisor_tool.start_worker_process")
    def test_start_workers_partial_failure(self, mock_start, _mock_sleep):
        """A worker that fails to start is not added to the pool."""
        st.config.num_workers = 2
        st.config.base_port = 9000
        mock_start.side_effect = [(Mock(), None), (None, None)]

        st.start_workers()

        assert len(st.workers) == 1
        assert st.workers[0]["port"] == 9000

    @patch("supervisor_tool._kill_worker")
    def test_stop_workers(self, mock_kill):
        """Test stopping worker processes clears state and tears down each worker."""
        st.workers.append({"port": 9000, "process": Mock(), "active": True})
        st.workers.append({"port": 9001, "process": Mock(), "active": True})
        st.session_map["some-session"] = 9000

        st.stop_workers()

        assert mock_kill.call_count == 2
        assert len(st.workers) == 0
        assert len(st.session_map) == 0

    @patch("supervisor_tool._kill_orphans_on_port")
    def test_kill_worker_graceful(self, mock_orphans):
        """_kill_worker SIGTERMs the process and closes the log handle."""
        proc = Mock(spec=subprocess.Popen)
        proc.poll.return_value = None
        proc.pid = 99999
        log_fh = Mock()
        worker = {"port": 9000, "process": proc, "active": True, "log_fh": log_fh}

        with patch("supervisor_tool.os.getpgid", side_effect=ProcessLookupError):
            st._kill_worker(worker)

        proc.send_signal.assert_called_with(signal.SIGTERM)
        proc.wait.assert_called()
        log_fh.close.assert_called_once()
        mock_orphans.assert_called_once_with(9000)

    def test_get_next_worker_port_round_robin(self):
        """Test round-robin worker selection."""
        st.workers.append({"port": 9000, "active": True})
        st.workers.append({"port": 9001, "active": True})
        st.workers.append({"port": 9002, "active": False})  # Inactive worker

        assert st.get_next_worker_port() == 9000
        assert st.get_next_worker_port() == 9001
        assert st.get_next_worker_port() == 9000

    def test_get_next_worker_port_no_active(self):
        """Test behavior when no workers are active."""
        st.workers.append({"port": 9000, "active": False})

        assert st.get_next_worker_port() is None


class TestWorkerRestart:
    """Test crash detection and restart handling."""

    def _crashed_worker(self):
        dead_proc = Mock(spec=subprocess.Popen)
        dead_proc.poll.return_value = 1  # crashed
        return {
            "port": 9000,
            "process": dead_proc,
            "active": True,
            "log_path": "./worker_9000.log",
            "log_fh": Mock(),
        }

    def test_crash_cleans_up_sessions(self):
        """Sessions mapped to a crashed worker are removed."""
        st.workers.append(self._crashed_worker())
        st.session_map["dead-session"] = 9000
        st.session_map["other-session"] = 9001

        st.check_worker_health()

        assert st.workers[0]["active"] is False
        assert "dead-session" not in st.session_map
        assert "other-session" in st.session_map

    @patch("supervisor_tool.time.sleep")
    @patch("supervisor_tool.start_worker_process")
    def test_restart_stores_unpacked_process(self, mock_start, _mock_sleep):
        """Regression: the restart path must unpack (process, log_fh) — it used to
        store the whole tuple as the process handle."""
        worker = self._crashed_worker()
        st.workers.append(worker)
        new_proc = Mock(spec=subprocess.Popen)
        new_fh = Mock()
        mock_start.return_value = (new_proc, new_fh)

        with patch.object(st, "RESTART_WORKERS", True):
            st.check_worker_health()

        mock_start.assert_called_once_with(9000, "./worker_9000.log")
        assert worker["process"] is new_proc
        assert worker["log_fh"] is new_fh
        assert worker["active"] is True


class TestSessionMapping:
    """Test session mapping functions."""

    @pytest.fixture(autouse=True)
    def two_workers(self):
        st.workers.append({"port": 9000, "active": True})
        st.workers.append({"port": 9001, "active": True})

    def test_select_worker_for_existing_session(self):
        """Test selecting worker for existing session."""
        st.session_map["test-session-123"] = 9001

        assert st.select_worker_for_session("test-session-123") == 9001

    def test_select_worker_for_new_session(self):
        """Test selecting worker for new session."""
        port = st.select_worker_for_session("new-session-456")
        assert port in [9000, 9001]
        assert st.session_map["new-session-456"] == port

    def test_select_worker_no_workers(self):
        """Test behavior when no workers are available."""
        st.workers.clear()

        assert st.select_worker_for_session("test-session") is None


class TestPathParsing:
    """Test URL path parsing functions."""

    def test_extract_session_id_from_path_valid(self):
        """Test extracting session ID from valid paths."""
        test_cases = [
            ("/v1/sessions/12345678-1234-5678-9012-123456789012/action", "12345678-1234-5678-9012-123456789012"),
            ("/v1/session/12345678-1234-5678-9012-123456789012/screenshot", "12345678-1234-5678-9012-123456789012"),
            ("/v1/sessions/12345678-1234-5678-9012-123456789012/events", "12345678-1234-5678-9012-123456789012"),
        ]

        for path, expected in test_cases:
            assert st.extract_session_id_from_path(path) == expected

    def test_extract_session_id_from_path_invalid(self):
        """Test extracting session ID from invalid paths."""
        test_cases = [
            "/v1/sessions/start",
            "/v1/keywords",
            "/health",
            "/v1/sessions/not-a-uuid/action",
            "/v1/session/123/action",  # Too short
        ]

        for path in test_cases:
            assert st.extract_session_id_from_path(path) is None


class TestAPIEndpoints:
    """Test API endpoints."""

    def test_health_endpoint(self):
        """Test health check endpoint."""
        with TestClient(test_app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert "status" in data
            assert "active_workers" in data
            assert "total_sessions" in data

    @patch("supervisor_tool.forward_request")
    def test_create_session_endpoint(self, mock_forward):
        """Test session creation endpoint."""
        st.workers.append({"port": 9000, "active": True})

        # Mock successful response with session_id
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = json.dumps({"session_id": "test-session-123"}).encode()
        mock_response.headers = {"content-type": "application/json"}
        mock_forward.return_value = mock_response

        with TestClient(test_app) as client:
            response = client.post("/v1/sessions/start", json={"driver_sources": []})

            assert response.status_code == 200
            assert st.session_map["test-session-123"] == 9000

    def test_create_session_no_workers(self):
        """Session creation without workers returns 503."""
        with TestClient(test_app) as client:
            response = client.post("/v1/sessions/start", json={"driver_sources": []})
            assert response.status_code == 503

    @patch("supervisor_tool.forward_request")
    def test_create_session_failure_does_not_map(self, mock_forward):
        """A non-200 from the worker must not create a route."""
        st.workers.append({"port": 9000, "active": True})

        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.content = b'{"detail": "boom"}'
        mock_response.headers = {"content-type": "application/json"}
        mock_forward.return_value = mock_response

        with TestClient(test_app) as client:
            response = client.post("/v1/sessions/start", json={"driver_sources": []})

            assert response.status_code == 500
            assert len(st.session_map) == 0

    @patch("supervisor_tool.forward_request")
    def test_forward_to_worker_with_session(self, mock_forward):
        """Test forwarding request with session ID."""
        session_id = "12345678-1234-5678-9012-123456789012"
        st.workers.append({"port": 9000, "active": True})
        st.session_map[session_id] = 9000

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"OK"
        mock_response.headers = {"content-type": "text/plain"}
        mock_forward.return_value = mock_response

        with TestClient(test_app) as client:
            response = client.post(f"/v1/sessions/{session_id}/action", json={"keyword": "test"})

            assert response.status_code == 200
            mock_forward.assert_called_once()
            forwarded_url = mock_forward.call_args.kwargs.get("url") or mock_forward.call_args.args[1]
            assert forwarded_url.startswith("http://127.0.0.1:9000/")

    @patch("supervisor_tool.forward_request")
    def test_forward_to_worker_no_session(self, mock_forward):
        """Test forwarding request without session ID."""
        st.workers.append({"port": 9000, "active": True})

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"OK"
        mock_response.headers = {"content-type": "text/plain"}
        mock_forward.return_value = mock_response

        with TestClient(test_app) as client:
            response = client.get("/v1/keywords")

            assert response.status_code == 200
            mock_forward.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__])
