#!/usr/bin/env python3
"""
Tests for the WorkerLauncher implementations.

SubprocessLauncher tests spawn real uvicorn processes running the stub worker
app (no Appium needed) on ephemeral ports; each takes a couple of seconds.

Run with: python -m pytest test_launcher.py -v
"""

import asyncio
import os
from pathlib import Path

import pytest

from optics_framework.helper.supervisor.launcher import (
    DockerLauncher,
    K8sLauncher,
    SubprocessLauncher,
    WorkerHandle,
    WorkerLaunchError,
    create_launcher,
)

HERE = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def stub_worker_importable(monkeypatch):
    """Ensure spawned uvicorn workers can import the stub app."""
    monkeypatch.setenv("PYTHONPATH", str(HERE) + os.pathsep + os.environ.get("PYTHONPATH", ""))


@pytest.fixture
def launcher():
    instance = SubprocessLauncher(worker_app="stub_worker:app")
    yield instance
    # Belt-and-braces: kill anything a failing test left behind.
    for handle_id in instance.owned_handle_ids():
        proc, log_fh = instance._procs[handle_id]
        proc.kill()
        proc.wait()
        if log_fh:
            log_fh.close()


def run(coro):
    return asyncio.run(coro)


class TestSubprocessLauncher:
    def test_launch_wait_ready_stop(self, launcher):
        async def scenario():
            handle = await launcher.launch()
            assert handle.endpoint.startswith("http://127.0.0.1:")
            assert await launcher.wait_ready(handle, timeout_s=30)
            assert await launcher.is_alive(handle)

            await launcher.stop(handle)
            assert not await launcher.is_alive(handle)

        run(scenario())

    def test_ephemeral_ports_do_not_collide(self, launcher):
        async def scenario():
            first = await launcher.launch()
            second = await launcher.launch()
            try:
                assert first.endpoint != second.endpoint
                assert first.id != second.id
                assert await launcher.wait_ready(first, timeout_s=30)
                assert await launcher.wait_ready(second, timeout_s=30)
            finally:
                await launcher.stop(first)
                await launcher.stop(second)

        run(scenario())

    def test_wait_ready_times_out_for_dead_worker(self):
        broken = SubprocessLauncher(worker_app="no_such_module:app")

        async def scenario():
            handle = await broken.launch()
            try:
                # The process exits immediately (bad import), which wait_ready
                # detects without burning the whole timeout.
                assert not await broken.wait_ready(handle, timeout_s=10)
            finally:
                await broken.stop(handle)

        run(scenario())

    def test_stop_unknown_handle_is_noop(self, launcher):
        ghost = WorkerHandle(id="not-ours", endpoint="http://127.0.0.1:1")
        run(launcher.stop(ghost))  # must not raise
        assert not run(launcher.is_alive(ghost))


class FakeRunner:
    """CommandRunner double: records commands, replays canned results."""

    def __init__(self, results=None):
        self.commands = []
        self.results = list(results or [])
        self.default = (0, "", "")

    async def __call__(self, cmd):
        self.commands.append(cmd)
        if self.results:
            return self.results.pop(0)
        return self.default

    def command_strings(self):
        return [" ".join(cmd) for cmd in self.commands]


class TestDockerLauncher:
    def _launcher(self, runner, **kwargs):
        kwargs.setdefault("image", "optics-worker:latest")
        kwargs.setdefault("network", "optics-net")
        kwargs.setdefault("resources", "--memory=1g --cpus=1")
        kwargs.setdefault("port", 8000)
        return DockerLauncher(runner=runner, **kwargs)

    def test_launch_returns_subnet_endpoint(self):
        runner = FakeRunner(results=[
            (0, "containersha\n", ""),   # docker run
            (0, "10.5.0.7\n", ""),       # docker inspect (IP)
        ])
        handle = run(self._launcher(runner).launch())

        assert handle.endpoint == "http://10.5.0.7:8000"
        assert handle.id.startswith("optics-worker-")
        run_cmd = runner.command_strings()[0]
        assert run_cmd.startswith("docker run --detach --name optics-worker-")
        assert "--network optics-net" in run_cmd
        assert "--memory=1g --cpus=1" in run_cmd
        assert run_cmd.endswith("optics-worker:latest")

    def test_launch_failure_raises(self):
        runner = FakeRunner(results=[(1, "", "no such image")])
        with pytest.raises(WorkerLaunchError, match="docker run failed"):
            run(self._launcher(runner).launch())

    def test_launch_without_ip_cleans_up(self):
        runner = FakeRunner(results=[
            (0, "containersha\n", ""),
            (0, "", ""),                 # inspect returns no IP
            (0, "", ""),                 # docker rm
        ])
        with pytest.raises(WorkerLaunchError, match="subnet IP"):
            run(self._launcher(runner).launch())
        assert any(cmd[:3] == ["docker", "rm", "--force"] for cmd in runner.commands)

    def test_stop_removes_container(self):
        runner = FakeRunner()
        handle = WorkerHandle(id="optics-worker-abc", endpoint="http://10.5.0.7:8000")
        run(self._launcher(runner).stop(handle))
        assert runner.commands == [["docker", "rm", "--force", "optics-worker-abc"]]

    def test_is_alive_parses_state(self):
        handle = WorkerHandle(id="optics-worker-abc", endpoint="http://10.5.0.7:8000")
        assert run(self._launcher(FakeRunner(results=[(0, "true\n", "")])).is_alive(handle))
        assert not run(self._launcher(FakeRunner(results=[(0, "false\n", "")])).is_alive(handle))
        assert not run(self._launcher(FakeRunner(results=[(1, "", "No such container")])).is_alive(handle))

    def test_requires_image(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_WORKER_IMAGE", raising=False)
        with pytest.raises(ValueError, match="SUPERVISOR_WORKER_IMAGE"):
            DockerLauncher(runner=FakeRunner())


class TestK8sLauncher:
    def _launcher(self, runner, **kwargs):
        kwargs.setdefault("image", "registry.local/optics-worker:latest")
        kwargs.setdefault("namespace", "optics-workers")
        kwargs.setdefault("port", 8000)
        kwargs.setdefault("ip_timeout_s", 2)
        return K8sLauncher(runner=runner, **kwargs)

    def test_launch_returns_pod_endpoint(self):
        runner = FakeRunner(results=[
            (0, "pod/x created\n", ""),  # kubectl run
            (0, "", ""),                 # first IP poll: not assigned yet
            (0, "10.42.3.9", ""),        # second IP poll
        ])
        handle = run(self._launcher(runner).launch())

        assert handle.endpoint == "http://10.42.3.9:8000"
        run_cmd = runner.command_strings()[0]
        assert run_cmd.startswith("kubectl --namespace optics-workers run optics-worker-")
        assert "--image=registry.local/optics-worker:latest" in run_cmd
        assert "--restart=Never" in run_cmd
        assert "--labels=app=optics-session-worker" in run_cmd

    def test_pod_deadline_backstop_is_applied(self):
        runner = FakeRunner(results=[
            (0, "", ""),
            (0, "10.42.3.9", ""),
        ])
        run(self._launcher(runner, pod_deadline_s=600).launch())
        assert '--overrides={"spec": {"activeDeadlineSeconds": 600}}' in runner.command_strings()[0]

    def test_launch_failure_raises(self):
        runner = FakeRunner(results=[(1, "", "forbidden")])
        with pytest.raises(WorkerLaunchError, match="kubectl run failed"):
            run(self._launcher(runner).launch())

    def test_no_ip_within_timeout_cleans_up(self):
        runner = FakeRunner(results=[(0, "pod/x created\n", "")])
        runner.default = (0, "", "")  # IP never assigned; delete also hits default
        with pytest.raises(WorkerLaunchError, match="no IP"):
            run(self._launcher(runner, ip_timeout_s=0.6).launch())
        assert any("delete" in cmd for cmd in runner.commands)

    def test_stop_deletes_pod(self):
        runner = FakeRunner()
        handle = WorkerHandle(id="optics-worker-abc", endpoint="http://10.42.3.9:8000")
        run(self._launcher(runner).stop(handle))
        assert runner.commands == [[
            "kubectl", "--namespace", "optics-workers",
            "delete", "pod", "optics-worker-abc", "--ignore-not-found", "--wait=false",
        ]]

    def test_is_alive_parses_phase(self):
        handle = WorkerHandle(id="optics-worker-abc", endpoint="http://10.42.3.9:8000")
        assert run(self._launcher(FakeRunner(results=[(0, "Running", "")])).is_alive(handle))
        assert run(self._launcher(FakeRunner(results=[(0, "Pending", "")])).is_alive(handle))
        assert not run(self._launcher(FakeRunner(results=[(0, "Succeeded", "")])).is_alive(handle))
        assert not run(self._launcher(FakeRunner(results=[(1, "", "NotFound")])).is_alive(handle))


@pytest.mark.skipif(
    os.environ.get("SUPERVISOR_DOCKER_TESTS") != "1",
    reason="live Docker test; set SUPERVISOR_DOCKER_TESTS=1 (Linux daemon whose "
    "bridge IPs are reachable from this host) to run",
)
class TestDockerLauncherLive:
    """Round-trip against a real Docker daemon.

    Uses SUPERVISOR_TEST_IMAGE (default traefik/whoami, which answers 200 on
    GET /) so no optics worker image is required to validate the launcher.
    """

    def test_launch_ready_stop(self):
        image = os.environ.get("SUPERVISOR_TEST_IMAGE", "traefik/whoami")
        launcher = DockerLauncher(image=image, network=None, resources="", port=80)

        async def scenario():
            handle = await launcher.launch()
            try:
                assert await launcher.wait_ready(handle, timeout_s=30)
                assert await launcher.is_alive(handle)
            finally:
                await launcher.stop(handle)
            assert not await launcher.is_alive(handle)

        run(scenario())


class TestCreateLauncher:
    def test_default_is_subprocess(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_LAUNCHER", raising=False)
        assert isinstance(create_launcher(), SubprocessLauncher)

    def test_explicit_subprocess(self):
        assert isinstance(create_launcher("subprocess"), SubprocessLauncher)

    def test_docker_selected(self):
        assert isinstance(create_launcher("docker", image="img", runner=FakeRunner()),
                          DockerLauncher)

    def test_k8s_selected(self):
        assert isinstance(create_launcher("k8s", image="img", runner=FakeRunner()),
                          K8sLauncher)

    def test_env_selection(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_LAUNCHER", "docker")
        monkeypatch.setenv("SUPERVISOR_WORKER_IMAGE", "img")
        assert isinstance(create_launcher(), DockerLauncher)

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="Unknown SUPERVISOR_LAUNCHER"):
            create_launcher("teleport")


if __name__ == "__main__":
    pytest.main([__file__])
