"""Optics Framework MCP server.

Exposes the Optics keyword surface to any MCP-compatible client
(Claude Desktop, Claude Code, Cursor, claude.ai web, etc.). The MCP
layer is a thin transport translator over the existing FastAPI handlers
in `optics_framework.common.expose_api` — session lifecycle, keyword
dispatch, and execution fallback are reused verbatim.
"""
