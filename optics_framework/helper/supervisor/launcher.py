#!/usr/bin/env python3
"""Worker launchers for the Optics supervisor.

A WorkerLauncher creates and destroys one worker per session. The supervisor
only ever talks to a worker through its WorkerHandle.endpoint URL, so
subprocess, container, and pod launchers are interchangeable behind this
interface.
"""

import asyncio
import logging
import os
import shlex
import signal
import socket
import subprocess  # nosec B404 - spawning local worker processes is this module's purpose
import sys
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TextIO

import httpx

logger = logging.getLogger(__name__)

# ASGI app each worker runs (same override the pool path honors).
WORKER_APP = os.environ.get("SUPERVISOR_WORKER_APP", "optics_framework.common.expose_api:app")


@dataclass
class WorkerHandle:
    id: str
    endpoint: str  # "http://host:port"


class WorkerLauncher(ABC):
    """Lifecycle contract for one-worker-per-session backends."""

    @abstractmethod
    async def launch(self) -> "WorkerHandle":
        """Start a worker and return its handle. Does not wait for readiness."""

    @abstractmethod
    async def wait_ready(self, handle: WorkerHandle, timeout_s: float) -> bool:
        """Poll the worker's health endpoint until it answers or time runs out.

        The optics worker app answers health checks on GET / (expose_api has
        no /health route).
        """

    @abstractmethod
    async def stop(self, handle: WorkerHandle) -> None:
        """Tear the worker down. Must be idempotent and safe for unknown handles."""

    @abstractmethod
    async def is_alive(self, handle: WorkerHandle) -> bool:
        """Whether the worker still runs (process/container liveness, not HTTP)."""


async def _poll_health(endpoint: str, timeout_s: float, interval_s: float = 0.25) -> bool:
    """Shared wait_ready implementation: poll GET {endpoint}/ until 200."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    async with httpx.AsyncClient() as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                response = await client.get(f"{endpoint}/", timeout=2.0)
                if response.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(interval_s)
    return False


def _pick_free_port(host: str = "127.0.0.1") -> int:
    """Ask the OS for an ephemeral port. Small launch race window is fine —
    a worker that loses it fails wait_ready and is stopped."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def signal_process_group(proc: subprocess.Popen, sig: signal.Signals) -> None:
    """Signal a worker's own process group, falling back to the single process."""
    pid = getattr(proc, "pid", None)
    try:
        if pid is not None:
            os.killpg(os.getpgid(int(pid)), sig)
        else:
            proc.send_signal(sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.send_signal(sig)
        except (ProcessLookupError, OSError):
            logger.debug("Worker pid %s already gone", pid)


class SubprocessLauncher(WorkerLauncher):
    """One `uvicorn <worker app>` per session on an ephemeral local port."""

    def __init__(self, host: str = "127.0.0.1", worker_app: str | None = None,
                 log_dir: str | None = None) -> None:
        self._host = host
        self._worker_app = worker_app or WORKER_APP
        self._log_dir = log_dir
        self._procs: dict[str, tuple[subprocess.Popen, TextIO | None]] = {}

    async def launch(self) -> WorkerHandle:
        port = _pick_free_port(self._host)
        handle = WorkerHandle(id=f"subproc-{port}-{uuid.uuid4().hex[:8]}",
                              endpoint=f"http://{self._host}:{port}")
        cmd = [
            sys.executable, "-m", "uvicorn",
            self._worker_app,
            "--host", self._host,
            "--port", str(port),
            "--log-level", "info",
        ]
        log_fh: TextIO | None = None
        if self._log_dir:
            log_fh = open(os.path.join(self._log_dir, f"worker_{handle.id}.log"),
                          "a", encoding="utf-8", errors="replace")
        logger.info("Launching session worker %s: %s", handle.id, " ".join(cmd))
        # start_new_session so teardown of the worker's process group never
        # touches the supervisor's own group.
        proc = subprocess.Popen(  # nosec B603 - argv is sys.executable plus internal values
            cmd,
            stdout=log_fh,
            stderr=log_fh,
            text=True,
            start_new_session=True,
        )
        self._procs[handle.id] = (proc, log_fh)
        return handle

    async def wait_ready(self, handle: WorkerHandle, timeout_s: float) -> bool:
        record = self._procs.get(handle.id)
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            if record and record[0].poll() is not None:
                logger.error("Session worker %s exited during startup (code %s)",
                             handle.id, record[0].poll())
                return False
            if await _poll_health(handle.endpoint, timeout_s=0.5):
                return True
        return False

    async def stop(self, handle: WorkerHandle) -> None:
        record = self._procs.pop(handle.id, None)
        if record is None:
            # Unknown handle: launched by another replica; a subprocess can
            # only be stopped by its parent, so its owner's reaper gets it.
            logger.debug("No local process for worker %s; skipping stop", handle.id)
            return
        proc, log_fh = record
        if proc.poll() is None:
            signal_process_group(proc, signal.SIGTERM)
            for _ in range(50):
                if proc.poll() is not None:
                    break
                await asyncio.sleep(0.1)
            if proc.poll() is None:
                logger.warning("Force killing session worker %s", handle.id)
                signal_process_group(proc, signal.SIGKILL)
                await asyncio.to_thread(proc.wait, 5)
        if log_fh:
            try:
                log_fh.close()
            except OSError:
                logger.debug("Failed to close log handle for worker %s", handle.id)
        logger.info("Session worker %s stopped", handle.id)

    async def is_alive(self, handle: WorkerHandle) -> bool:
        record = self._procs.get(handle.id)
        return record is not None and record[0].poll() is None

    def owned_handle_ids(self) -> list[str]:
        return list(self._procs)


# (returncode, stdout, stderr) of one CLI invocation
CommandRunner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]


async def _run_cli(cmd: list[str]) -> tuple[int, str, str]:
    """Default CommandRunner: run a CLI tool and capture its output."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


class WorkerLaunchError(RuntimeError):
    """A launcher could not create a worker."""


class DockerLauncher(WorkerLauncher):
    """One container per session; WorkerHandle.endpoint is the container's
    subnet IP, so sessions spread across any host reachable on that network.

    Speaks the `docker` CLI through an injectable runner (no docker SDK
    dependency); the handle id is the container name, so ANY replica can stop
    a worker it did not launch.
    """

    def __init__(
        self,
        image: str | None = None,
        network: str | None = None,
        resources: str | None = None,
        port: int | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self._image = image or os.environ.get("SUPERVISOR_WORKER_IMAGE", "")
        if not self._image:
            raise ValueError("DockerLauncher requires SUPERVISOR_WORKER_IMAGE")
        self._network = network if network is not None else os.environ.get("SUPERVISOR_WORKER_NETWORK")
        # Extra `docker run` args, e.g. "--memory=1g --cpus=1"
        self._resources = shlex.split(
            resources if resources is not None else os.environ.get("SUPERVISOR_WORKER_RESOURCES", "")
        )
        self._port = port if port is not None else int(os.environ.get("SUPERVISOR_WORKER_PORT", "8000"))
        self._run = runner or _run_cli

    async def launch(self) -> WorkerHandle:
        # NOTE: placement is delegated to the container scheduler. A
        # DeviceRegistry-backed launcher (device-aware scheduling, design doc
        # Layer 3) would slot in here by picking image/env per free device.
        name = f"optics-worker-{uuid.uuid4().hex[:12]}"
        cmd = ["docker", "run", "--detach", "--name", name]
        if self._network:
            cmd += ["--network", self._network]
        cmd += self._resources
        cmd.append(self._image)
        code, _, stderr = await self._run(cmd)
        if code != 0:
            raise WorkerLaunchError(f"docker run failed for {name}: {stderr.strip()}")

        code, stdout, stderr = await self._run([
            "docker", "inspect", "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name,
        ])
        ip = stdout.strip()
        if code != 0 or not ip:
            await self.stop(WorkerHandle(id=name, endpoint=""))
            raise WorkerLaunchError(f"could not resolve subnet IP for {name}: {stderr.strip()}")
        # NOTE: supervisor<->worker auth (shared token / mTLS) would be
        # injected here as container env; workers must only ever bind the
        # subnet interface, with the supervisor as the sole ingress.
        return WorkerHandle(id=name, endpoint=f"http://{ip}:{self._port}")

    async def wait_ready(self, handle: WorkerHandle, timeout_s: float) -> bool:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            if not await self.is_alive(handle):
                logger.error("Container %s exited during startup", handle.id)
                return False
            if await _poll_health(handle.endpoint, timeout_s=0.5):
                return True
        return False

    async def stop(self, handle: WorkerHandle) -> None:
        code, _, stderr = await self._run(["docker", "rm", "--force", handle.id])
        if code != 0 and "No such container" not in stderr:
            logger.warning("docker rm %s failed: %s", handle.id, stderr.strip())

    async def is_alive(self, handle: WorkerHandle) -> bool:
        code, stdout, _ = await self._run([
            "docker", "inspect", "-f", "{{.State.Running}}", handle.id,
        ])
        return code == 0 and stdout.strip() == "true"


class K8sLauncher(WorkerLauncher):
    """One Pod per session; WorkerHandle.endpoint is the pod IP.

    Speaks `kubectl` through the same injectable runner. Node placement is
    the scheduler's job; the handle id is the pod name, so any replica can
    delete it.
    """

    def __init__(
        self,
        image: str | None = None,
        namespace: str | None = None,
        port: int | None = None,
        pod_deadline_s: int | None = None,
        runner: CommandRunner | None = None,
        ip_timeout_s: float = 30.0,
    ) -> None:
        self._image = image or os.environ.get("SUPERVISOR_WORKER_IMAGE", "")
        if not self._image:
            raise ValueError("K8sLauncher requires SUPERVISOR_WORKER_IMAGE")
        self._namespace = namespace or os.environ.get("SUPERVISOR_WORKER_NAMESPACE", "default")
        self._port = port if port is not None else int(os.environ.get("SUPERVISOR_WORKER_PORT", "8000"))
        # Orchestrator-side backstop TTL: the pod dies even if every reaper
        # misses it (two independent timers, no single failure leaks pods).
        self._pod_deadline_s = pod_deadline_s if pod_deadline_s is not None else int(
            os.environ.get("SUPERVISOR_WORKER_DEADLINE_S", "0")
        )
        self._run = runner or _run_cli
        self._ip_timeout_s = ip_timeout_s

    def _kubectl(self, *args: str) -> list[str]:
        return ["kubectl", "--namespace", self._namespace, *args]

    async def launch(self) -> WorkerHandle:
        # NOTE: placement is delegated to the Kubernetes scheduler. A
        # DeviceRegistry-backed launcher (design doc Layer 3) would slot in
        # here via node selectors / affinity per free device.
        name = f"optics-worker-{uuid.uuid4().hex[:12]}"
        cmd = self._kubectl(
            "run", name,
            f"--image={self._image}",
            "--restart=Never",
            f"--port={self._port}",
            "--labels=app=optics-session-worker",
        )
        if self._pod_deadline_s > 0:
            cmd.append(f"--overrides={{\"spec\": {{\"activeDeadlineSeconds\": {self._pod_deadline_s}}}}}")
        code, _, stderr = await self._run(cmd)
        if code != 0:
            raise WorkerLaunchError(f"kubectl run failed for {name}: {stderr.strip()}")

        # Pod IPs are assigned asynchronously by the scheduler/CNI.
        deadline = asyncio.get_event_loop().time() + self._ip_timeout_s
        while asyncio.get_event_loop().time() < deadline:
            code, stdout, _ = await self._run(
                self._kubectl("get", "pod", name, "-o", "jsonpath={.status.podIP}")
            )
            ip = stdout.strip()
            if code == 0 and ip:
                # NOTE: supervisor<->worker auth (shared token / mTLS) is a
                # follow-up; keep worker pods on the cluster network only.
                return WorkerHandle(id=name, endpoint=f"http://{ip}:{self._port}")
            await asyncio.sleep(0.5)

        await self.stop(WorkerHandle(id=name, endpoint=""))
        raise WorkerLaunchError(f"pod {name} got no IP within {self._ip_timeout_s}s")

    async def wait_ready(self, handle: WorkerHandle, timeout_s: float) -> bool:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            if not await self.is_alive(handle):
                logger.error("Pod %s died during startup", handle.id)
                return False
            if await _poll_health(handle.endpoint, timeout_s=0.5):
                return True
        return False

    async def stop(self, handle: WorkerHandle) -> None:
        code, _, stderr = await self._run(
            self._kubectl("delete", "pod", handle.id, "--ignore-not-found", "--wait=false")
        )
        if code != 0:
            logger.warning("kubectl delete pod %s failed: %s", handle.id, stderr.strip())

    async def is_alive(self, handle: WorkerHandle) -> bool:
        code, stdout, _ = await self._run(
            self._kubectl("get", "pod", handle.id, "-o", "jsonpath={.status.phase}")
        )
        return code == 0 and stdout.strip() in ("Pending", "Running")


def create_launcher(kind: str | None = None, **kwargs: Any) -> WorkerLauncher:
    """Build the launcher selected by SUPERVISOR_LAUNCHER (subprocess default)."""
    kind = (kind or os.environ.get("SUPERVISOR_LAUNCHER", "subprocess")).strip().lower()
    if kind == "subprocess":
        return SubprocessLauncher(**kwargs)
    if kind == "docker":
        return DockerLauncher(**kwargs)
    if kind == "k8s":
        return K8sLauncher(**kwargs)
    raise ValueError(f"Unknown SUPERVISOR_LAUNCHER: {kind!r} (expected 'subprocess', 'docker', or 'k8s')")
