"""Stdio transport for the Optics MCP server.

For local MCP clients (Claude Desktop, Claude Code, Cursor) that talk
to the server over stdin/stdout instead of HTTP. No auth — the client
process started us, so the trust boundary is process-local.
"""

from __future__ import annotations

import asyncio
import logging

from mcp.server.stdio import stdio_server

from optics_framework.mcp.server import server

log = logging.getLogger("optics_framework.mcp")


async def _run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def run_stdio() -> None:
    log.info("optics-mcp stdio starting")
    asyncio.run(_run())
