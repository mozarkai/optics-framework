#!/usr/bin/env python3
"""
Unit tests for supervisor_tool.py

Run with: python -m pytest test_supervisor_tool.py -v
"""

import json
from unittest.mock import Mock, patch
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from supervisor_tool import health_check, create_session, forward_to_worker

# Import functions to test
from supervisor_tool import (
    SupervisorConfig,
    start_workers,
    stop_workers,
    get_next_worker_port,
    select_worker_for_session,
    extract_session_id_from_path,
    session_map,
    workers,
    config
)

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
test_app.get("/health")(health_check)
test_app.post("/v1/sessions/start")(create_session)
test_app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])(forward_to_worker)


class TestSupervisorConfig:
    """Test SupervisorConfig class."""

    def test_default_config(self):
        """Test default configuration values."""
        config = SupervisorConfig()
        assert config.num_workers == 2
        assert config.base_port == 9000
        assert config.host == "127.0.0.1"
        assert config.port == 8000

    def test_custom_config(self):
        """Test custom configuration values."""
        config = SupervisorConfig(num_workers=4, base_port=8000, host="0.0.0.0", port=8080)
        assert config.num_workers == 4
        assert config.base_port == 8000
        assert config.host == "0.0.0.0"
        assert config.port == 8080


class TestWorkerManagement:
    """Test worker management functions."""

    def setup_method(self):
        """Reset global state before each test."""
        global workers, session_map, worker_index
        workers.clear()
        session_map.clear()
        worker_index = 0

    @patch('supervisor_tool.multiprocessing.Process')
    def test_start_workers(self, mock_process):
        """Test starting worker processes."""
        global config
        config.num_workers = 2
        config.base_port = 9000

        mock_process.return_value.start = Mock()

        start_workers()

        assert len(workers) == 2
        assert workers[0]["port"] == 9000
        assert workers[1]["port"] == 9001
        assert all(w["active"] for w in workers)

    def test_stop_workers(self):
        """Test stopping worker processes."""
        global workers
        # Mock workers
        mock_process1 = Mock()
        mock_process1.is_alive.return_value = True
        mock_process2 = Mock()
        mock_process2.is_alive.return_value = False

        workers.append({"port": 9000, "process": mock_process1, "active": True})
        workers.append({"port": 9001, "process": mock_process2, "active": True})

        stop_workers()

        mock_process1.terminate.assert_called_once()
        mock_process1.join.assert_called_once()
        assert len(workers) == 0
        assert len(session_map) == 0

    def test_get_next_worker_port_round_robin(self):
        """Test round-robin worker selection."""
        global workers
        workers.append({"port": 9000, "active": True})
        workers.append({"port": 9001, "active": True})
        workers.append({"port": 9002, "active": False})  # Inactive worker

        # First call should return 9000
        port1 = get_next_worker_port()
        assert port1 == 9000

        # Second call should return 9001
        port2 = get_next_worker_port()
        assert port2 == 9001

        # Third call should return 9000 again
        port3 = get_next_worker_port()
        assert port3 == 9000

    def test_get_next_worker_port_no_active(self):
        """Test behavior when no workers are active."""
        global workers
        workers.append({"port": 9000, "active": False})

        port = get_next_worker_port()
        assert port is None


class TestSessionMapping:
    """Test session mapping functions."""

    def setup_method(self):
        """Reset global state before each test."""
        global workers, session_map
        workers.clear()
        session_map.clear()
        workers.append({"port": 9000, "active": True})
        workers.append({"port": 9001, "active": True})

    def test_select_worker_for_existing_session(self):
        """Test selecting worker for existing session."""
        session_map["test-session-123"] = 9001

        port = select_worker_for_session("test-session-123")
        assert port == 9001

    def test_select_worker_for_new_session(self):
        """Test selecting worker for new session."""
        port = select_worker_for_session("new-session-456")
        assert port in [9000, 9001]
        assert session_map["new-session-456"] == port

    def test_select_worker_no_workers(self):
        """Test behavior when no workers are available."""
        global workers
        workers.clear()

        port = select_worker_for_session("test-session")
        assert port is None


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
            result = extract_session_id_from_path(path)
            assert result == expected

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
            result = extract_session_id_from_path(path)
            assert result is None


class TestAPIEndpoints:
    """Test API endpoints."""

    def setup_method(self):
        """Reset global state before each test."""
        global workers, session_map
        workers.clear()
        session_map.clear()

    def test_health_endpoint(self):
        """Test health check endpoint."""
        with TestClient(test_app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert "status" in data
            assert "active_workers" in data
            assert "total_sessions" in data

    @patch('supervisor_tool.forward_request')
    def test_create_session_endpoint(self, mock_forward):
        """Test session creation endpoint."""
        global workers
        workers.append({"port": 9000, "active": True})

        # Mock successful response with session_id
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = json.dumps({"session_id": "test-session-123"}).encode()
        mock_response.headers = {"content-type": "application/json"}
        mock_forward.return_value = mock_response

        with TestClient(test_app) as client:
            response = client.post("/v1/sessions/start", json={"driver_sources": []})

            assert response.status_code == 200
            assert session_map["test-session-123"] == 9000

    @patch('supervisor_tool.forward_request')
    def test_forward_to_worker_with_session(self, mock_forward):
        """Test forwarding request with session ID."""
        global workers, session_map
        workers.append({"port": 9000, "active": True})
        session_map["test-session-123"] = 9000

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"OK"
        mock_response.headers = {"content-type": "text/plain"}
        mock_forward.return_value = mock_response

        with TestClient(test_app) as client:
            response = client.post("/v1/sessions/test-session-123/action", json={"keyword": "test"})

            assert response.status_code == 200
            mock_forward.assert_called_once()

    @patch('supervisor_tool.forward_request')
    def test_forward_to_worker_no_session(self, mock_forward):
        """Test forwarding request without session ID."""
        global workers
        workers.append({"port": 9000, "active": True})

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
