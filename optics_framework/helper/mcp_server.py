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
from optics_framework.common.factories import ElementSourceFactory
from optics_framework.common.logging_config import internal_logger
from optics_framework.helper.version import VERSION

# Listing order for a driver's element-source config entries (find_element
# before page_source before screenshot) when start_session builds its
# defaults. Unrelated to StrategyFactory's locate-strategy priority in
# strategies.py (XPath/Text/TextDetection/Image, one per LocatorStrategy
# class): this ranks element-source *modules*, of which there's no 1:1
# correspondence to those 4 strategies (page_source backs neither a
# LocatorStrategy nor PagesourceStrategy's own separate factory list).
_SOURCE_RANK = {"find_element": 0, "page_source": 1, "screenshot": 2}


def _default_sources_for_driver(driver: str) -> list[str]:
    """The driver's canonical ``elements_sources``, discovered by reflection.

    Returns the ``{driver}_find_element/_page_source/_screenshot`` trio (in
    ``_SOURCE_RANK`` order) for whatever driver is named, by matching the
    installed element-source module names — so it stays correct for appium,
    selenium, playwright, or any future driver. Returns ``[]`` for a driver
    with no matching sources (e.g. a misspelled name); the caller then passes
    that on, and session creation fails with the explicit "Element source
    configuration must be set" error. Name-level only: no engine import, so a
    missing optional extra never breaks it.
    """
    key = (driver or "").strip().lower()
    matches = ElementSourceFactory.available_sources(key)

    def rank(name: str) -> tuple[int, str]:
        suffix = name.split("_", 1)[1] if "_" in name else name
        return (_SOURCE_RANK.get(suffix, len(_SOURCE_RANK)), name)

    return sorted(matches, key=rank)

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
    "Drive a live device or browser through the optics-framework under agent control.\n"
    "1. `start_session` opens a session against your driver (e.g. appium) and returns "
    "a `session_id`; pass it to every keyword tool and resource. Element sources "
    "default per driver, so `start_session(driver=\"appium\")` just works — supply "
    "`url`/`capabilities` for your target (device id goes in capabilities, e.g. "
    "deviceName/udid; the MCP itself needs no adb).\n"
    "2. Act with the keyword tools (press_element, enter_text, swipe, assert_presence, "
    "...). Target elements by locator (`xpath=`/`text=`/an id/an image) with "
    "`press_element`; if there is no stable locator, `detect_and_press` taps by the "
    "visible text/label. Otherwise use the exact `bounds` from `get_interactive_elements` "
    "— do NOT tap pixel coordinates guessed from a screenshot; they misfire. Observe "
    "with the `screenshot` tool and the `optics://session/{id}/...` resources.\n"
    "3. To build a reusable suite you already have the steps you ran — author an "
    "optics CSV project yourself (read the `optics://project-format` resource for the "
    "exact layout and `${var}` parameterization), then run it with the CLI "
    "(`optics execute <folder>`). The MCP has no record/save/replay tools by design: "
    "compose them from the keyword tools plus that format knowledge.\n"
    "4. `terminate_session` when done. Sessions live only inside this server process; "
    "they are not shared with `optics serve` or `optics live`."
)

# Knowledge (not a tool) served at optics://project-format so an agent can author
# a runnable optics suite itself from the steps it already ran — no save/record
# tool needed. It documents the CSV layout the `optics execute` runner reads.
_PROJECT_FORMAT_DOC = """\
# Optics project format

An optics test project is a folder of CSV files plus a `config.yaml` that
`optics execute <folder>` runs. Author one directly from the steps you already
performed this session — the MCP has no save/record tool because you don't need
one: you know the keywords you called and their args.

## Layout
    <project>/
      config.yaml                 # driver + element sources
      test_cases/test_cases.csv   # which modules each test case runs, in order
      modules/modules.csv         # the keyword steps of each module
      test_data/elements.csv      # named locators / variables

## test_cases/test_cases.csv  — columns: test_case,test_step
One row per (test case, module) in run order; `test_step` holds a MODULE name
(not a keyword).
    test_case,test_step
    Set Alarm,Open Clock
    Set Alarm,Create Alarm

## modules/modules.csv  — columns: module_name,module_step,param_1,param_2,...
One row per step. `module_step` is a keyword's display name (Title Case of the
tool/keyword name: `press_element` -> "Press Element"; full catalog at the
`optics://keywords` resource). Params are positional; an optional keyword arg is
written `name=value` (e.g. `index=2`). A locator such as `text=Save` stays one
positional value.
    module_name,module_step,param_1,param_2
    Open Clock,Launch App
    Create Alarm,Press Element,${add_alarm}
    Create Alarm,Enter Text,${hour_field},${time}

## test_data/elements.csv  — columns: Element_Name,Element_ID
Maps a name to a locator OR a value; steps reference it as `${Element_Name}`.
This is also how you parameterize a suite: put the run-specific value here and
reference it, so "set alarm to 22:00" becomes a reusable `${time}`. A whole cell
must be exactly `${Name}` — the runner substitutes whole values only, it does not
expand `${name}` inside a larger string.
    Element_Name,Element_ID
    add_alarm,//*[@content-desc="Add alarm"]
    hour_field,text=Hour
    time,22:00

## config.yaml  — driver + element sources
`elements_sources` for a driver are `{driver}_find_element`,
`{driver}_page_source`, `{driver}_screenshot`. appium is only an example here —
selenium and playwright follow the same shape.
    driver_sources:
      - appium:            # or selenium / playwright / ...
          enabled: true
          url: "<driver server url>"
          capabilities: {}   # e.g. platformName, deviceName/udid for appium
    elements_sources:
      - appium_find_element: { enabled: true }
      - appium_page_source: { enabled: true }
      - appium_screenshot: { enabled: true }

## Control flow & data (author these into modules; the runner executes them)
A module step is any keyword from the `optics://keywords` catalog — including the
control-flow/data keywords that only run under the CSV runner, not as a live
tool: `Run Loop`, `Condition`, `Execute Module`, `Evaluate`, `Date Evaluate`,
`Read Data`, `Invoke Api`. Use them when a suite needs loops, branching, computed
values, or API calls (e.g. a picker's read-current -> compute -> repeat pattern
becomes an `Evaluate` + `Run Loop`). Live, you drive that logic yourself by
calling the primitive keyword tools in the loop/branch you decide; you only need
these keywords when authoring a suite to run headless via `optics execute`.

## Escaping
CSV values containing commas are quoted by the writer; a literal newline, tab or
backslash in a value is written escaped (`\\n`, `\\t`, `\\\\`) and the runner
un-escapes it on read.

## Running
`optics execute <project>` runs every test case; JUnit XML, logs and screenshots
land in `<project>/execution_output/`.
"""

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
        """Run the keyword. On an AI self-heal, return the whole {result, healed, ...}
        dict so the client sees the recovery; otherwise unwrap to the bare result.
        """
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
    mcp = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS, version=VERSION)

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
        strict_element_match: Optional[bool] = None,
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

        strict_element_match: reject fuzzy/partial text and xpath matches on locate
        (default False, i.e. fuzzy-tolerant). Set True when a near-miss locator string
        pressing the wrong element is worse than the keyword failing outright.
        """
        if url or capabilities:
            driver_sources: list[Any] = [
                {driver: {"enabled": True, "url": url, "capabilities": capabilities or {}}}
            ]
        else:
            driver_sources = [driver]
        # Sane defaults: only an *omitted* elements_sources (None) triggers the
        # per-driver defaults. An explicit empty list is passed through — it
        # disables element sources entirely, and session creation then fails with
        # "Element source configuration must be set".
        resolved_elements = (
            elements_sources
            if elements_sources is not None
            else _default_sources_for_driver(driver)
        )
        config = expose_api.SessionConfig(
            driver_sources=driver_sources,
            elements_sources=resolved_elements,
            text_detection=text_detection or [],
            image_detection=image_detection or [],
            project_path=project_path,
            ai_self_heal=ai_self_heal,
            llm_provider=llm_provider,
            llm_model=llm_model,
            strict_element_match=strict_element_match,
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

    @mcp.resource("optics://project-format", mime_type="text/markdown")
    def project_format() -> str:
        """How optics stores a test suite (CSV layout + ${var} params) so you can
        author a runnable project yourself, then run it with `optics execute`."""
        return _PROJECT_FORMAT_DOC

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
