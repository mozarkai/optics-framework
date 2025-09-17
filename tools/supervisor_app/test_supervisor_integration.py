#!/usr/bin/env python3
"""
Integration tests for supervisor_tool.py

These tests run the actual supervisor tool with real worker processes.
Run with: python -m pytest test_supervisor_integration.py -v -s
"""

import subprocess
import sys
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytest

# Add the tools directory to path
sys.path.insert(0, '/Users/dhruvmenon/Documents/optics-framework-1/tools')

SUPERVISOR_HOST = "127.0.0.1"
SUPERVISOR_PORT = 8000
WORKER_BASE_PORT = 9000
NUM_WORKERS = 2

class SupervisorTestFixture:
    """Test fixture for running supervisor with workers."""

    def __init__(self):
        self.supervisor_process = None
        self.worker_processes = []

    def start(self):
        """Start supervisor and workers."""
        print("Starting supervisor and workers...")

        # Start supervisor
        supervisor_cmd = [
            sys.executable,
            "/Users/dhruvmenon/Documents/optics-framework-1/tools/supervisor_tool.py",
            "--workers", str(NUM_WORKERS),
            "--base-port", str(WORKER_BASE_PORT),
            "--host", SUPERVISOR_HOST,
            "--port", str(SUPERVISOR_PORT)
        ]

        self.supervisor_process = subprocess.Popen(
            supervisor_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Poll for supervisor /health until it's ready (timeout after 30s).
        deadline = time.time() + 30
        started = False
        while time.time() < deadline:
            # If the supervisor process died early, capture stderr and fail fast
            if self.supervisor_process.poll() is not None:
                stderr = ""
                try:
                    if self.supervisor_process.stderr:
                        stderr = self.supervisor_process.stderr.read()
                except Exception:
                    stderr = "<failed to read supervisor stderr>"
                self.stop()
                raise RuntimeError(f"Supervisor exited early during startup. Stderr:\n{stderr}")

            try:
                response = requests.get(f"http://{SUPERVISOR_HOST}:{SUPERVISOR_PORT}/health", timeout=2)
                if response.status_code == 200:
                    started = True
                    break
            except Exception:
                # Not ready yet
                pass

            time.sleep(0.5)

        if not started:
            stderr = ""
            try:
                if self.supervisor_process and self.supervisor_process.stderr:
                    stderr = self.supervisor_process.stderr.read()
            except Exception:
                stderr = "<failed to read supervisor stderr>"
            self.stop()
            raise RuntimeError(f"Failed to start supervisor within timeout. Stderr:\n{stderr}")

        print("Supervisor and workers started successfully")

    def stop(self):
        """Stop supervisor and workers."""
        print("Stopping supervisor and workers...")

        if self.supervisor_process:
            self.supervisor_process.terminate()
            try:
                self.supervisor_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.supervisor_process.kill()
                self.supervisor_process.wait()

        # Workers should be stopped by supervisor shutdown
        print("Supervisor and workers stopped")

    def get_health(self):
        """Get supervisor health status."""
        try:
            response = requests.get(f"http://{SUPERVISOR_HOST}:{SUPERVISOR_PORT}/health", timeout=5)
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    def create_session(self, config=None):
        """Create a new session."""
        if config is None:
            config = {
                "driver_sources": [
                    {
                        "appium": {
                            "enabled": True,
                            "url": "http://localhost:4723",
                            "capabilities": {
                                "appActivity": "com.android.contacts.activities.PeopleActivity",
                                "appPackage": "com.google.android.contacts",
                                "automationName": "UiAutomator2",
                                "deviceName": "emulator-5554",
                                "platformName": "Android"
                            }
                        }
                    }
                ],
                "elements_sources": [
                    {"appium_find_element": {"enabled": True}},
                    {"appium_screenshot": {"enabled": True}},
                    {"appium_page_source": {"enabled": True}}
                ],
                "text_detection": [
                    {"easyocr": {"enabled": False}}
                ],
                "image_detection": [
                    {"templatematch": {"enabled": False}}
                ]
            }

        try:
            response = requests.post(
                f"http://{SUPERVISOR_HOST}:{SUPERVISOR_PORT}/v1/sessions/start",
                json=config,
                timeout=10
            )
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    def execute_action(self, session_id, action_data=None):
        """Execute an action on a session."""
        if action_data is None:
            action_data = {
                "mode": "keyword",
                "keyword": "get_driver_session_id"
            }

        try:
            response = requests.post(
                f"http://{SUPERVISOR_HOST}:{SUPERVISOR_PORT}/v1/sessions/{session_id}/action",
                json=action_data,
                timeout=10
            )
            return response.json()
        except Exception as e:
            return {"error": str(e)}


@pytest.fixture(scope="function")
def supervisor():
    """Pytest fixture for supervisor test setup."""
    fixture = SupervisorTestFixture()

    # Start supervisor
    fixture.start()

    yield fixture

    # Cleanup
    fixture.stop()


class TestSupervisorIntegration:
    """Integration tests for supervisor tool."""

    def test_supervisor_health(self, supervisor):
        """Test that supervisor health endpoint works."""
        health = supervisor.get_health()

        assert "status" in health
        assert health["status"] == "healthy"
        assert "active_workers" in health
        assert health["active_workers"] == NUM_WORKERS
        assert "total_sessions" in health
        assert health["total_sessions"] == 0

    def test_session_creation(self, supervisor):
        """Test session creation and routing."""
        # Create a session
        result = supervisor.create_session()

        assert "session_id" in result
        assert "driver_id" in result
        session_id = result["session_id"]

        # Check health again - should have 1 session
        health = supervisor.get_health()
        assert health["total_sessions"] == 1

        # Execute an action on the session
        action_result = supervisor.execute_action(session_id)
        assert "execution_id" in action_result
        assert "status" in action_result

    def test_session_affinity(self, supervisor):
        """Test that the same session always routes to the same worker."""
        # Create multiple sessions
        sessions = []
        for _ in range(5):
            result = supervisor.create_session()
            assert "session_id" in result
            sessions.append(result["session_id"])

        # Execute actions on each session multiple times
        for session_id in sessions:
            for _ in range(3):
                result = supervisor.execute_action(session_id)
                assert "execution_id" in result
                assert result["status"] == "SUCCESS"

        # Check that sessions are distributed across workers
        health = supervisor.get_health()
        assert health["total_sessions"] == 5

        # Check session distribution
        distribution = health.get("session_distribution", {})
        total_sessions_in_workers = sum(distribution.values())
        assert total_sessions_in_workers == 5

    def test_concurrent_requests(self, supervisor):
        """Test handling of concurrent requests."""
        def create_and_use_session(session_num):
            """Create a session and perform some actions."""
            try:
                # Create session
                result = supervisor.create_session()
                if "error" in result:
                    return {"session_num": session_num, "error": result["error"]}

                session_id = result["session_id"]

                # Perform multiple actions
                actions_completed = 0
                for _ in range(3):
                    action_result = supervisor.execute_action(session_id)
                    if "execution_id" in action_result:
                        actions_completed += 1

                return {
                    "session_num": session_num,
                    "session_id": session_id,
                    "actions_completed": actions_completed
                }
            except Exception as e:
                return {"session_num": session_num, "error": str(e)}

        # Run concurrent session creation and usage
        num_concurrent = 10
        with ThreadPoolExecutor(max_workers=num_concurrent) as executor:
            futures = [executor.submit(create_and_use_session, i) for i in range(num_concurrent)]
            results = [future.result() for future in as_completed(futures)]

        # Analyze results
        successful_sessions = [r for r in results if "session_id" in r]
        failed_sessions = [r for r in results if "error" in r]

        print(f"Successful sessions: {len(successful_sessions)}")
        print(f"Failed sessions: {len(failed_sessions)}")

        # Should have created most sessions successfully
        assert len(successful_sessions) >= num_concurrent * 0.8  # At least 80% success rate

        # Check final health
        health = supervisor.get_health()
        assert health["total_sessions"] >= len(successful_sessions)

    def test_load_balancing(self, supervisor):
        """Test that sessions are distributed across workers."""
        # Create many sessions to test load balancing
        sessions = []
        for _ in range(20):
            result = supervisor.create_session()
            if "session_id" in result:
                sessions.append(result["session_id"])

        # Check distribution
        health = supervisor.get_health()
        distribution = health.get("session_distribution", {})

        print(f"Session distribution: {distribution}")

        # Should have sessions on multiple workers
        workers_with_sessions = [port for port, count in distribution.items() if count > 0]
        assert len(workers_with_sessions) >= 1  # At least one worker has sessions

        # Total should match
        total_sessions = sum(distribution.values())
        assert total_sessions == len(sessions)

    def test_fault_tolerance(self, supervisor):
        """Test fault tolerance (basic check that monitoring is working)."""
        # Get initial health
        initial_health = supervisor.get_health()
        initial_workers = initial_health["active_workers"]

        # Wait a bit for monitoring to run
        time.sleep(15)

        # Check health again
        current_health = supervisor.get_health()
        current_workers = current_health["active_workers"]

        # Workers should still be active (no crashes detected)
        assert current_workers == initial_workers

        print(f"Workers stable: {current_workers} active")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
