#!/usr/bin/env python3
"""Minimal stand-in for `optics_framework.common.expose_api:app`.

Used by the supervisor integration tests (via SUPERVISOR_WORKER_APP) so routing,
affinity, and lifecycle behavior can be exercised without an Appium hub or a
real device. It mirrors the worker endpoints the supervisor cares about and
keeps sessions in process memory — so a request routed to the wrong worker
process gets a 404, exactly like the real app.
"""

import asyncio
import os
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Optics Stub Worker")

_sessions: set[str] = set()


@app.get("/")
async def health():
    """Mirrors the real worker's health check (expose_api has GET /, not /health)."""
    return {"status": "ok", "pid": os.getpid()}


@app.post("/v1/sessions/start")
async def start_session():
    session_id = str(uuid.uuid4())
    _sessions.add(session_id)
    return {"session_id": session_id, "driver_id": "stub-driver", "pid": os.getpid()}


@app.post("/v1/sessions/{session_id}/action")
async def execute_action(session_id: str, payload: dict | None = None):
    if session_id not in _sessions:
        return JSONResponse({"detail": "Session not found"}, status_code=404)
    # Optional artificial delay so tests can assert concurrency across workers.
    delay = float((payload or {}).get("stub_delay_s", 0))
    if delay:
        await asyncio.sleep(delay)
    return {
        "execution_id": str(uuid.uuid4()),
        "status": "SUCCESS",
        "pid": os.getpid(),
    }


@app.delete("/v1/sessions/{session_id}/stop")
async def stop_session(session_id: str):
    if session_id not in _sessions:
        return JSONResponse({"detail": "Session not found"}, status_code=404)
    _sessions.discard(session_id)
    return {"status": "terminated", "session_id": session_id}
