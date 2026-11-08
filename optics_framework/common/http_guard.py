"""HTTP guards for the servers that expose keywords: CORS allowlist, loopback Host check, exposure warning."""
import ipaddress
import os
from typing import Any

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse

from optics_framework.common.error import Code, OpticsError
from optics_framework.common.logging_config import internal_logger

ENV_CORS_ORIGINS = "OPTICS_CORS_ORIGINS"


def _hostname(host: str) -> str:
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_loopback(host: str) -> bool:
    name = _hostname(host.strip()).lower()
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return name == "localhost" or name.endswith(".localhost")


def cors_origins() -> list[str]:
    """Browser origins allowed to call the server, from ``OPTICS_CORS_ORIGINS``; none by default."""
    origins = [origin.strip() for origin in os.environ.get(ENV_CORS_ORIGINS, "").split(",") if origin.strip()]
    if "*" in origins:
        raise OpticsError(Code.E0501, f"{ENV_CORS_ORIGINS} must list origins, not '*': any web page could drive the server")
    return origins


class LoopbackHostGuard:
    """Refuse a request to a loopback-bound server whose Host header is not a loopback name.

    Without it, a web page can rebind its own domain to 127.0.0.1 and drive the server.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        server = scope.get("server")
        if (
            scope["type"] == "http"
            and server and is_loopback(str(server[0]))
            and not is_loopback(Headers(scope=scope).get("host", ""))
        ):
            await PlainTextResponse("Misdirected Request", status_code=421)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def add_http_guards(app: Any) -> None:
    """Add the CORS allowlist and the loopback Host check to a Starlette/FastAPI app."""
    origins = cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
        )
    app.add_middleware(LoopbackHostGuard)


def warn_if_exposed(server: str, host: str) -> None:
    """Warn that a server bound beyond loopback has no authentication of its own."""
    if not is_loopback(host):
        internal_logger.warning(
            "%s listens on %s with no authentication: anyone who can reach it can drive its devices "
            "and browsers. Put it behind an authenticating gateway or a network policy.", server, host,
        )
