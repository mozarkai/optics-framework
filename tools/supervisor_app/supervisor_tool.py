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
import subprocess #nosec
import os
import time
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from dataclasses import dataclass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Worker management


# FastAPI app
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Worker management
workers: List[Dict[str, Any]] = []  # List of {"port": int, "process": subprocess.Popen, "active": bool}
session_map: Dict[str, int] = {}  # session_id -> worker_port
worker_index = 0  # For round-robin selection


@dataclass
class SupervisorConfig:
    num_workers: int = 2
    base_port: int = 9000
    host: str = "127.0.0.1"
    port: int = 8000


# Monitoring/behavior flags
MONITOR_INTERVAL = 2
RESTART_WORKERS = False


# Shutdown/monitor coordination
shutdown_event = asyncio.Event()
monitor_task: Optional[asyncio.Task] = None


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
    active_workers = [w for w in workers if w["active"]]
    crashed_workers = [w["port"] for w in workers if not w["active"]]
    return {
        "status": "healthy" if active_workers else "unhealthy",
        "active_workers": len(active_workers),
        "total_workers": len(workers),
        "crashed_workers": crashed_workers,
        "total_sessions": len(session_map),
        "session_distribution": {port: len([s for s, p in session_map.items() if p == port]) for port in [w["port"] for w in workers]}
    }

# Placeholder endpoints - will be implemented with forwarding
@app.get("/")
async def root():
    return {"message": "Optics Supervisor API"}

# Placeholder endpoints - will be implemented with forwarding logic

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage worker lifecycle."""
    logger.info("Starting Optics Supervisor...")
    # Reset shutdown event
    shutdown_event.clear()

    # Clear any existing state
    session_map.clear()
    workers.clear()
    start_workers()

    # Start monitoring task
    global monitor_task
    monitor_task = asyncio.create_task(monitor_workers())

    try:
        yield
    finally:
        logger.info("Shutting down Optics Supervisor...")

        # Signal shutdown to monitoring task
        shutdown_event.set()

        # Stop monitoring task
        if monitor_task and not monitor_task.done():
            monitor_task.cancel()

        # Stop all workers
        stop_workers()

        logger.info("Optics Supervisor shutdown complete")

app.router.lifespan_context = lifespan

def start_workers():
    """Start worker processes."""
    global workers
    for i in range(config.num_workers):
        port = config.base_port + i
        logger.info(f"Starting worker on port {port}")
        # Start optics serve process on the port
        # Prepare per-worker log file
        log_path = f"./worker_{port}.log"
        worker_process, log_fh = start_worker_process(port, log_path)
        if worker_process:
            workers.append({"port": port, "process": worker_process, "active": True, "log_path": log_path, "log_fh": log_fh})
            time.sleep(5)  # Increased delay to allow worker to fully start
        else:
            logger.error(f"Failed to start worker on port {port}")

def stop_workers():
    """Stop all worker processes."""
    global workers, session_map
    logger.info("Stopping all workers...")
    # First, try graceful termination by signalling the worker process group
    for worker in workers:
        proc = worker.get("process")
        if proc and proc.poll() is None:
            pid = getattr(proc, "pid", None)
            logger.info(f"Terminating worker process group on port {worker['port']} (pid={pid})")
            try:
                if pid is not None:
                    pgid = os.getpgid(int(pid))
                    os.killpg(pgid, signal.SIGTERM)
                else:
                    proc.terminate()
            except Exception:
                # Fallback to terminating the single process
                try:
                    proc.terminate()
                except Exception:
                    logger.exception("Failed to terminate worker pid %s", pid)

    # Wait for processes to terminate gracefully
    time.sleep(2)

    # Force kill any remaining processes (SIGKILL to process groups)
    for worker in workers:
        proc = worker.get("process")
        if proc and proc.poll() is None:
            pid = getattr(proc, "pid", None)
            logger.warning(f"Force killing worker process group on port {worker['port']} (pid={pid})")
            try:
                if pid is not None:
                    pgid = os.getpgid(int(pid))
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    proc.kill()
                    proc.wait(timeout=5)
            except Exception:
                # Fallback to killing single process
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    logger.exception("Failed to force-kill worker pid %s", pid)
        # Close log file handle if present
        try:
            if worker.get("log_fh"):
                worker["log_fh"].close()
        except Exception:
            logger.error("Failed to close log file handle for worker on port %s", worker['port'])
            pass

    # Also try to kill any orphaned worker processes by port
    for worker in workers:
        port = worker["port"]
        try:
            # Use lsof to find processes listening on the port and kill them
            result = subprocess.run( #nosec
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    if pid.strip():
                        logger.info(f"Killing orphaned process {pid} on port {port}")
                        subprocess.run(["kill", "-9", pid.strip()], timeout=5, check=False) #nosec
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            pass  # lsof might not be available or no processes found

    workers.clear()
    session_map.clear()
    logger.info("All workers stopped")

async def monitor_workers():
    """Monitor worker processes and handle failures."""
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=MONITOR_INTERVAL)
            break  # Shutdown event was set
        except asyncio.TimeoutError:
            # Timeout reached, check worker health
            check_worker_health()

    logger.info("Monitor task stopping due to shutdown signal")

def check_worker_health():
    """Check health of all workers and handle failures."""
    global workers, session_map
    crashed_workers = []

    for worker in workers:
        if not worker["active"]:
            continue

        port = worker["port"]
        if worker["process"].poll() is not None:  # Process has terminated
            logger.error(f"Worker on port {port} has crashed (exit code: {worker['process'].poll()})")
            worker["active"] = False
            crashed_workers.append(port)

            # Clean up sessions for this worker
            affected_sessions = [sid for sid, wport in session_map.items() if wport == port]
            for session_id in affected_sessions:
                del session_map[session_id]
                logger.info(f"Cleaned up session {session_id} due to worker crash on port {port}")

            # Optional: Restart worker (with delay to avoid rapid restart loops)
            if RESTART_WORKERS:
                logger.info(f"Waiting before restarting worker on port {port}")
                time.sleep(5)  # Wait 5 seconds before restart
                logger.info(f"Attempting to restart worker on port {port}")
                new_process = start_worker_process(port)
                if new_process:
                    worker["process"] = new_process
                    worker["active"] = True
                    logger.info(f"Restarted worker on port {port}")
                else:
                    logger.error(f"Failed to restart worker on port {port}")

    if crashed_workers:
        logger.warning(f"Crashed workers: {crashed_workers}. Active workers: {len([w for w in workers if w['active']])}")

def start_worker_process(port: int, log_path: Optional[str] = None) -> tuple[Optional[subprocess.Popen], Optional[object]]:
    """Start a single worker process running optics serve and redirect output to log_path.

    Returns a tuple of (process, log_file_handle).
    """
    log_fh = None
    try:
        # Use optics directly to run the optics API
        cmd = [
            "optics",
            "serve",
            "--host", "127.0.0.1",
            "--port", str(port),
        ]
        logger.info(f"Starting worker with command: {' '.join(cmd)}")
        log_fh = None
        if log_path:
            log_fh = open(log_path, "a", encoding="utf-8", errors="replace")
            process = subprocess.Popen( #nosec
                cmd,
                stdout=log_fh,
                stderr=log_fh,
                text=True
            )
        else:
            process = subprocess.Popen(cmd, text=True) # nosec

        # Give it a moment to start
        time.sleep(2)
        # Check if it's still running
        if process.poll() is None:
            logger.info(f"Worker on port {port} started successfully")
            return process, log_fh
        else:
            logger.error(f"Worker on port {port} exited immediately with code {process.poll()}")
            if log_fh:
                log_fh.close()
            return None, None
    except Exception as e:
        logger.error(f"Failed to start worker process on port {port}: {e}")
        try:
            if log_fh:
                log_fh.close()
        except Exception:
            logger.error("Failed to close log file handle for worker on port %s", port)
            pass
        return None, None

def get_next_worker_port() -> Optional[int]:
    """Get next available worker port using round-robin."""
    global worker_index
    active_workers = [w for w in workers if w["active"]]
    if not active_workers:
        return None

    port = active_workers[worker_index % len(active_workers)]["port"]
    worker_index += 1
    return port

def select_worker_for_session(session_id: str) -> Optional[int]:
    """Select worker for a session. If session exists, return its worker; otherwise, assign new."""
    if session_id in session_map:
        return session_map[session_id]
    else:
        # New session - assign to next worker
        port = get_next_worker_port()
        if port:
            session_map[session_id] = port
        return port

async def forward_request(method: str, url: str, headers: Dict[str, str], body: Optional[bytes] = None) -> httpx.Response:
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

# Session management endpoints
@app.post("/v1/sessions/start")
async def create_session(request: Request):
    """Create a new session by forwarding to a worker."""
    port = get_next_worker_port()
    if not port:
        return Response(content=b"No workers available", status_code=503)

    worker_url = f"http://127.0.0.1:{port}/v1/sessions/start"

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

    # If successful, extract session_id and store mapping
    if httpx_response.status_code == 200:
        try:
            response_data = httpx_response.content.decode('utf-8')
            session_data = json.loads(response_data)
            session_id = session_data.get("session_id")
            if session_id:
                session_map[session_id] = port
                logger.info(f"Mapped session {session_id} to worker on port {port}")
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to extract session_id from response: {e}")

    return convert_httpx_to_fastapi_response(httpx_response)

# Generic endpoint forwarder
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def forward_to_worker(path: str, request: Request):
    """Forward requests to appropriate worker based on session_id in path."""
    # Extract session_id from path if present
    session_id = extract_session_id_from_path(path)

    if session_id:
        port = select_worker_for_session(session_id)
        if not port:
            return Response(content=b"No worker available for session", status_code=503)
    else:
        # For non-session endpoints, use round-robin
        port = get_next_worker_port()
        if not port:
            return Response(content=b"No workers available", status_code=503)

    worker_url = f"http://127.0.0.1:{port}/{path}"

    # Forward the request
    httpx_response = await forward_request(
        method=request.method,
        url=worker_url,
        headers=dict(request.headers),
        body=await request.body()
    )

    return convert_httpx_to_fastapi_response(httpx_response)

def extract_session_id_from_path(path: str) -> Optional[str]:
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
