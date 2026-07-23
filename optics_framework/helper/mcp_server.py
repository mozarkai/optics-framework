"""MCP (Model Context Protocol) server exposing optics-framework keywords.

Launched via ``optics mcp``. This wraps the in-process keyword machinery in
``optics_framework.common.expose_api`` (the same async functions the HTTP server
``optics serve`` uses) and surfaces it over MCP so an LLM client (Claude
Desktop/Code, Cursor, ...) can drive a live device/browser session.

Shape (hybrid):
- Each optics *action* keyword becomes its own typed MCP **tool** (each takes an
  explicit ``session_id``). Tool parameters are reflected from the real API-class
  method signatures but typed as ``str`` because the underlying
  ``ExecuteRequest.params`` boundary is string-only.
- Read-only device state is exposed as MCP **resources** (screenshot, page
  source, interactive elements, screen elements) plus the keyword catalog.

``fastmcp`` is an optional dependency: ``pip install optics-framework[mcp]``.

Process-isolation note: ``optics mcp`` and ``optics serve`` are separate OS
processes. ``SessionManager`` is in-memory, so sessions are NOT shared between
them. A client must call ``start_session`` before any keyword tool will work.
"""

from __future__ import annotations

import base64
import inspect
from typing import Any, Callable, Optional

from fastapi import HTTPException

from optics_framework.api.action_keyword import ActionKeyword
from optics_framework.api.app_management import AppManagement
from optics_framework.api.verifier import Verifier
from optics_framework.common import expose_api
from optics_framework.common.error import OpticsError
from optics_framework.common.logging_config import internal_logger

# fastmcp is optional (extra: mcp). Import lazily with a clear, actionable error.
try:
    from fastmcp import FastMCP
    from fastmcp.exceptions import ToolError
    from fastmcp.tools.tool import Tool
    from fastmcp.utilities.types import Image

    _FASTMCP_IMPORT_ERROR: Optional[ImportError] = None
except ImportError as _e:  # pragma: no cover - exercised only without the extra
    FastMCP = None  # type: ignore[assignment,misc]
    ToolError = RuntimeError  # type: ignore[assignment,misc]
    Tool = None  # type: ignore[assignment,misc]
    Image = None  # type: ignore[assignment,misc]
    _FASTMCP_IMPORT_ERROR = _e

SERVER_NAME = "Optics MCP"
SERVER_INSTRUCTIONS = (
    "Drive a live device/browser through the optics-framework.\n"
    "1. Call `start_session` first to open a session against your driver "
    "(e.g. appium) and capture the returned `session_id`.\n"
    "2. Pass that `session_id` to every keyword tool (press_element, enter_text, "
    "swipe, assert_presence, ...).\n"
    "3. Observe device state via resources: optics://session/{session_id}/screenshot, "
    "/source, /elements, /screen_elements. The full keyword catalog is at "
    "optics://keywords.\n"
    "4. Call `terminate_session` when done.\n"
    "Sessions live only inside this server process; they are not shared with "
    "`optics serve` or `optics live`."
)

# API classes whose public methods become keyword tools — the same set the HTTP
# `execute_keyword` registry builds (FlowControl is intentionally excluded; it
# needs runner context).
_KEYWORD_CLASSES = (ActionKeyword, AppManagement, Verifier)

# Read-only observers surfaced as resources instead of action tools.
# `get_interactive_elements` is intentionally NOT here: it takes `filter_config`
# (which resources cannot accept), so it stays a tool AND is mirrored as an
# unfiltered resource.
_RESOURCE_ONLY_KEYWORDS = frozenset(
    {"capture_screenshot", "capture_pagesource", "get_screen_elements"}
)

# Internal params injected by decorators (e.g. `@with_self_healing` fills
# `located`), never supplied by an MCP client — drop them from tool schemas.
_EXCLUDED_PARAMS = frozenset({"located"})

_RESULT_KEY = expose_api.KEY_RESULT


def _require_fastmcp() -> None:
    if _FASTMCP_IMPORT_ERROR is not None:
        raise RuntimeError(
            "The 'mcp' extra is required to run the Optics MCP server. "
            "Install it with: pip install 'optics-framework[mcp]'"
        ) from _FASTMCP_IMPORT_ERROR


def _http_detail(detail: Any) -> str:
    """Render an HTTPException detail (str or optics error payload dict) to text."""
    if isinstance(detail, dict):
        # OpticsError.to_payload(include_status=True) shape: prefer message/code.
        msg = detail.get("message") or detail.get("detail") or detail
        code = detail.get("code")
        return f"[{code}] {msg}" if code else str(msg)
    return str(detail)


def _stringify_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Coerce tool kwargs to the string-only ExecuteRequest.params contract.

    None values are dropped so keyword defaults apply; lists are stringified
    element-wise (the param-fallback ladder consumes List[str]).
    """
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        if isinstance(value, list):
            out[key] = [str(item) for item in value]
        else:
            out[key] = str(value)
    return out


def _reflect_keyword_params(method: Callable[..., Any]) -> list[inspect.Parameter]:
    """Real (name, default) params of an API method, excluding self."""
    sig = inspect.signature(method)
    return [
        p
        for name, p in sig.parameters.items()
        if name != "self"
        and name not in _EXCLUDED_PARAMS
        # *args / **kwargs can't be represented as discrete string tool params.
        and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]


def _make_keyword_tool(slug: str, params: list[inspect.Parameter]) -> Callable[..., Any]:
    """Synthesize a str-typed wrapper that dispatches `slug` via execute_keyword.

    The wrapper's exposed signature is `session_id: str` followed by the
    keyword's params, every annotation forced to `str` (or `str | None` when the
    default is None) so fastmcp/pydantic emit a clean, consistent schema.
    """

    async def wrapper(**kwargs: Any) -> Any:
        session_id = kwargs.pop("session_id")
        request = expose_api.ExecuteRequest(
            mode=expose_api.MODE_KEYWORD,
            keyword=slug,
            params=_stringify_params(kwargs),
        )
        try:
            response = await expose_api.execute_keyword(session_id, request)
        except HTTPException as exc:
            raise ToolError(_http_detail(exc.detail)) from exc
        except OpticsError as exc:  # pragma: no cover - defensive
            raise ToolError(str(exc)) from exc
        data = getattr(response, "data", None) or {}
        # When a self-heal recovered this call, execute_keyword returns {"result": ...,
        # "healed": True, "heal_summary": ...} instead of the bare result — return the
        # whole dict so the MCP client sees the heal, not just the unwrapped result.
        if "healed" in data:
            return data
        return data.get(_RESULT_KEY, data)

    synth: list[inspect.Parameter] = [
        inspect.Parameter("session_id", inspect.Parameter.KEYWORD_ONLY, annotation=str)
    ]
    annotations: dict[str, Any] = {"session_id": str}
    for param in params:
        name = param.name
        if param.default is inspect.Parameter.empty:
            synth.append(
                inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=str)
            )
            annotations[name] = str
        elif param.default is None:
            synth.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=Optional[str],
                    default=None,
                )
            )
            annotations[name] = Optional[str]
        else:
            synth.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=str,
                    default=str(param.default),
                )
            )
            annotations[name] = str

    wrapper.__signature__ = inspect.Signature(synth)  # type: ignore[attr-defined]
    wrapper.__annotations__ = {**annotations, "return": Any}
    wrapper.__name__ = slug
    return wrapper


def _iter_keyword_tools() -> list[tuple[str, str, Callable[..., Any]]]:
    """Yield (slug, description, wrapper) for every action keyword to register."""
    tools: list[tuple[str, str, Callable[..., Any]]] = []
    seen: set[str] = set()
    for cls in _KEYWORD_CLASSES:
        for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            if name.startswith("_") or name.startswith("test"):
                continue
            if name in _RESOURCE_ONLY_KEYWORDS or name in seen:
                continue
            seen.add(name)
            description = inspect.getdoc(method) or expose_api._humanize_keyword(name)
            wrapper = _make_keyword_tool(name, _reflect_keyword_params(method))
            tools.append((name, description, wrapper))
    return tools


async def _observe(session_id: str, keyword: str) -> Any:
    """Run a read-only observer keyword and return its unwrapped result."""
    try:
        response = await expose_api.run_keyword_endpoint(session_id, keyword)
    except HTTPException as exc:
        raise ToolError(_http_detail(exc.detail)) from exc
    data = getattr(response, "data", None) or {}
    return data.get(_RESULT_KEY, data)


def _decode_screenshot(result: Any) -> bytes:
    """Decode a base64 screenshot result (raw base64 or data URL) to raw bytes."""
    if isinstance(result, bytes):
        return result
    if not isinstance(result, str):
        raise ToolError("Screenshot result was not a base64 string")
    payload = result.split(",", 1)[1] if result.startswith("data:") else result
    return base64.b64decode(payload)


def build_server() -> "FastMCP":
    """Construct the FastMCP server with all keyword tools and state resources."""
    _require_fastmcp()
    mcp = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)

    # --- Lifecycle tools -------------------------------------------------------
    async def start_session(
        driver: str = "appium",
        url: Optional[str] = None,
        capabilities: Optional[dict[str, Any]] = None,
        elements_sources: Optional[list[str]] = None,
        text_detection: Optional[list[str]] = None,
        image_detection: Optional[list[str]] = None,
        project_path: Optional[str] = None,
        ai_self_heal: Optional[bool] = None,
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> dict[str, Any]:
        """Start a new optics session and launch the target app.

        Returns {"session_id", "driver_id"}. Pass the session_id to every
        keyword tool and state resource. The app is auto-launched on start.
        Sessions are NOT shared with `optics serve`/`optics live` (separate
        process); start one here before using any keyword tool.

        ai_self_heal / llm_provider / llm_model: per-session overrides for AI self-heal.
        Each falls back to the matching service-level env var the operator set at
        start-up (OPTICS_AI_SELF_HEAL / OPTICS_LLM_PROVIDER / OPTICS_LLM_MODEL) when left
        unset, so an already-configured server needs none of them. Because the process
        is multi-tenant, pass them to opt in/out or pick your own model for just this
        session instead of being locked to the operator's choice. Note that
        ai_self_heal=True with no provider configured anywhere still degrades to inert
        (no LLM to drive it); LLM credentials always come from the provider's own env
        vars (e.g. GOOGLE_API_KEY), never this tool.
        """
        if url or capabilities:
            driver_sources: list[Any] = [
                {driver: {"enabled": True, "url": url, "capabilities": capabilities or {}}}
            ]
        else:
            driver_sources = [driver]
        config = expose_api.SessionConfig(
            driver_sources=driver_sources,
            elements_sources=elements_sources or [],
            text_detection=text_detection or [],
            image_detection=image_detection or [],
            project_path=project_path,
            ai_self_heal=ai_self_heal,
            llm_provider=llm_provider,
            llm_model=llm_model,
        )
        try:
            response = await expose_api.create_session(config)
        except HTTPException as exc:
            raise ToolError(_http_detail(exc.detail)) from exc
        return {"session_id": response.session_id, "driver_id": response.driver_id}

    async def terminate_session(session_id: str) -> dict[str, str]:
        """Terminate a session and release its driver/resources."""
        try:
            await expose_api.delete_session(session_id)
        except HTTPException as exc:
            raise ToolError(_http_detail(exc.detail)) from exc
        return {"session_id": session_id, "status": "terminated"}

    async def screenshot(session_id: str) -> "Image":
        """Capture the current screen and return it as a rendered PNG image.

        Prefer this over the `optics://session/{session_id}/screenshot` resource
        when you want the screen rendered inline (the resource returns raw bytes).
        """
        raw = _decode_screenshot(await _observe(session_id, "capture_screenshot"))
        return Image(data=raw, format="png")

    mcp.add_tool(Tool.from_function(start_session, name="start_session"))
    mcp.add_tool(Tool.from_function(terminate_session, name="terminate_session"))
    mcp.add_tool(Tool.from_function(screenshot, name="screenshot"))

    # --- Per-keyword action tools ---------------------------------------------
    for slug, description, wrapper in _iter_keyword_tools():
        mcp.add_tool(Tool.from_function(wrapper, name=slug, description=description))

    # --- Read-only state resources --------------------------------------------
    @mcp.resource("optics://keywords", mime_type="application/json")
    def keywords_catalog() -> list[dict[str, Any]]:
        """The full optics keyword catalog (name, slug, description, params)."""
        return [info.model_dump() for info in expose_api.discover_keywords()]

    @mcp.resource("optics://session/{session_id}/screenshot", mime_type="image/png")
    async def screenshot_resource(session_id: str) -> bytes:
        """Current screen as raw PNG bytes (use the `screenshot` tool for a rendered image)."""
        return _decode_screenshot(await _observe(session_id, "capture_screenshot"))

    @mcp.resource("optics://session/{session_id}/source", mime_type="application/json")
    async def page_source(session_id: str) -> Any:
        """Current page source / UI hierarchy."""
        return await _observe(session_id, "capture_pagesource")

    @mcp.resource("optics://session/{session_id}/elements", mime_type="application/json")
    async def interactive_elements(session_id: str) -> Any:
        """All interactive elements on screen (unfiltered; use the tool to filter)."""
        return await _observe(session_id, "get_interactive_elements")

    @mcp.resource(
        "optics://session/{session_id}/screen_elements", mime_type="application/json"
    )
    async def screen_elements(session_id: str) -> Any:
        """Captured screen elements for the current screen."""
        return await _observe(session_id, "get_screen_elements")

    return mcp


def run_mcp_server(
    transport: str = "stdio", host: str = "127.0.0.1", port: int = 8090
) -> None:
    """Build and run the Optics MCP server.

    Args:
        transport: "stdio" (default, for local MCP clients) or "http".
        host: Bind host for the http transport (ignored for stdio).
        port: Bind port for the http transport (ignored for stdio).
    """
    _require_fastmcp()
    mcp = build_server()
    internal_logger.info(
        "Starting Optics MCP server (transport=%s%s)",
        transport,
        f", {host}:{port}" if transport != "stdio" else "",
    )
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=transport, host=host, port=port)
