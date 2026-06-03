"""Streamable HTTP transport for the Optics MCP server.

Mirrors the mozark-mcp shape: a Starlette app that mounts the MCP
`StreamableHTTPSessionManager` at `/mcp` via a raw ASGI route (to avoid
the slash-redirect that drops the POST body), plus `/healthz` and a `/`
discovery page. No auth in v1 — this is intended for local use; a
BearerAuthMiddleware can drop in later.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Match, Route
from starlette.types import Receive, Scope, Send

from optics_framework.helper.version import VERSION
from optics_framework.mcp.server import server as mcp_server

log = logging.getLogger("optics_framework.mcp")

_session_manager = StreamableHTTPSessionManager(app=mcp_server, stateless=False)


async def _handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
    await _session_manager.handle_request(scope, receive, send)


class _RawASGIRoute(BaseRoute):
    """Mount a raw ASGI app at one exact path.

    Starlette's `Mount` appends `/{path:path}` and 307-redirects the
    bare prefix, which drops the POST body MCP clients send to `/mcp`.

    A `Route("/mcp", endpoint=...)` with a Request → Response endpoint
    is not a workable alternative either: bridging the MCP SDK's raw
    ASGI handler back through a Starlette Request requires either
    reading `request._send` (private attribute) or buffering the
    streamable response in memory. The custom BaseRoute is the lowest-
    blast-radius way to give the SDK the `(scope, receive, send)` triple
    it expects without slash-rewriting or private-API access.
    """

    def __init__(self, path: str, app):
        self.path = path
        self.app = app
        self.name = path

    def matches(self, scope: Scope):
        if scope.get("type") != "http":
            return Match.NONE, {}
        if scope.get("path") == self.path:
            return Match.FULL, {}
        return Match.NONE, {}

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)

    def url_path_for(self, name: str, /, **path_params):
        from starlette.routing import NoMatchFound

        if name == self.name and not path_params:
            return self.path
        raise NoMatchFound(name, path_params)


async def _root(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "service": "optics-framework-mcp",
            "version": VERSION,
            "mcp_endpoint": "/mcp",
        }
    )


async def _healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "optics-framework-mcp"})


@contextlib.asynccontextmanager
async def _lifespan(_app: Starlette) -> AsyncIterator[None]:
    log.info("optics-mcp starting")
    async with _session_manager.run():
        yield
    log.info("optics-mcp stopped")


def create_app(cors_allowed_origins: tuple[str, ...] = ("*",)) -> Starlette:
    return Starlette(
        routes=[
            Route("/", _root, methods=["GET"]),
            Route("/healthz", _healthz, methods=["GET"]),
            _RawASGIRoute("/mcp", _handle_mcp),
        ],
        lifespan=_lifespan,
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=list(cors_allowed_origins),
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Authorization", "Content-Type", "Mcp-Session-Id"],
                expose_headers=["Mcp-Session-Id", "WWW-Authenticate"],
                max_age=600,
            ),
        ],
    )
