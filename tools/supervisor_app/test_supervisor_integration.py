#!/usr/bin/env python3
"""
Integration tests for supervisor_tool.py

These tests spawn the real supervisor process, which in turn spawns real
uvicorn worker processes. Workers run the stub app (stub_worker.py) instead of
the full optics API so no Appium hub or device is needed — the stub keeps
per-process session state, so a request routed to the wrong worker fails with
404 exactly like the real app would.

They bind real ports and take tens of seconds, so they are gated:

    SUPERVISOR_INTEGRATION=1 python -m pytest test_supervisor_integration.py -v -s

Set SUPERVISOR_WORKER_APP=optics_framework.common.expose_api:app (and have an
Appium hub + device available) to run the same suite against real workers.
"""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.skipif(
    os.environ.get("SUPERVISOR_INTEGRATION") != "1",
    reason="integration tests spawn real processes; set SUPERVISOR_INTEGRATION=1 to run",
)

HERE = Path(__file__).resolve().parent
SUPERVISOR_SCRIPT = HERE / "supervisor_tool.py"

SUPERVISOR_HOST = "127.0.0.1"
SUPERVISOR_PORT = 18000
WORKER_BASE_PORT = 19000
NUM_WORKERS = 2
WORKER_APP = os.environ.get("SUPERVISOR_WORKER_APP", "stub_worker:app")


class SupervisorTestFixture:
    """Test fixture for running the supervisor with real worker processes."""

    def __init__(self, port=SUPERVISOR_PORT, worker_base_port=WORKER_BASE_PORT,
                 num_workers=NUM_WORKERS, extra_env=None):
        self.supervisor_process = None
        self.port = port
        self.worker_base_port = worker_base_port
        self.num_workers = num_workers
        self.extra_env = extra_env or {}
        self.base_url = f"http://{SUPERVISOR_HOST}:{port}"

    def start(self):
        """Start supervisor (which starts its own workers)."""
        env = dict(os.environ)
        env["SUPERVISOR_WORKER_APP"] = WORKER_APP
        # Workers must be able to import the stub app module.
        env["PYTHONPATH"] = str(HERE) + os.pathsep + env.get("PYTHONPATH", "")
        env.update(self.extra_env)

        cmd = [
            sys.executable,
            str(SUPERVISOR_SCRIPT),
            "--workers", str(self.num_workers),
            "--base-port", str(self.worker_base_port),
            "--host", SUPERVISOR_HOST,
            "--port", str(self.port),
        ]
        self.supervisor_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(HERE),
        )

        # Poll /health until ready (worker startup is serialized and slow).
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.supervisor_process.poll() is not None:
                raise RuntimeError(
                    "Supervisor exited early during startup. Stderr:\n"
                    + self._read_stderr()
                )
            try:
                response = requests.get(f"{self.base_url}/health", timeout=2)
                if response.status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.5)

        stderr = self._read_stderr()
        self.stop()
        raise RuntimeError(f"Failed to start supervisor within timeout. Stderr:\n{stderr}")

    def _read_stderr(self) -> str:
        try:
            if self.supervisor_process and self.supervisor_process.stderr:
                return self.supervisor_process.stderr.read()
        except OSError:
            pass
        return "<failed to read supervisor stderr>"

    def stop(self):
        """Stop supervisor; it stops its own workers on shutdown."""
        if self.supervisor_process:
            self.supervisor_process.terminate()
            try:
                self.supervisor_process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.supervisor_process.kill()
                self.supervisor_process.wait()

    def kill_hard(self):
        """SIGKILL the supervisor so its shutdown/cleanup never runs.

        Its workers survive (they run in their own process groups), simulating
        a supervisor replica dying while sessions are live.
        """
        if self.supervisor_process:
            self.supervisor_process.kill()
            self.supervisor_process.wait()

    def reap_orphan_workers(self):
        """Kill workers left behind by kill_hard()."""
        for i in range(self.num_workers):
            port = self.worker_base_port + i
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=5
            )
            for pid in result.stdout.strip().split("\n"):
                if pid.strip():
                    subprocess.run(["kill", "-9", pid.strip()], timeout=5)

    def get_health(self):
        response = requests.get(f"{self.base_url}/health", timeout=5)
        response.raise_for_status()
        return response.json()

    def create_session(self):
        response = requests.post(
            f"{self.base_url}/v1/sessions/start",
            json={"driver_sources": []},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def execute_action(self, session_id, action_data=None):
        response = requests.post(
            f"{self.base_url}/v1/sessions/{session_id}/action",
            json=action_data or {"mode": "keyword", "keyword": "noop"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def stop_session(self, session_id):
        response = requests.delete(
            f"{self.base_url}/v1/sessions/{session_id}/stop",
            timeout=30,
        )
        return response


@pytest.fixture(scope="module")
def supervisor():
    """One supervisor (with its worker pool) shared by the whole module.

    Tests must make delta-based assertions on session counts — sessions
    accumulate across tests within the module.
    """
    fixture = SupervisorTestFixture()
    fixture.start()
    yield fixture
    fixture.stop()


class TestSupervisorIntegration:
    """Integration tests for supervisor tool."""

    def test_supervisor_health(self, supervisor):
        """Supervisor reports its worker pool as healthy."""
        health = supervisor.get_health()

        assert health["status"] == "healthy"
        assert health["active_workers"] == NUM_WORKERS

    def test_session_creation_and_action(self, supervisor):
        """A created session is routed back to the worker that owns it."""
        before = supervisor.get_health()["total_sessions"]

        result = supervisor.create_session()
        assert "session_id" in result
        session_id = result["session_id"]

        assert supervisor.get_health()["total_sessions"] == before + 1

        action_result = supervisor.execute_action(session_id)
        assert "execution_id" in action_result
        assert action_result["status"] == "SUCCESS"

    def test_session_affinity(self, supervisor):
        """The same session always lands on the same worker process."""
        sessions = [supervisor.create_session()["session_id"] for _ in range(4)]

        for session_id in sessions:
            pids = {supervisor.execute_action(session_id)["pid"] for _ in range(3)}
            # The stub reports its pid; affinity means every action for one
            # session hits the same process (a miss would 404 anyway).
            assert len(pids) == 1

    def test_load_balancing(self, supervisor):
        """Sessions are spread across the worker pool round-robin."""
        results = [supervisor.create_session() for _ in range(4)]
        pids = {r["pid"] for r in results}
        assert len(pids) == NUM_WORKERS

        distribution = supervisor.get_health()["session_distribution"]
        workers_with_sessions = [p for p, count in distribution.items() if count > 0]
        assert len(workers_with_sessions) == NUM_WORKERS

    def test_concurrent_requests(self, supervisor):
        """Concurrent session creation and usage succeeds."""

        def create_and_use_session(session_num):
            result = supervisor.create_session()
            session_id = result["session_id"]
            actions_completed = 0
            for _ in range(3):
                action_result = supervisor.execute_action(session_id)
                if "execution_id" in action_result:
                    actions_completed += 1
            return {"session_num": session_num, "session_id": session_id, "actions_completed": actions_completed}

        num_concurrent = 10
        with ThreadPoolExecutor(max_workers=num_concurrent) as executor:
            futures = [executor.submit(create_and_use_session, i) for i in range(num_concurrent)]
            results = [future.result() for future in as_completed(futures)]

        assert len(results) == num_concurrent
        assert all(r["actions_completed"] == 3 for r in results)

    def test_worker_pool_stability(self, supervisor):
        """Monitoring keeps the pool healthy over time (no false crash detection)."""
        initial_workers = supervisor.get_health()["active_workers"]
        time.sleep(3 * 2)  # a few MONITOR_INTERVAL ticks
        assert supervisor.get_health()["active_workers"] == initial_workers


@pytest.fixture(scope="class")
def redis_supervisors():
    """Two supervisor replicas sharing one (fake) Redis routing store.

    fakeredis's TcpFakeServer speaks the real Redis protocol over TCP, so the
    two supervisor subprocesses connect to it like a real server and no Redis
    install is needed.
    """
    import threading

    from fakeredis import TcpFakeServer

    redis_port = 16379
    server = TcpFakeServer((SUPERVISOR_HOST, redis_port), server_type="redis")
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    shared_env = {
        "SUPERVISOR_STORE": "redis",
        "SUPERVISOR_REDIS_URL": f"redis://{SUPERVISOR_HOST}:{redis_port}/0",
    }
    replica_a = SupervisorTestFixture(port=18200, worker_base_port=19200,
                                      num_workers=1, extra_env=shared_env)
    replica_b = SupervisorTestFixture(port=18300, worker_base_port=19300,
                                      num_workers=1, extra_env=shared_env)
    try:
        replica_a.start()
        replica_b.start()
        yield replica_a, replica_b
    finally:
        replica_b.stop()
        replica_a.stop()
        replica_a.reap_orphan_workers()  # in case A was hard-killed mid-test
        server.shutdown()


class TestTwoSupervisorsSharedStore:
    """Upgrade 1 acceptance: routing state lives in the shared store, so any
    replica can route any session and losing a replica loses nothing."""

    def test_cross_replica_routing_and_replica_loss(self, redis_supervisors):
        replica_a, replica_b = redis_supervisors

        # Both replicas see the whole worker fleet through the shared registry.
        assert replica_a.get_health()["active_workers"] == 2
        assert replica_b.get_health()["active_workers"] == 2

        # Create via A ...
        created = replica_a.create_session()
        session_id = created["session_id"]
        owner_pid = created["pid"]

        # ... act via B: the route comes from the shared store, and the action
        # lands on the worker that owns the session (wrong worker would 404).
        result = replica_b.execute_action(session_id)
        assert result["status"] == "SUCCESS"
        assert result["pid"] == owner_pid

        # Kill A without cleanup (its worker survives as an orphan process).
        replica_a.kill_hard()

        # B keeps serving the session, whichever replica created it.
        result = replica_b.execute_action(session_id)
        assert result["status"] == "SUCCESS"
        assert result["pid"] == owner_pid


def _wait_pid_dead(pid: int, timeout_s: float = 10) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.2)
    return False


PER_SESSION_LEASE_TTL_S = 4
PER_SESSION_REAP_INTERVAL_S = 1


@pytest.fixture(scope="class")
def per_session_supervisor():
    """A supervisor in per_session mode with an aggressive lease TTL."""
    fixture = SupervisorTestFixture(
        port=18400,
        worker_base_port=19400,  # unused in per_session mode
        num_workers=0,
        extra_env={
            "SUPERVISOR_WORKER_MODE": "per_session",
            "SUPERVISOR_LEASE_TTL_S": str(PER_SESSION_LEASE_TTL_S),
            "SUPERVISOR_REAP_INTERVAL_S": str(PER_SESSION_REAP_INTERVAL_S),
        },
    )
    fixture.start()
    yield fixture
    fixture.stop()


class TestPerSessionWorkers:
    """Upgrade 2 acceptance: one isolated worker per session, reaped on idle
    or explicit stop."""

    def test_sessions_get_distinct_concurrent_workers(self, per_session_supervisor):
        supervisor = per_session_supervisor
        session_a = supervisor.create_session()
        session_b = supervisor.create_session()

        # Two sessions, two distinct worker processes.
        assert session_a["pid"] != session_b["pid"]

        # A slow keyword on session A must not block session B.
        timings = {}

        def slow_action():
            start = time.monotonic()
            result = supervisor.execute_action(
                session_a["session_id"], {"stub_delay_s": 3})
            timings["slow"] = (start, time.monotonic())
            return result

        def fast_action():
            time.sleep(0.5)  # let the slow one get in flight first
            start = time.monotonic()
            result = supervisor.execute_action(session_b["session_id"])
            timings["fast"] = (start, time.monotonic())
            return result

        with ThreadPoolExecutor(max_workers=2) as executor:
            slow_future = executor.submit(slow_action)
            fast_future = executor.submit(fast_action)
            assert fast_future.result()["status"] == "SUCCESS"
            assert slow_future.result()["status"] == "SUCCESS"

        fast_start, fast_end = timings["fast"]
        slow_start, slow_end = timings["slow"]
        assert slow_start < fast_start and fast_end < slow_end, "actions did not overlap"
        assert fast_end - fast_start < 2, "fast action was blocked by the slow one"

    def test_idle_session_is_reaped(self, per_session_supervisor):
        supervisor = per_session_supervisor
        created = supervisor.create_session()
        session_id, worker_pid = created["session_id"], created["pid"]

        # Never renew: the lease expires and the reaper reclaims the worker.
        time.sleep(PER_SESSION_LEASE_TTL_S + 3 * PER_SESSION_REAP_INTERVAL_S + 1)

        assert _wait_pid_dead(worker_pid)
        response = requests.post(
            f"{supervisor.base_url}/v1/sessions/{session_id}/action", json={}, timeout=10)
        assert response.status_code == 503  # route is gone

    def test_explicit_stop_tears_worker_down(self, per_session_supervisor):
        supervisor = per_session_supervisor
        created = supervisor.create_session()
        session_id, worker_pid = created["session_id"], created["pid"]

        response = supervisor.stop_session(session_id)
        assert response.status_code == 200

        assert _wait_pid_dead(worker_pid)
        response = requests.post(
            f"{supervisor.base_url}/v1/sessions/{session_id}/action", json={}, timeout=10)
        assert response.status_code == 503


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
