"""What a remote caller (``Config.remote_caller``: ``optics serve``, HTTP MCP) may make the server touch."""
from typing import Any
from urllib.parse import urlsplit

from optics_framework.common.error import Code, OpticsError

# Schemes that open resources of the machine running the browser rather than a web page.
_LOCAL_RESOURCE_SCHEMES = frozenset({
    "file", "view-source", "about", "chrome", "chrome-untrusted", "chrome-extension", "edge",
    "devtools", "filesystem", "resource", "jar", "moz-extension",
})


def _is_remote(config: Any) -> bool:
    return getattr(config, "remote_caller", False) is True


def refuse_server_read(config: Any, what: str) -> None:
    """Refuse a read of the server's files or environment when the caller is remote."""
    if _is_remote(config):
        raise OpticsError(Code.E0501, f"{what} reads the server's files or environment; a remote session must send the data itself")


def _local_scheme(target: Any) -> str | None:
    """The scheme of a URL that opens a local resource of the browser's machine, else None."""
    if not isinstance(target, str):
        return None
    url = target.strip()
    scheme = urlsplit(url).scheme.lower()
    return scheme if scheme in _LOCAL_RESOURCE_SCHEMES and url.lower() != "about:blank" else None


def refuse_local_resource(config: Any, target: Any) -> None:
    """Refuse a URL that opens a local resource of the browser's machine when the caller is remote."""
    if _is_remote(config) and (scheme := _local_scheme(target)):
        raise OpticsError(Code.E0501, f"A remote session cannot open {scheme}: URLs; use an http(s) URL")


def refuse_local_capabilities(name: str, capabilities: dict[str, Any]) -> None:
    """Refuse a remote caller's capability that names a local resource, such as a start URL a driver opens itself."""
    for key, value in capabilities.items():
        if scheme := _local_scheme(value):
            raise OpticsError(Code.E0501, f"{name}: {key} cannot be a {scheme}: URL in a remote session; use an http(s) URL")
