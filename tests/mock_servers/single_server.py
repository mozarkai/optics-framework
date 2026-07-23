"""In-process FastAPI mock used by the API-invocation tests.

Exposes a token + OTP endpoint pair and a :func:`start_server` helper that binds
an **ephemeral** port (race-free: we own the socket, so we know the port before
uvicorn starts) and guarantees teardown. Consume it through the session-scoped
``mock_api_server`` fixture in ``tests/conftest.py`` rather than starting it by
hand — that keeps the suite hermetic and free of fixed-port collisions.
"""
from __future__ import annotations

import socket
import threading
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

app = FastAPI()


class LoginRequest(BaseModel):
    username: str
    password: str


class OTPRequest(BaseModel):
    userId: str
    txnType: str


@app.post("/token", responses={400: {"description": "Invalid credentials"}})
async def post_token(request: LoginRequest):
    if request.username == "test" and request.password == "password":
        return {
            "access_token": "real_auth_token_123",
            "token_type": "bearer",
            "expires_in": 3600,
            "user": {"userId": "98765"},
        }
    raise HTTPException(status_code=400, detail="Invalid credentials")


@app.post("/sendotp", responses={400: {"description": "Invalid OTP request"}})
async def send_otp(request: OTPRequest, request_obj: Request):
    authorization = request_obj.headers.get("Authorization")
    if (
        authorization == "real_auth_token_123"
        and request.userId == "98765"
        and request.txnType == "GEN"
    ):
        return {"txnType": "GEN"}
    raise HTTPException(status_code=400, detail="Invalid OTP request")


class _ReadySignallingServer(uvicorn.Server):
    """A uvicorn server that flips an event once startup completes."""

    def __init__(self, config: uvicorn.Config):
        super().__init__(config)
        self.started_event = threading.Event()

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        self.started_event.set()


@dataclass
class RunningServer:
    base_url: str
    _server: _ReadySignallingServer
    _thread: threading.Thread

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def start_server() -> RunningServer:
    """Start the mock API on a free ephemeral port and block until it is ready.

    We create and bind the listening socket ourselves (port ``0`` → kernel picks
    a free port), read the assigned port back, then hand the bound socket to
    uvicorn. This removes both the fixed-8001 collision risk and the
    bind-after-check race the old ``time.sleep(0.1)`` band-aid papered over.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    config = uvicorn.Config(app, log_level="warning")
    server = _ReadySignallingServer(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    if not server.started_event.wait(30):
        server.should_exit = True
        thread.join(timeout=10)
        raise RuntimeError("mock API server failed to start within 30s")
    return RunningServer(base_url=f"http://127.0.0.1:{port}", _server=server, _thread=thread)
