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

SESSION_ID = "12345678-1234-5678-9012-123456789012"


def _register_pool(*ports: int):
    """Register pool workers both locally and in the routing store."""
    for port in ports:
        st.workers.append({"port": port, "process": Mock(), "active": True})
        st.store.register_worker(st._endpoint_for_port(port), st.WORKER_TTL_S)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset supervisor global state before each test."""
    st.workers.clear()
    st.session_workers.clear()
    st.store = st._create_store()
    yield
    st.workers.clear()
    st.session_workers.clear()
    st.store = st._create_store()


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
        """Started workers appear in the local pool and the store registry."""
        st.config.num_workers = 2
        st.config.base_port = 9000
        mock_start.side_effect = lambda port, log_path=None: (Mock(), None)

        st.start_workers()

        assert len(st.workers) == 2
        assert st.workers[0]["port"] == 9000
        assert st.workers[1]["port"] == 9001
        assert all(w["active"] for w in st.workers)
        assert st.store.list_live_workers() == [
            "http://127.0.0.1:9000",
            "http://127.0.0.1:9001",
        ]

    @patch("supervisor_tool.time.sleep")
    @patch("supervisor_tool.start_worker_process")
    def test_start_workers_partial_failure(self, mock_start, _mock_sleep):
        """A worker that fails to start is registered nowhere."""
        st.config.num_workers = 2
        st.config.base_port = 9000
        mock_start.side_effect = [(Mock(), None), (None, None)]

        st.start_workers()

        assert len(st.workers) == 1
        assert st.store.list_live_workers() == ["http://127.0.0.1:9000"]

    @patch("supervisor_tool._kill_worker")
    def test_stop_workers(self, mock_kill):
        """Stopping tears down each worker and clears its registry entries and routes."""
        _register_pool(9000, 9001)
        st.store.put_route("some-session", "http://127.0.0.1:9000")

        st.stop_workers()

        assert mock_kill.call_count == 2
        assert len(st.workers) == 0
        assert st.store.list_live_workers() == []
        assert st.store.get_route("some-session") is None

    @patch("supervisor_tool._kill_worker")
    def test_stop_workers_leaves_foreign_routes(self, mock_kill):
        """A supervisor only cleans up routes that point at its own workers."""
        _register_pool(9000)
        st.store.put_route("mine", "http://127.0.0.1:9000")
        st.store.put_route("someone-elses", "http://10.0.0.9:9000")

        st.stop_workers()

        assert st.store.get_route("mine") is None
        assert st.store.get_route("someone-elses") == "http://10.0.0.9:9000"

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

    def test_round_robin_over_live_workers(self):
        """next_worker rotates over live store registrations."""
        _register_pool(9000, 9001)

        assert st.store.next_worker() == "http://127.0.0.1:9000"
        assert st.store.next_worker() == "http://127.0.0.1:9001"
        assert st.store.next_worker() == "http://127.0.0.1:9000"

    def test_next_worker_none_when_empty(self):
        """No registered workers means no pick."""
        assert st.store.next_worker() is None

    def test_heartbeat_keeps_worker_alive(self):
        """Workers expire from the registry without heartbeats."""
        st.store.register_worker("http://127.0.0.1:9000", ttl_s=0.0)
        assert st.store.list_live_workers() == []

        st.store.register_worker("http://127.0.0.1:9000", ttl_s=10.0)
        assert st.store.list_live_workers() == ["http://127.0.0.1:9000"]


class TestWorkerCrashHandling:
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

    def test_crash_cleans_up_registry_and_routes(self):
        """A crashed worker is deregistered and its sessions' routes removed."""
        st.workers.append(self._crashed_worker())
        st.store.register_worker("http://127.0.0.1:9000", st.WORKER_TTL_S)
        st.store.put_route("dead-session", "http://127.0.0.1:9000")
        st.store.put_route("other-session", "http://127.0.0.1:9001")

        st.check_worker_health()

        assert st.workers[0]["active"] is False
        assert st.store.list_live_workers() == []
        assert st.store.get_route("dead-session") is None
        assert st.store.get_route("other-session") == "http://127.0.0.1:9001"

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
        assert st.store.list_live_workers() == ["http://127.0.0.1:9000"]


class TestSessionRouting:
    """Test session routing decisions."""

    @pytest.fixture(autouse=True)
    def two_workers(self):
        _register_pool(9000, 9001)

    def test_select_worker_for_existing_session(self):
        """An already-routed session keeps its worker."""
        st.store.put_route("test-session-123", "http://127.0.0.1:9001")

        assert st.select_worker_for_session("test-session-123") == "http://127.0.0.1:9001"

    def test_select_worker_for_new_session(self):
        """An unrouted session is assigned a live worker and remembered."""
        endpoint = st.select_worker_for_session("new-session-456")
        assert endpoint in ["http://127.0.0.1:9000", "http://127.0.0.1:9001"]
        assert st.store.get_route("new-session-456") == endpoint

    def test_select_worker_no_workers(self):
        """No live workers means no route."""
        st.workers.clear()
        st.store = st._create_store()

        assert st.select_worker_for_session("test-session") is None


class TestPathParsing:
    """Test URL path parsing functions."""

    def test_extract_session_id_from_path_valid(self):
        """Test extracting session ID from valid paths."""
        test_cases = [
            (f"/v1/sessions/{SESSION_ID}/action", SESSION_ID),
            (f"/v1/session/{SESSION_ID}/screenshot", SESSION_ID),
            (f"/v1/sessions/{SESSION_ID}/events", SESSION_ID),
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

    def test_health_reports_distribution(self):
        """Health surfaces sessions per live worker."""
        _register_pool(9000, 9001)
        st.store.put_route("s1", "http://127.0.0.1:9000")
        st.store.put_route("s2", "http://127.0.0.1:9000")

        with TestClient(test_app) as client:
            data = client.get("/health").json()

        assert data["status"] == "healthy"
        assert data["active_workers"] == 2
        assert data["total_sessions"] == 2
        assert data["session_distribution"] == {"9000": 2, "9001": 0}

    @patch("supervisor_tool.forward_request")
    def test_create_session_endpoint(self, mock_forward):
        """Test session creation endpoint."""
        _register_pool(9000)

        # Mock successful response with session_id
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = json.dumps({"session_id": "test-session-123"}).encode()
        mock_response.headers = {"content-type": "application/json"}
        mock_forward.return_value = mock_response

        with TestClient(test_app) as client:
            response = client.post("/v1/sessions/start", json={"driver_sources": []})

            assert response.status_code == 200
            assert st.store.get_route("test-session-123") == "http://127.0.0.1:9000"

    def test_create_session_no_workers(self):
        """Session creation without workers returns 503."""
        with TestClient(test_app) as client:
            response = client.post("/v1/sessions/start", json={"driver_sources": []})
            assert response.status_code == 503

    @patch("supervisor_tool.forward_request")
    def test_create_session_failure_does_not_map(self, mock_forward):
        """A non-200 from the worker must not create a route."""
        _register_pool(9000)

        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.content = b'{"detail": "boom"}'
        mock_response.headers = {"content-type": "application/json"}
        mock_forward.return_value = mock_response

        with TestClient(test_app) as client:
            response = client.post("/v1/sessions/start", json={"driver_sources": []})

            assert response.status_code == 500
            assert st.store.list_routes() == {}

    @patch("supervisor_tool.forward_request")
    def test_forward_to_worker_with_session(self, mock_forward):
        """Test forwarding request with session ID."""
        _register_pool(9000)
        st.store.put_route(SESSION_ID, "http://127.0.0.1:9000")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"OK"
        mock_response.headers = {"content-type": "text/plain"}
        mock_forward.return_value = mock_response

        with TestClient(test_app) as client:
            response = client.post(f"/v1/sessions/{SESSION_ID}/action", json={"keyword": "test"})

            assert response.status_code == 200
            mock_forward.assert_called_once()
            forwarded_url = mock_forward.call_args.kwargs.get("url") or mock_forward.call_args.args[1]
            assert forwarded_url.startswith("http://127.0.0.1:9000/")

    @patch("supervisor_tool.forward_request")
    def test_forward_to_worker_no_session(self, mock_forward):
        """Test forwarding request without session ID."""
        _register_pool(9000)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"OK"
        mock_response.headers = {"content-type": "text/plain"}
        mock_forward.return_value = mock_response

        with TestClient(test_app) as client:
            response = client.get("/v1/keywords")

            assert response.status_code == 200
            mock_forward.assert_called_once()

    @patch("supervisor_tool.forward_request")
    def test_stop_session_deletes_route(self, mock_forward):
        """A successful stop removes the session's route."""
        _register_pool(9000)
        st.store.put_route(SESSION_ID, "http://127.0.0.1:9000")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'{"status": "terminated"}'
        mock_response.headers = {"content-type": "application/json"}
        mock_forward.return_value = mock_response

        with TestClient(test_app) as client:
            response = client.delete(f"/v1/sessions/{SESSION_ID}/stop")

            assert response.status_code == 200
            assert st.store.get_route(SESSION_ID) is None

    @patch("supervisor_tool.forward_request")
    def test_failed_stop_keeps_route(self, mock_forward):
        """A failed stop (worker error) must not drop the route."""
        _register_pool(9000)
        st.store.put_route(SESSION_ID, "http://127.0.0.1:9000")

        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.content = b'{"detail": "boom"}'
        mock_response.headers = {"content-type": "application/json"}
        mock_forward.return_value = mock_response

        with TestClient(test_app) as client:
            response = client.delete(f"/v1/sessions/{SESSION_ID}/stop")

            assert response.status_code == 500
            assert st.store.get_route(SESSION_ID) == "http://127.0.0.1:9000"


class FakeLauncher:
    """In-memory WorkerLauncher double recording lifecycle calls."""

    def __init__(self, ready=True, forward_status=200):
        self.ready = ready
        self.launched = []
        self.stopped = []
        self._alive = set()
        self._counter = 0

    async def launch(self):
        from launcher import WorkerHandle

        self._counter += 1
        handle = WorkerHandle(id=f"fake-{self._counter}",
                              endpoint=f"http://127.0.0.1:{9500 + self._counter}")
        self.launched.append(handle)
        self._alive.add(handle.id)
        return handle

    async def wait_ready(self, handle, timeout_s):
        return self.ready

    async def stop(self, handle):
        self.stopped.append(handle)
        self._alive.discard(handle.id)

    async def is_alive(self, handle):
        return handle.id in self._alive


def _forward_response(status_code=200, body=None):
    response = Mock()
    response.status_code = status_code
    response.content = json.dumps(body or {}).encode()
    response.headers = {"content-type": "application/json"}
    return response


class TestPerSessionMode:
    """Unit tests for the per_session worker mode (mocked launcher/forward)."""

    @pytest.fixture(autouse=True)
    def per_session(self):
        fake = FakeLauncher()
        with patch.object(st, "WORKER_MODE", "per_session"), \
             patch.object(st, "launcher", fake):
            yield fake

    @patch("supervisor_tool.forward_request")
    def test_create_launches_dedicated_worker(self, mock_forward, per_session):
        mock_forward.return_value = _forward_response(200, {"session_id": "sid-1"})

        with TestClient(test_app) as client:
            response = client.post("/v1/sessions/start", json={"driver_sources": []})

        assert response.status_code == 200
        assert len(per_session.launched) == 1
        handle = per_session.launched[0]
        assert st.store.get_route("sid-1") == handle.endpoint
        assert st.session_workers["sid-1"] is handle
        assert handle.endpoint in st.store.list_live_workers()
        assert st.store.expired_leases() == []  # lease exists and is fresh
        assert per_session.stopped == []

    @patch("supervisor_tool.forward_request")
    def test_two_sessions_get_distinct_workers(self, mock_forward, per_session):
        mock_forward.side_effect = [
            _forward_response(200, {"session_id": "sid-1"}),
            _forward_response(200, {"session_id": "sid-2"}),
        ]

        with TestClient(test_app) as client:
            client.post("/v1/sessions/start", json={})
            client.post("/v1/sessions/start", json={})

        assert st.store.get_route("sid-1") != st.store.get_route("sid-2")

    def test_worker_not_ready_returns_503(self, per_session):
        per_session.ready = False

        with TestClient(test_app) as client:
            response = client.post("/v1/sessions/start", json={})

        assert response.status_code == 503
        assert len(per_session.stopped) == 1  # no leaked worker
        assert st.store.list_routes() == {}

    @patch("supervisor_tool.forward_request")
    def test_failed_create_tears_worker_down(self, mock_forward, per_session):
        mock_forward.return_value = _forward_response(500, {"detail": "boom"})

        with TestClient(test_app) as client:
            response = client.post("/v1/sessions/start", json={})

        assert response.status_code == 500
        assert len(per_session.stopped) == 1
        assert st.store.list_routes() == {}

    @patch("supervisor_tool.forward_request")
    def test_unknown_session_is_not_bound(self, mock_forward, per_session):
        """per_session must never round-robin an unknown session onto someone
        else's worker."""
        st.store.register_worker("http://127.0.0.1:9501", ttl_s=10)

        with TestClient(test_app) as client:
            response = client.post(f"/v1/sessions/{SESSION_ID}/action", json={})

        assert response.status_code == 503
        assert st.store.list_routes() == {}
        mock_forward.assert_not_called()

    @patch("supervisor_tool.forward_request")
    def test_request_renews_expired_lease(self, mock_forward, per_session):
        """Traffic on a still-routed session refreshes its lease."""
        import time as _time

        st.store.put_route(SESSION_ID, "http://127.0.0.1:9501")
        st.store.acquire_lease(SESSION_ID, owner="fake-1", ttl_s=0.05)
        _time.sleep(0.1)
        assert st.store.expired_leases() != []

        mock_forward.return_value = _forward_response(200, {"status": "SUCCESS"})
        with TestClient(test_app) as client:
            client.post(f"/v1/sessions/{SESSION_ID}/action", json={})

        assert st.store.expired_leases() == []

    @patch("supervisor_tool.forward_request")
    def test_stop_tears_down_worker_route_and_lease(self, mock_forward, per_session):
        mock_forward.side_effect = [
            _forward_response(200, {"session_id": SESSION_ID}),
            _forward_response(200, {"status": "terminated"}),
        ]

        with TestClient(test_app) as client:
            client.post("/v1/sessions/start", json={})
            response = client.delete(f"/v1/sessions/{SESSION_ID}/stop")

        assert response.status_code == 200
        assert st.store.get_route(SESSION_ID) is None
        assert st.session_workers == {}
        assert len(per_session.stopped) == 1
        assert st.store.expired_leases() == []
        assert st.store.list_live_workers() == []


class TestReaper:
    """Unit tests for the lease reaper sweep."""

    @pytest.fixture(autouse=True)
    def per_session(self):
        fake = FakeLauncher()
        with patch.object(st, "WORKER_MODE", "per_session"), \
             patch.object(st, "launcher", fake):
            yield fake

    def _launch_session(self, fake, session_id, ttl_s):
        import asyncio

        handle = asyncio.run(fake.launch())
        st.session_workers[session_id] = handle
        st.store.put_route(session_id, handle.endpoint)
        st.store.register_worker(handle.endpoint, ttl_s=10)
        st.store.acquire_lease(session_id, owner=handle.id, ttl_s=ttl_s)
        return handle

    def test_expired_lease_reaps_local_worker(self, per_session):
        import asyncio
        import time as _time

        handle = self._launch_session(per_session, "sid-1", ttl_s=0.05)
        _time.sleep(0.1)

        asyncio.run(st._reap_once())

        assert per_session.stopped == [handle]
        assert st.store.get_route("sid-1") is None
        assert st.store.expired_leases() == []
        assert st.session_workers == {}
        assert st.store.list_live_workers() == []

    def test_active_lease_is_untouched(self, per_session):
        import asyncio

        handle = self._launch_session(per_session, "sid-1", ttl_s=10)

        asyncio.run(st._reap_once())

        assert per_session.stopped == []
        assert st.store.get_route("sid-1") == handle.endpoint

    def test_expired_foreign_lease_reconstructs_handle(self, per_session):
        """A remote-capable launcher can stop a worker owned by a dead replica."""
        import asyncio
        import time as _time

        st.store.put_route("sid-remote", "http://10.0.0.9:9001")
        st.store.acquire_lease("sid-remote", owner="pod-abc", ttl_s=0.05)
        _time.sleep(0.1)

        asyncio.run(st._reap_once())

        assert len(per_session.stopped) == 1
        assert per_session.stopped[0].id == "pod-abc"
        assert per_session.stopped[0].endpoint == "http://10.0.0.9:9001"
        assert st.store.get_route("sid-remote") is None
        assert st.store.expired_leases() == []

    def test_route_removed_elsewhere_stops_local_worker(self, per_session):
        """Another replica handled the stop; this replica owns the process."""
        import asyncio

        handle = self._launch_session(per_session, "sid-1", ttl_s=10)
        st.store.delete_route("sid-1")  # simulates a stop via another replica

        asyncio.run(st._reap_once())

        assert per_session.stopped == [handle]
        assert st.session_workers == {}


if __name__ == "__main__":
    pytest.main([__file__])
