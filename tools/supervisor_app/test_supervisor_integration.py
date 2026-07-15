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

    def __init__(self):
        self.supervisor_process = None
        self.base_url = f"http://{SUPERVISOR_HOST}:{SUPERVISOR_PORT}"

    def start(self):
        """Start supervisor (which starts its own workers)."""
        env = dict(os.environ)
        env["SUPERVISOR_WORKER_APP"] = WORKER_APP
        # Workers must be able to import the stub app module.
        env["PYTHONPATH"] = str(HERE) + os.pathsep + env.get("PYTHONPATH", "")

        cmd = [
            sys.executable,
            str(SUPERVISOR_SCRIPT),
            "--workers", str(NUM_WORKERS),
            "--base-port", str(WORKER_BASE_PORT),
            "--host", SUPERVISOR_HOST,
            "--port", str(SUPERVISOR_PORT),
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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
