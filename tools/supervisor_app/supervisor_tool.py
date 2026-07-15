#!/usr/bin/env python3
"""
Supervisor Tool for Optics Framework

This tool provides vertical scaling for the Optics Framework API by spawning multiple
worker processes and routing requests based on session affinity.

Usage:
    python supervisor_tool.py --workers 4 --base-port 9000 --host 127.0.0.1 --port 8000
"""

import atexit
import asyncio
import json
import logging
import signal
import subprocess  # nosec B404 - spawning local worker processes is this tool's purpose
import os
import sys
import time
from typing import Any, TextIO
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from dataclasses import dataclass

from launcher import WorkerHandle, WorkerLauncher, create_launcher, signal_process_group
from routing_store import InMemoryRoutingStore, RedisRoutingStore, RoutingStore

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Local process handles for the workers THIS supervisor spawned. Routing never
# reads this list — routes and the live-worker registry live in `store`.
workers: list[dict[str, Any]] = []  # List of {"port": int, "process": subprocess.Popen, "active": bool, "log_path": str | None, "log_fh": TextIO | None}

# ASGI app each worker runs. Overridable so the integration-test harness can
# point workers at a lightweight stub instead of the full optics API.
WORKER_APP = os.environ.get("SUPERVISOR_WORKER_APP", "optics_framework.common.expose_api:app")

# How long a worker registration stays live without a heartbeat.
WORKER_TTL_S = float(os.environ.get("SUPERVISOR_WORKER_TTL_S", "10"))

# Worker topology: "pool" (fixed pool, sessions share workers — default) or
# "per_session" (one worker launched per session, reaped via leases).
WORKER_MODE = os.environ.get("SUPERVISOR_WORKER_MODE", "pool").strip().lower()
if WORKER_MODE not in ("pool", "per_session"):
    raise ValueError(f"Unknown SUPERVISOR_WORKER_MODE: {WORKER_MODE!r} (expected 'pool' or 'per_session')")

# per_session knobs
STARTUP_TIMEOUT_S = float(os.environ.get("SUPERVISOR_STARTUP_TIMEOUT_S", "30"))
LEASE_TTL_S = float(os.environ.get("SUPERVISOR_LEASE_TTL_S", "120"))
REAP_INTERVAL_S = float(os.environ.get("SUPERVISOR_REAP_INTERVAL_S", "5"))

# Launcher for per_session mode; created lazily so pool mode never needs it.
launcher: WorkerLauncher | None = None

# Handles of session workers THIS replica launched (sid -> handle). A replica
# can only tear down subprocess workers it owns; remote-capable launchers can
# also stop workers by lease owner id (see _reap_expired_sessions).
session_workers: dict[str, WorkerHandle] = {}


def _create_store() -> RoutingStore:
    """Build the routing store selected by SUPERVISOR_STORE.

    - ``memory`` (default): single-supervisor, today's behavior.
    - ``redis``: shared store so any supervisor replica can route any
      session; needs SUPERVISOR_REDIS_URL (default redis://127.0.0.1:6379/0)
      and the optional redis dependency (optics-framework[supervisor]).
    """
    backend = os.environ.get("SUPERVISOR_STORE", "memory").strip().lower()
    if backend == "memory":
        return InMemoryRoutingStore()
    if backend == "redis":
        return RedisRoutingStore(os.environ.get("SUPERVISOR_REDIS_URL"))
    raise ValueError(f"Unknown SUPERVISOR_STORE backend: {backend!r} (expected 'memory' or 'redis')")


store: RoutingStore = _create_store()


def _endpoint_for_port(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _port_from_endpoint(endpoint: str) -> int:
    return int(endpoint.rsplit(":", 1)[1])


@dataclass
class SupervisorConfig:
    num_workers: int = 2
    base_port: int = 9000
    host: str = "127.0.0.1"
    port: int = 8000


# Default config so importing this module (e.g. `uvicorn supervisor_tool:app`)
# never hits an undefined name; `__main__` overrides it from CLI args.
config = SupervisorConfig()


# Monitoring/behavior flags
MONITOR_INTERVAL = 2
RESTART_WORKERS = False


# Shutdown/monitor coordination
shutdown_event = asyncio.Event()
monitor_task: asyncio.Task | None = None


def emergency_cleanup():
    """Best-effort cleanup on process exit to remove orphaned workers."""
    logger.info("Running emergency cleanup...")
    try:
        # Attempt to stop workers cleanly; if stop_workers uses lsof/kills, it'll do best-effort
        stop_workers()
    except Exception:
        logger.exception("Emergency cleanup encountered an error")


# Register emergency cleanup on exit
atexit.register(emergency_cleanup)

# Health check
@app.get("/health")
async def health_check():
    """Supervisor health check endpoint."""
    live_endpoints = store.list_live_workers()
    routes = store.list_routes()
    crashed_workers = [w["port"] for w in workers if not w["active"]]
    # per_session has no standing pool: an empty fleet is healthy (workers are
    # created on demand); an empty pool is not.
    healthy = bool(live_endpoints) or WORKER_MODE == "per_session"
    return {
        "status": "healthy" if healthy else "unhealthy",
        "active_workers": len(live_endpoints),
        "total_workers": len(workers),
        "crashed_workers": crashed_workers,
        "total_sessions": len(routes),
        "worker_mode": WORKER_MODE,
        "session_distribution": {
            _port_from_endpoint(endpoint): sum(1 for ep in routes.values() if ep == endpoint)
            for endpoint in live_endpoints
        },
    }

@app.get("/")
async def root():
    return {"message": "Optics Supervisor API"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage worker lifecycle."""
    logger.info("Starting Optics Supervisor (worker mode: %s)...", WORKER_MODE)
    # Reset shutdown event
    shutdown_event.clear()

    # Clear any existing local state; a fresh store instance drops nothing
    # shared (in-memory state is per-process anyway).
    global store, launcher
    store = _create_store()
    workers.clear()
    session_workers.clear()

    reaper_task: asyncio.Task | None = None
    if WORKER_MODE == "pool":
        start_workers()
    else:
        launcher = create_launcher()
        reaper_task = asyncio.create_task(reap_expired_sessions())

    # Start monitoring task
    global monitor_task
    monitor_task = asyncio.create_task(monitor_workers())

    try:
        yield
    finally:
        logger.info("Shutting down Optics Supervisor...")

        # Signal shutdown to monitoring/reaper tasks
        shutdown_event.set()

        for task in (monitor_task, reaper_task):
            if task and not task.done():
                task.cancel()

        # Stop all workers
        if WORKER_MODE == "pool":
            stop_workers()
        else:
            await stop_session_workers()

        logger.info("Optics Supervisor shutdown complete")

app.router.lifespan_context = lifespan

def start_workers():
    """Start worker processes."""
    for i in range(config.num_workers):
        port = config.base_port + i
        logger.info(f"Starting worker on port {port}")
        # Start optics serve process on the port
        # Prepare per-worker log file
        log_path = f"./worker_{port}.log"
        worker_process, log_fh = start_worker_process(port, log_path)
        if worker_process:
            workers.append({"port": port, "process": worker_process, "active": True, "log_path": log_path, "log_fh": log_fh})
            store.register_worker(_endpoint_for_port(port), WORKER_TTL_S)
            time.sleep(5)  # Increased delay to allow worker to fully start
        else:
            logger.error(f"Failed to start worker on port {port}")

    # Startup is serialized and can outlast WORKER_TTL_S with several workers;
    # refresh every registration before requests start flowing.
    _heartbeat_workers()

def _kill_orphans_on_port(port: int) -> None:
    """Best-effort kill of any process still listening on a worker port (POSIX-only)."""
    try:
        # Use lsof to find processes listening on the port and kill them
        result = subprocess.run(  # nosec B603 B607 - fixed argv, port is an int from config
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            for pid in result.stdout.strip().split("\n"):
                if pid.strip():
                    logger.info(f"Killing orphaned process {pid} on port {port}")
                    subprocess.run(["kill", "-9", pid.strip()], timeout=5)  # nosec B603 B607 - pids come from lsof output
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        pass  # lsof might not be available or no processes found


def _kill_worker(worker: dict[str, Any], grace_s: float = 2.0) -> None:
    """Tear down a single worker: SIGTERM its process group, escalate to SIGKILL,
    close its log handle, and reap anything still bound to its port.

    Isolated here so alternative launchers can override teardown without touching
    the pool logic.
    """
    port = worker["port"]
    proc = worker.get("process")
    if proc and proc.poll() is None:
        logger.info(f"Terminating worker process group on port {port} (pid={getattr(proc, 'pid', None)})")
        signal_process_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            logger.warning(f"Force killing worker process group on port {port}")
            signal_process_group(proc, signal.SIGKILL)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.error("Worker on port %s did not exit after SIGKILL", port)

    log_fh = worker.get("log_fh")
    if log_fh:
        try:
            log_fh.close()
        except OSError:
            logger.debug("Failed to close log handle for worker on port %s", port)

    _kill_orphans_on_port(port)


def stop_workers():
    """Stop all worker processes owned by this supervisor."""
    logger.info("Stopping all workers...")
    my_endpoints = {_endpoint_for_port(w["port"]) for w in workers}
    # Only this supervisor's workers and their routes are removed — a shared
    # store may hold routes owned by other replicas.
    for endpoint in my_endpoints:
        store.deregister_worker(endpoint)
    for session_id, endpoint in store.list_routes().items():
        if endpoint in my_endpoints:
            store.delete_route(session_id)
    for worker in workers:
        _kill_worker(worker)
    workers.clear()
    logger.info("All workers stopped")

async def monitor_workers():
    """Monitor worker processes and handle failures."""
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=MONITOR_INTERVAL)
            break  # Shutdown event was set
        except asyncio.TimeoutError:
            # Timeout reached, check worker health
            if WORKER_MODE == "pool":
                check_worker_health()
                _heartbeat_workers()
            else:
                await _heartbeat_session_workers()

    logger.info("Monitor task stopping due to shutdown signal")


def _heartbeat_workers():
    """Refresh the store TTL of every locally-owned live pool worker."""
    for worker in workers:
        if worker["active"]:
            store.heartbeat_worker(_endpoint_for_port(worker["port"]), WORKER_TTL_S)


async def _heartbeat_session_workers():
    """Refresh the store TTL of every locally-owned live session worker.

    A dead worker is deliberately NOT cleaned up here: its route stays until
    the lease reaper claims it, and requests in between get a 502 from the
    failed forward — a crashed worker means that session is lost.
    # NOTE: mid-session worker recovery (relaunch + reattach to a still-live
    # backend session) would hook in here, gated on the driver capability
    # contract from the stateless API design doc. Out of scope for now.
    """
    if launcher is None:
        return
    for handle in list(session_workers.values()):
        if await launcher.is_alive(handle):
            store.heartbeat_worker(handle.endpoint, WORKER_TTL_S)


async def reap_expired_sessions():
    """Reclaim workers of sessions whose lease expired, and locally-owned
    workers whose route was removed by another replica."""
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=REAP_INTERVAL_S)
            break
        except asyncio.TimeoutError:
            try:
                await _reap_once()
            except Exception:
                logger.exception("Reaper sweep failed; will retry")

    logger.info("Reaper task stopping due to shutdown signal")


async def _reap_once():
    if launcher is None:
        return
    for session_id, owner in store.expired_leases():
        logger.info(f"Reaping expired session {session_id} (owner={owner})")
        handle = session_workers.pop(session_id, None)
        if handle is None and owner:
            # Not launched here: reconstruct the handle so remote-capable
            # launchers (containers/pods) can stop it from any replica; the
            # subprocess launcher treats unknown handles as a no-op and the
            # owning replica's reaper cleans the process up.
            endpoint = store.get_route(session_id)
            if endpoint:
                handle = WorkerHandle(id=owner, endpoint=endpoint)
        if handle:
            await launcher.stop(handle)
            store.deregister_worker(handle.endpoint)
        store.delete_route(session_id)
        store.release_lease(session_id)

    # Sessions stopped through another replica: the route (and lease) are
    # gone, but the subprocess is ours to kill.
    for session_id, handle in list(session_workers.items()):
        if store.get_route(session_id) is None:
            logger.info(f"Reaping session {session_id}: route removed elsewhere")
            session_workers.pop(session_id, None)
            await launcher.stop(handle)
            store.deregister_worker(handle.endpoint)


async def stop_session_workers():
    """Tear down every session worker this replica launched (shutdown path)."""
    if launcher is None:
        return
    for session_id, handle in list(session_workers.items()):
        session_workers.pop(session_id, None)
        store.deregister_worker(handle.endpoint)
        store.delete_route(session_id)
        store.release_lease(session_id)
        await launcher.stop(handle)

def check_worker_health():
    """Check health of all workers and handle failures."""
    crashed_workers = []

    for worker in workers:
        if not worker["active"]:
            continue

        port = worker["port"]
        if worker["process"].poll() is not None:  # Process has terminated
            logger.error(f"Worker on port {port} has crashed (exit code: {worker['process'].poll()})")
            worker["active"] = False
            crashed_workers.append(port)

            # Remove the dead worker from the registry and its sessions' routes
            endpoint = _endpoint_for_port(port)
            store.deregister_worker(endpoint)
            affected_sessions = [sid for sid, ep in store.list_routes().items() if ep == endpoint]
            for session_id in affected_sessions:
                store.delete_route(session_id)
                logger.info(f"Cleaned up session {session_id} due to worker crash on port {port}")

            # Optional: Restart worker (with delay to avoid rapid restart loops)
            if RESTART_WORKERS:
                logger.info(f"Waiting before restarting worker on port {port}")
                time.sleep(5)  # Wait 5 seconds before restart
                logger.info(f"Attempting to restart worker on port {port}")
                old_fh = worker.get("log_fh")
                if old_fh:
                    try:
                        old_fh.close()
                    except OSError:
                        logger.debug("Failed to close old log handle for worker on port %s", port)
                new_process, new_log_fh = start_worker_process(port, worker.get("log_path"))
                if new_process:
                    worker["process"] = new_process
                    worker["log_fh"] = new_log_fh
                    worker["active"] = True
                    store.register_worker(_endpoint_for_port(port), WORKER_TTL_S)
                    logger.info(f"Restarted worker on port {port}")
                else:
                    logger.error(f"Failed to restart worker on port {port}")

    if crashed_workers:
        logger.warning(f"Crashed workers: {crashed_workers}. Active workers: {len([w for w in workers if w['active']])}")

def start_worker_process(port: int, log_path: str | None = None) -> tuple[subprocess.Popen | None, TextIO | None]:
    """Start a single worker process running the optics API and redirect output to log_path.

    Returns a tuple of (process, log_file_handle).
    """
    log_fh = None
    try:
        # Use uvicorn directly to run the optics API
        cmd = [
            sys.executable, "-m", "uvicorn",
            WORKER_APP,
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "info"
        ]
        logger.info(f"Starting worker with command: {' '.join(cmd)}")
        if log_path:
            log_fh = open(log_path, "a", encoding="utf-8", errors="replace")

        # start_new_session gives each worker its own process group, so the
        # killpg-based teardown in _kill_worker targets only that worker and
        # never the supervisor's own group.
        process = subprocess.Popen(  # nosec B603 - argv is sys.executable plus validated config values
            cmd,
            stdout=log_fh,
            stderr=log_fh,
            text=True,
            start_new_session=True,
        )

        # Give it a moment to start
        time.sleep(2)
        # Check if it's still running
        if process.poll() is None:
            logger.info(f"Worker on port {port} started successfully")
            return process, log_fh
        logger.error(f"Worker on port {port} exited immediately with code {process.poll()}")
        if log_fh:
            log_fh.close()
        return None, None
    except OSError as e:
        logger.error(f"Failed to start worker process on port {port}: {e}")
        if log_fh:
            try:
                log_fh.close()
            except OSError:
                logger.debug("Failed to close log handle for worker on port %s", port)
        return None, None

def select_worker_for_session(session_id: str) -> str | None:
    """Return the endpoint owning a session; assign a new worker if unrouted."""
    endpoint = store.get_route(session_id)
    if endpoint:
        return endpoint
    # New session - assign to next worker
    endpoint = store.next_worker()
    if endpoint:
        store.put_route(session_id, endpoint)
    return endpoint

async def forward_request(method: str, url: str, headers: dict[str, str], body: bytes | None = None) -> httpx.Response:
    """Forward request to worker and return httpx response.

    Increased timeout and improved logging to aid diagnosing slow or failing upstream workers.
    """
    # Use a slightly longer timeout to accommodate slow startup or heavy operations on workers
    timeout_seconds = 90.0
    async with httpx.AsyncClient() as client:
        try:
            logger.debug(f"Forwarding {method} request to {url} with timeout={timeout_seconds}")
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                content=body,
                timeout=timeout_seconds
            )
            logger.debug(f"Received response from {url}: {response.status_code}")
            return response
        except httpx.ReadTimeout as e:
            logger.error(f"Read timeout when forwarding request to {url}: {e}")
            return httpx.Response(504, content=b"Gateway Timeout")
        except httpx.RequestError as e:
            logger.exception(f"Request forwarding failed for {url}: {e}")
            return httpx.Response(502, content=b"Bad Gateway")

def convert_httpx_to_fastapi_response(httpx_response: httpx.Response) -> Response:
    """Convert httpx.Response to FastAPI Response."""
    return Response(
        content=httpx_response.content,
        status_code=httpx_response.status_code,
        headers=dict(httpx_response.headers)
    )

def _extract_session_id_from_body(httpx_response: httpx.Response) -> str | None:
    """Pull session_id out of a successful create-session response body."""
    try:
        session_data = json.loads(httpx_response.content.decode("utf-8"))
        return session_data.get("session_id")
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as e:
        logger.warning(f"Failed to extract session_id from response: {e}")
        return None


# Session management endpoints
@app.post("/v1/sessions/start")
async def create_session(request: Request):
    """Create a new session by forwarding to a worker."""
    if WORKER_MODE == "per_session":
        return await _create_session_per_session(request)

    endpoint = store.next_worker()
    if not endpoint:
        return Response(content=b"No workers available", status_code=503)

    worker_url = f"{endpoint}/v1/sessions/start"

    # Forward the request
    body_bytes = await request.body()
    httpx_response = await forward_request(
        method=request.method,
        url=worker_url,
        headers=dict(request.headers),
        body=body_bytes
    )

    # If backend returned a 5xx we log details to help debugging
    if 500 <= httpx_response.status_code < 600:
        logger.error(
            "Worker %s returned error %s for /v1/sessions/start. Request size=%d bytes. Response body=%s",
            worker_url,
            httpx_response.status_code,
            len(body_bytes) if body_bytes is not None else 0,
            (httpx_response.content.decode('utf-8', errors='replace')[:200] if httpx_response.content else "<empty>")
        )

    # The worker mints the session_id, so the route can only be written after
    # a successful forward — never before.
    if httpx_response.status_code == 200:
        session_id = _extract_session_id_from_body(httpx_response)
        if session_id:
            store.put_route(session_id, endpoint)
            logger.info(f"Mapped session {session_id} to worker at {endpoint}")

    return convert_httpx_to_fastapi_response(httpx_response)


async def _create_session_per_session(request: Request):
    """per_session mode: launch a dedicated worker, then forward the create."""
    handle = await launcher.launch()
    if not await launcher.wait_ready(handle, timeout_s=STARTUP_TIMEOUT_S):
        logger.error(f"Session worker {handle.id} failed to become ready within {STARTUP_TIMEOUT_S}s")
        await launcher.stop(handle)
        return Response(content=b"worker failed to start", status_code=503)

    body_bytes = await request.body()
    httpx_response = await forward_request(
        method="POST",
        url=f"{handle.endpoint}/v1/sessions/start",
        headers=dict(request.headers),
        body=body_bytes,
    )

    session_id = _extract_session_id_from_body(httpx_response) if httpx_response.status_code == 200 else None
    if session_id:
        store.put_route(session_id, handle.endpoint)
        store.acquire_lease(session_id, owner=handle.id, ttl_s=LEASE_TTL_S)
        store.register_worker(handle.endpoint, WORKER_TTL_S)
        session_workers[session_id] = handle
        logger.info(f"Session {session_id} owns worker {handle.id} at {handle.endpoint}")
    else:
        # Session creation failed — don't leak the worker.
        logger.error(
            "Worker %s returned %s for /v1/sessions/start; tearing it down. Response body=%s",
            handle.endpoint,
            httpx_response.status_code,
            (httpx_response.content.decode("utf-8", errors="replace")[:200] if httpx_response.content else "<empty>"),
        )
        await launcher.stop(handle)

    return convert_httpx_to_fastapi_response(httpx_response)

# Generic endpoint forwarder
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def forward_to_worker(path: str, request: Request):
    """Forward requests to appropriate worker based on session_id in path."""
    # Extract session_id from path if present
    session_id = extract_session_id_from_path(path)

    if session_id:
        if WORKER_MODE == "per_session":
            # Workers are session-owned: never bind an unknown session to
            # someone else's worker.
            endpoint = store.get_route(session_id)
            if endpoint:
                # Traffic keeps the session alive.
                store.renew_lease(session_id, LEASE_TTL_S)
        else:
            endpoint = select_worker_for_session(session_id)
        if not endpoint:
            return Response(content=b"No worker available for session", status_code=503)
    else:
        # For non-session endpoints, use round-robin
        endpoint = store.next_worker()
        if not endpoint:
            return Response(content=b"No workers available", status_code=503)

    worker_url = f"{endpoint}/{path}"

    # Forward the request
    httpx_response = await forward_request(
        method=request.method,
        url=worker_url,
        headers=dict(request.headers),
        body=await request.body()
    )

    # A successfully-stopped session no longer needs its route
    # (the worker's stop endpoint is DELETE /v1/sessions/{id}/stop).
    if session_id and path.rstrip("/").endswith("/stop") and 200 <= httpx_response.status_code < 300:
        store.delete_route(session_id)
        logger.info(f"Removed route for terminated session {session_id}")
        if WORKER_MODE == "per_session":
            store.release_lease(session_id)
            handle = session_workers.pop(session_id, None)
            if handle:
                # The worker already quit its driver via the forwarded stop;
                # now retire the process itself.
                store.deregister_worker(handle.endpoint)
                await launcher.stop(handle)
            # else: launched by another replica; its reaper notices the
            # missing route and cleans the process up.

    return convert_httpx_to_fastapi_response(httpx_response)

def extract_session_id_from_path(path: str) -> str | None:
    """Extract session_id from URL path if present."""
    if "/sessions/" not in path and "/session/" not in path:
        return None

    parts = path.split("/")
    for i, part in enumerate(parts):
        if part in ("sessions", "session") and i + 1 < len(parts):
            potential_id = parts[i + 1]
            # Check if it looks like a UUID (basic validation)
            if len(potential_id) == 36 and potential_id.count("-") == 4:
                return potential_id
    return None

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Optics Supervisor Tool")
    parser.add_argument("--workers", type=int, default=2, help="Number of worker processes")
    parser.add_argument("--base-port", type=int, default=9000, help="Base port for workers")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Supervisor host")
    parser.add_argument("--port", type=int, default=8000, help="Supervisor port")

    args = parser.parse_args()
    config = SupervisorConfig(
        num_workers=args.workers,
        base_port=args.base_port,
        host=args.host,
        port=args.port
    )

    logger.info(f"Starting supervisor with {config.num_workers} workers on ports {config.base_port}-{config.base_port + config.num_workers - 1}")
    logger.info(f"Supervisor listening on {config.host}:{config.port}")

    uvicorn.run(app, host=config.host, port=config.port)
