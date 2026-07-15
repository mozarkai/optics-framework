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
import signal
import socket
import subprocess  # nosec B404 - spawning local worker processes is this module's purpose
import sys
import uuid
from abc import ABC, abstractmethod
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


def create_launcher(kind: str | None = None, **kwargs: Any) -> WorkerLauncher:
    """Build the launcher selected by SUPERVISOR_LAUNCHER (subprocess default)."""
    kind = (kind or os.environ.get("SUPERVISOR_LAUNCHER", "subprocess")).strip().lower()
    if kind == "subprocess":
        return SubprocessLauncher(**kwargs)
    raise ValueError(f"Unknown SUPERVISOR_LAUNCHER: {kind!r} (expected 'subprocess')")
