"""MCP (Model Context Protocol) server for optics-framework, launched via ``optics mcp``.

It wraps the in-process keyword machinery of ``optics_framework.common.expose_api`` (the
functions ``optics serve`` uses), so an LLM client can drive a live device or browser.

Two toolsets:
- ``core`` (default): ten typed tools. The agent targets a row of the screen by its ref
  (``e7``); every action returns what changed and a verdict on the expectations it declared.
- ``full``: every reflected keyword as its own string-typed tool, plus the core session,
  ``observe``, ``check`` and ``run_keyword`` tools, for clients written against that surface.

``fastmcp`` is an optional dependency: ``pip install optics-framework[mcp]``.

``optics mcp`` and ``optics serve`` are separate processes with separate in-memory
session stores, so a client must call ``start_session`` here first.
"""
import asyncio
import base64
import contextlib
import csv
import inspect
import io
import math
import os
import pathlib
import re
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Annotated, Any, Literal

import cv2
import numpy as np
from fastapi import HTTPException
from pydantic import BaseModel, Field

from optics_framework.api.action_keyword import ActionKeyword
from optics_framework.api.app_management import AppManagement
from optics_framework.api.verifier import Verifier
from optics_framework.common import expose_api, utils
from optics_framework.common.agent_verbs import (
    ADVICE,
    COORDINATE_KEYWORDS,
    HIDDEN_KEYWORDS,
    NON_VERIFYING_KEYWORDS,
    TYPED_VALUE_PARAMS,
    KeywordCall,
    error_kind,
    is_deprecated,
    param_names,
    plan_call,
    runner_params,
    typed_value,
    validate,
    visible_params,
)
from optics_framework.common.error import OpticsError
from optics_framework.common.factories import ElementSourceFactory
from optics_framework.common.keyword_outcome import (
    capture_keyword_logs,
    silent_error,
    winning_strategy,
)
from optics_framework.common.logging_config import (
    decouple_console_logs,
    drain_console_logs,
    internal_logger,
    is_secret_name,
)
from optics_framework.common.screen_snapshot import (
    REF_RE,
    Check,
    Diff,
    RefTable,
    Row,
    Snapshot,
    TargetError,
    acting_ladder,
    check_expectations,
    diff,
    element_name,
    holds_secret,
    judge_step,
    live_ladder,
    read_screen,
    recordable_present,
    render_diff,
    render_rows,
    saved_ladder,
    settle,
    without_typed_value,
)
from optics_framework.helper.version import VERSION

# Unrelated to StrategyFactory's locate-strategy priority in strategies.py —
# this ranks element-source modules, not locate techniques.
_SOURCE_RANK = {"find_element": 0, "page_source": 1, "screenshot": 2}


def _default_sources_for_driver(driver: str) -> list[str]:
    """The driver's element sources, found by reflection; ``[]`` for an unknown driver."""
    key = (driver or "").strip().lower()
    matches = ElementSourceFactory.available_sources(key)

    def rank(name: str) -> tuple[int, str]:
        suffix = name.split("_", 1)[1] if "_" in name else name
        return (_SOURCE_RANK.get(suffix, len(_SOURCE_RANK)), name)

    return sorted(matches, key=rank)


try:
    from fastmcp import FastMCP
    from fastmcp.exceptions import ToolError
    from fastmcp.tools import Tool, ToolResult
    from fastmcp.utilities.types import Image
    from mcp.types import TextContent, ToolAnnotations

    _FASTMCP_IMPORT_ERROR: ImportError | None = None
except ImportError as _e:  # pragma: no cover - exercised only without the extra
    FastMCP = None  # type: ignore[assignment,misc]
    ToolError = RuntimeError  # type: ignore[assignment,misc]
    Tool = ToolResult = Image = TextContent = ToolAnnotations = None  # type: ignore[assignment,misc]
    _FASTMCP_IMPORT_ERROR = _e

SERVER_NAME = "Optics MCP"
SERVER_INSTRUCTIONS = (
    "Drive a live device or browser with ref-targeted, verified actions.\n"
    "1. `start_session(driver, app=...)` returns a session and the first screen. Web: "
    "`driver=\"playwright\"`, `app=\"<url>\"`. Device: `driver=\"appium\"` with `url` and "
    "`capabilities`.\n"
    "2. A row looks like `e7 button \"Sign in\" [tap] #id`. Quoted text is on the screen; "
    "`#id` is not. A row without `[...]` is read-only text. Target elements by ref.\n"
    "3. Every action returns what changed on the screen. Call `observe` only for a `query`, "
    "a screenshot or the full list.\n"
    "4. `ok` means only that the keyword ran. Add `expect_present`/`expect_absent` with text "
    "you already know, then trust `verified`. `check` gives a verdict, not an error. Do not "
    "use `sleep`.\n"
    "5. Use `run_keyword` for anything without a tool (catalog: `optics://keywords`). "
    "`detect_and_press` passes even when nothing was pressed.\n"
    "6. To build a suite, read `optics://session/{id}/steps` and `optics://project-format`, "
    "write the files, then run `optics execute <folder>`.\n"
    "7. `session_id` can be left out when one session is open. Sessions end after the idle "
    "lease and are not shared with `optics serve` or `optics live`. Call `terminate_session` "
    "when done."
)

_PROJECT_FORMAT_DOC = (
    pathlib.Path(__file__).parent / "mcp_project_format.md"
).read_text(encoding="utf-8")

# FlowControl is left out: its keywords need the runner's module and data context.
_KEYWORD_CLASSES = (ActionKeyword, AppManagement, Verifier)

# Read-only observers surfaced as resources instead of full-toolset tools.
_RESOURCE_ONLY_KEYWORDS = frozenset(
    {"capture_screenshot", "capture_pagesource", "get_screen_elements"}
)
_COORDINATE_TAP_KEYWORDS = frozenset({"press_by_coordinates", "press_by_percentage"})
ENV_ALLOW_COORDINATE_TAPS = "OPTICS_MCP_ALLOW_COORDINATE_TAPS"
ENV_TOOLSET = "OPTICS_MCP_TOOLSET"
ENV_SESSION_IDLE_S = "OPTICS_MCP_SESSION_IDLE_S"
ENV_CALL_TIMEOUT_S = "OPTICS_MCP_CALL_TIMEOUT_S"
TOOLSETS = ("core", "full")
CORE_TOOLS = (
    "start_session", "terminate_session", "observe", "tap", "type_text", "scroll",
    "press_key", "launch_app", "check", "run_keyword",
)
_DEFAULT_IDLE_S = 900
_DEFAULT_CALL_TIMEOUT_S = 100.0
_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})
_RESULT_KEY = expose_api.KEY_RESULT

_MAX_TEXT_CHARS = 8000
_ERROR_ROWS = 40
_SETTLE_PAUSE_S = 0.6
_POLL_S = 0.5
_KEYWORD_TIMEOUT_MAX = 60
_TIMEOUT_PARAMS = ("timeout", "timeout_str")
_JPEG_EDGE = 1024
_JPEG_QUALITY = 70
_ATTR_VALUE_RE = re.compile(r"""(?<![\w-])([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_CSS_ID_RE = re.compile(r"#([\w-]+)")
_QUOTED_RE = re.compile(r""""[^"]*"|'[^']*'""")
_ID_ATTRS = frozenset({"resource-id", "id", "name", "accessibility-id"})
_TEXT_ATTRS = frozenset({"text", "label", "name", "value", "content-desc", "placeholder", "aria-label", "title"})
_UNREADABLE = "The screen could not be read."
_VISION_STRATEGIES = frozenset({"OCR", "Image"})
_LOCATOR_TYPES = frozenset({"Image", "CSS", "XPath"})
_READ_ONLY_PREFIXES = ("get_", "assert_", "validate_")
_READ_ONLY_KEYWORDS = frozenset({"is_element"})
_DESTRUCTIVE_KEYWORDS = frozenset({"force_terminate_app", "close_and_terminate_app", "execute_script"})
_VARIADIC = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
_EXAMPLES = {
    "observe": 'observe(query="Sign in")',
    "tap": 'tap(target="e7", expect_present=["Welcome"])',
    "type_text": 'type_text(target="e3", text="a@b.c")',
    "scroll": 'scroll(direction="down", until="Orders")',
    "press_key": 'press_key(key="back")',
    "launch_app": 'launch_app(app="https://example.com")',
    "check": 'check(present=["Welcome"])',
}


def _coordinate_taps_allowed() -> bool:
    return os.environ.get(ENV_ALLOW_COORDINATE_TAPS, "").strip().lower() in _TRUE_STRINGS


def _env_number(name: str, default: float) -> float:
    try:
        return max(float(os.environ.get(name, "") or default), 0.0)
    except ValueError:
        return default


def _idle_lease_s() -> int:
    return int(_env_number(ENV_SESSION_IDLE_S, _DEFAULT_IDLE_S))


def _call_timeout_s() -> float:
    return _env_number(ENV_CALL_TIMEOUT_S, _DEFAULT_CALL_TIMEOUT_S) or _DEFAULT_CALL_TIMEOUT_S


def _now() -> float:
    return time.monotonic()


def _require_fastmcp() -> None:
    if _FASTMCP_IMPORT_ERROR is not None:
        raise RuntimeError(
            "The 'mcp' extra is required to run the Optics MCP server. "
            "Install it with: pip install 'optics-framework[mcp]'"
        ) from _FASTMCP_IMPORT_ERROR


def _resolve_toolset(toolset: str | None) -> str:
    name = (toolset or os.environ.get(ENV_TOOLSET, "") or "core").strip().lower()
    if name not in TOOLSETS:
        raise ValueError(f"Unknown MCP toolset '{name}'; use one of {', '.join(TOOLSETS)}")
    return name


def _http_detail(detail: Any) -> str:
    """Render an HTTPException detail (str or optics error payload dict) to text."""
    if isinstance(detail, dict):
        msg = detail.get("message") or detail.get("detail") or detail
        if detail.get("cause"):
            msg = f"{msg} (cause: {detail['cause']})"
        code = detail.get("code")
        return f"[{code}] {msg}" if code else str(msg)
    return str(detail)


def _stringify_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Coerce tool kwargs to the string-only ExecuteRequest.params contract; None is dropped."""
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        if isinstance(value, list):
            out[key] = [str(item) for item in value]
        else:
            out[key] = str(value)
    return out


def _annotations(*, read_only: bool = False, destructive: bool = False, idempotent: bool = False) -> Any:
    return ToolAnnotations(
        readOnlyHint=read_only, destructiveHint=destructive, idempotentHint=idempotent, openWorldHint=True,
    )


def _keyword_annotations(name: str) -> Any:
    if name in _DESTRUCTIVE_KEYWORDS:
        return _annotations(destructive=True)
    read_only = name.startswith(_READ_ONLY_PREFIXES) or name in _READ_ONLY_KEYWORDS
    return _annotations(read_only=read_only, idempotent=read_only)


def _all_keyword_functions() -> dict[str, Callable[..., Any]]:
    functions: dict[str, Callable[..., Any]] = {}
    for cls in _KEYWORD_CLASSES:
        for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            if not name.startswith(("_", "test")):
                functions.setdefault(name, method)
    return functions


def _agent_keywords() -> dict[str, Callable[..., Any]]:
    """The keywords ``run_keyword`` reaches, by slug."""
    hidden = HIDDEN_KEYWORDS if _coordinate_taps_allowed() else HIDDEN_KEYWORDS | COORDINATE_KEYWORDS
    return {
        name: fn for name, fn in _all_keyword_functions().items() if name not in hidden and not is_deprecated(fn)
    }


class RowOut(BaseModel):
    ref: str
    label: str
    cls: str
    act: list[str]
    rid: str | None = None
    loc: str | None = None
    bounds: list[int] | None = None
    was: str | None = None


class CheckOut(BaseModel):
    text: str
    expect: str
    met: bool


class ErrorOut(BaseModel):
    code: str | None = None
    kind: str
    message: str


class TargetOut(BaseModel):
    ref: str | None = None
    label: str = ""
    ladder: list[str] = []
    strategy: str | None = None


class ScreenOut(BaseModel):
    digest: str = ""
    changed: bool = False
    new_screen: bool = False
    added: list[RowOut] = []
    removed: list[RowOut] = []
    relabelled: list[RowOut] = []
    rows: list[RowOut] | None = None


class ActionOutcome(BaseModel):
    ok: bool = False
    verified: Literal["pass", "fail", "unknown"] = "unknown"
    verb: str = ""
    keyword: str | None = None
    target: TargetOut | None = None
    checks: list[CheckOut] = []
    error: ErrorOut | None = None
    screen: ScreenOut | None = None
    result: Any = None


class ObserveOutcome(BaseModel):
    digest: str = ""
    rows: list[RowOut] = []
    hidden: int = 0
    error: ErrorOut | None = None


class CheckOutcome(BaseModel):
    passed: bool = False
    checks: list[CheckOut] = []
    digest: str = ""
    error: ErrorOut | None = None


class SessionOutcome(BaseModel):
    session_id: str = ""
    driver: str = ""
    driver_id: str | None = None
    idle_lease_s: int = 0
    status: str | None = None
    error: ErrorOut | None = None


_TYPED_OUTPUT_FIELDS = frozenset({"ok", "verified", "passed", "session_id", "status", "digest", "hidden"})


def _output_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The fields a client branches on; the rest of the model is described in the docs."""
    properties = {}
    for name, prop in model.model_json_schema()["properties"].items():
        if name in _TYPED_OUTPUT_FIELDS:
            option = next((o for o in prop.get("anyOf", [prop]) if o.get("type") != "null"), {})
            properties[name] = {k: v for k, v in option.items() if k in ("type", "enum")}
    return {"type": "object", "properties": properties}


def _lean_inputs(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop what the schema says by default; fastmcp still rejects unknown arguments."""
    schema.pop("additionalProperties", None)
    for prop in schema.get("properties", {}).values():
        if prop.get("default", 0) in ("", [], {}, None):
            prop.pop("default")
        if prop.get("additionalProperties") is True:
            prop.pop("additionalProperties")
        options = [o for o in prop.pop("anyOf", []) if o.get("type") != "null"]
        if len(options) == 1:
            prop.update(options[0])
    return schema


def _row_out(row: Row, *, scale: float | None = None, was: str | None = None) -> RowOut:
    bounds = None
    if scale is not None and row.bounds:
        bounds = [round(v * scale) for v in row.bounds]
    return RowOut(
        ref=row.ref, label=row.label, cls=row.cls, act=list(row.act),
        rid=row.rid or None, loc=row.loc or None, bounds=bounds, was=was,
    )


def _checks_out(checks: Sequence[Check]) -> list[CheckOut]:
    return [CheckOut(text=c.text, expect=c.expect, met=c.met) for c in checks]


def _screen_out(d: Diff | None, cur: Snapshot, *, full: bool) -> ScreenOut:
    out = ScreenOut(digest=cur.digest)
    if d is not None:
        out.changed, out.new_screen = d.changed, d.new_screen
        out.added = [_row_out(r) for r in d.added]
        out.removed = [_row_out(r) for r in d.removed]
        out.relabelled = [_row_out(r, was=old) for r, old in d.relabelled]
    if full or (d is not None and d.new_screen):
        out.rows = [_row_out(r) for r in cur.rows]
    return out


@dataclass
class StepRecord:
    tool: str
    calls: list[KeywordCall]
    ok: bool
    verified: str
    elements: dict[str, tuple[Row, list[str]]] = field(default_factory=dict)
    absent: bool = False
    notes: list[str] = field(default_factory=list)
    screen: Snapshot | None = None


@dataclass
class _SessionState:
    refs: RefTable
    observer: Any
    driver: str = ""
    steps: list[StepRecord] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    busy: bool = False
    last_used: float = field(default_factory=_now)


_STATES: dict[str, _SessionState] = {}


@dataclass
class _Reply:
    text: str
    structured: dict[str, Any]
    is_error: bool = False
    images: list[Any] = field(default_factory=list)


class _Failure(Exception):
    """A call that cannot go on; ``kind`` is an ``ADVICE`` key."""

    def __init__(
        self, kind: str, fact: str, *, code: str | None = None, snap: Snapshot | None = None,
        extra: Sequence[str] = (), message: str = "",
    ) -> None:
        super().__init__(fact)
        self.kind, self.fact, self.code, self.snap = kind, fact, code, snap
        self.extra, self.message = list(extra), message or fact


class _Unreadable(_Failure):
    """The element source could not list the screen; never the same as an empty screen."""

    def __init__(self) -> None:
        super().__init__("other", _UNREADABLE)


def _fit(lines: list[str]) -> str:
    text = "\n".join(lines)
    if len(text) <= _MAX_TEXT_CHARS:
        return text
    kept: list[str] = []
    size = 80
    for line in lines:
        if size + len(line) + 1 > _MAX_TEXT_CHARS:
            break
        kept.append(line)
        size += len(line) + 1
    kept.append(f'… {len(lines) - len(kept)} more lines cut; observe(query="…") to find one')
    return "\n".join(kept)


def _failure_reply(failure: _Failure, base: dict[str, Any] | None = None) -> _Reply:
    head = f"[{failure.code}] {failure.fact}" if failure.code else failure.fact
    lines = [head, *failure.extra, f"Next: {ADVICE.get(failure.kind, ADVICE['other'])}"]
    structured = dict(base or {})
    if failure.snap is not None:
        lines.append(render_rows(failure.snap))
        if "verb" in structured:
            structured["screen"] = ScreenOut(
                digest=failure.snap.digest, rows=[_row_out(r) for r in failure.snap.rows]
            ).model_dump(exclude_none=True)
    structured["error"] = ErrorOut(code=failure.code, kind=failure.kind, message=failure.message).model_dump(
        exclude_none=True
    )
    return _Reply(_fit(lines), structured, is_error=True)


def _to_result(reply: _Reply) -> Any:
    content: list[Any] = [TextContent(type="text", text=reply.text), *reply.images]
    return ToolResult(content=content, structured_content=reply.structured, is_error=reply.is_error)


def _error_parts(exc: BaseException) -> tuple[str | None, str, int | None]:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            message = str(detail.get("message") or detail.get("detail") or detail)
            code = detail.get("code")
            return (str(code).removeprefix("Code.") if code else None), message, exc.status_code
        return None, str(detail), exc.status_code
    if isinstance(exc, OpticsError):
        return str(getattr(exc.code, "value", exc.code)), exc.message, exc.status_code
    return None, str(exc), None


def _keyword_failure(exc: BaseException, tool: str, tried: Sequence[str] | str) -> _Failure:
    code, message, status = _error_parts(exc)
    if status == 404 and message == expose_api.SESSION_NOT_FOUND:
        return _Failure("unknown_session", "The session has ended.")
    kind = error_kind(code) if code else "other"
    ladder = " | ".join(tried) if isinstance(tried, (list, tuple)) else str(tried)
    facts = {
        "not_found": f"not found: tried {ladder}.",
        "unsupported": f"{tool} is not supported on this platform.",
        "session_lost": "the driver session ended.",
    }
    return _Failure(kind, facts.get(kind, message), code=code, message=message)


async def _end_session(session_id: str) -> None:
    try:
        await expose_api.delete_session(session_id)
    except HTTPException as exc:
        internal_logger.warning("Ending MCP session %s: %s", session_id, _http_detail(exc.detail))
        await asyncio.to_thread(expose_api.session_manager.terminate_session, session_id)


async def _sweep() -> None:
    """End sessions idle past the lease whose lock is free."""
    lease = _idle_lease_s()
    if not lease:
        return
    now = _now()
    for sid, state in list(_STATES.items()):
        if now - state.last_used > lease and not state.lock.locked() and not state.busy:
            _STATES.pop(sid, None)
            internal_logger.info("Ending MCP session %s after %d s idle", sid, lease)
            await _end_session(sid)


def _unknown_session(sid: str) -> _Failure:
    lease = _idle_lease_s()
    why = f"ended after {lease} s idle, or never started" if lease else "ended, or never started"
    return _Failure("unknown_session", f"No session '{sid}' ({why}).")


async def _state(session_id: str) -> tuple[str, _SessionState]:
    await _sweep()
    sessions = expose_api.session_manager.sessions
    sid = (session_id or "").strip()
    if not sid:
        open_ids = sorted(sessions)
        if len(open_ids) != 1:
            if not open_ids:
                raise _Failure("unknown_session", "No session is open.")
            raise _Failure(
                "unknown_session", f"session_id is required; open sessions: {', '.join(open_ids)}."
            )
        sid = open_ids[0]
    session = sessions.get(sid)
    if session is None:
        _STATES.pop(sid, None)
        raise _unknown_session(sid)
    state = _STATES.get(sid)
    if state is None:
        observer = await asyncio.to_thread(session.optics.build, ActionKeyword)
        state = _STATES.setdefault(sid, _SessionState(RefTable(), observer))
    state.last_used = _now()
    return sid, state


async def _forget(sid: str) -> None:
    _STATES.pop(sid, None)
    try:
        await asyncio.to_thread(expose_api.session_manager.terminate_session, sid)
    except Exception as exc:  # noqa: BLE001 - the driver is already gone
        internal_logger.warning("Releasing lost session %s: %s", sid, exc)


async def _guarded(
    session_id: str, tool: str, base: dict[str, Any],
    work: Callable[[str, _SessionState], Awaitable[_Reply]],
) -> _Reply:
    """Run ``work`` under the session's lock, the busy flag and the call ceiling."""
    try:
        sid, state = await _state(session_id)
    except _Failure as failure:
        return _failure_reply(failure, base)
    if state.busy:
        return _failure_reply(_Failure("busy", f"Session {sid} is busy."), base)

    async def locked() -> _Reply:
        async with state.lock:
            try:
                return await work(sid, state)
            except TargetError as exc:
                rows = await _error_rows(state) if exc.kind == "stale_ref" else None
                failure = _Failure(exc.kind, str(exc), snap=rows)
            except (HTTPException, OpticsError) as exc:
                failure = _keyword_failure(exc, tool, "")
            except _Failure as exc:
                failure = exc
            finally:
                state.last_used = _now()
            if failure.kind == "session_lost":
                await _forget(sid)
            return _failure_reply(failure, base)

    task = asyncio.ensure_future(locked())
    task.add_done_callback(lambda _: setattr(state, "busy", False))
    timeout = _call_timeout_s()
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout)
    except TimeoutError:
        state.busy = True
        return _failure_reply(_Failure("timeout", f"Did not finish in {timeout:g} s."), base)


def _read_sync(state: _SessionState, max_rows: int = 80, query: str = "", png: bytes | None = None) -> Snapshot:
    elements = read_screen(state.observer, png)
    if elements is None:
        raise _Unreadable()
    return state.refs.snapshot(elements, max_rows=max_rows, query=query)


async def _read(state: _SessionState, max_rows: int = 80, query: str = "") -> Snapshot:
    return await asyncio.to_thread(_read_sync, state, max_rows, query)


def _try_read(read: Callable[[], Snapshot]) -> Snapshot | None:
    """``read()``, or None when the screen could not be read."""
    try:
        return read()
    except _Unreadable:
        return None


async def _error_rows(state: _SessionState) -> Snapshot | None:
    try:
        return await _read(state, _ERROR_ROWS)
    except Exception as exc:  # noqa: BLE001 - rows only help the message
        internal_logger.debug("Reading rows for an MCP error failed: %s", exc)
        return None


async def _run_call(sid: str, call: KeywordCall) -> tuple[Any, str | None, str | None]:
    """Run one keyword; returns its result, the winning strategy and a silent error, if any."""
    request = expose_api.ExecuteRequest(mode=expose_api.MODE_KEYWORD, keyword=call.keyword, params=call.params)
    with capture_keyword_logs() as records:
        response = await expose_api.execute_keyword(sid, request)
    data = getattr(response, "data", None) or {}
    result = data if "healed" in data else data.get(_RESULT_KEY)
    return result, winning_strategy(records), silent_error(records)


def _short(value: Any, limit: int = 400) -> str:
    text = " ".join(str(value).split())
    return text[:limit] + "…" if len(text) > limit else text


def _clamp_timeout(value: str) -> str:
    try:
        return str(min(float(value), _KEYWORD_TIMEOUT_MAX)).removesuffix(".0")
    except ValueError:
        return value


def _example_call(keyword: str, func: Callable[..., Any]) -> str:
    required = [p.name for p in visible_params(func) if p.default is inspect.Parameter.empty]
    params = ", ".join(f'"{n}": "{"e7" if "element" in n else f"<{n}>"}"' for n in required)
    return f'run_keyword(keyword="{keyword}", params={{{params}}})'


def _params_line(func: Callable[..., Any]) -> str:
    names = [
        p.name if p.default is inspect.Parameter.empty else f"{p.name}?" for p in visible_params(func)
    ]
    return f"Params: {', '.join(names) or 'none'}"


def _invalid(tool: str, hint: str, keyword: str = "", keywords: dict[str, Callable[..., Any]] | None = None) -> _Failure:
    if keyword and keywords and keyword in keywords:
        extra = [_params_line(keywords[keyword]), f"Example: {_example_call(keyword, keywords[keyword])}"]
    else:
        extra = [f"Example: {_EXAMPLES[tool]}"] if tool in _EXAMPLES else []
    return _Failure("invalid_params", hint, extra=extra)


def _target_text(row: Row | None, raw: str) -> str:
    if row is not None:
        return f'{row.ref} "{row.label}"' if row.label else row.ref
    return f'"{raw}"' if raw else ""


def _head(tool: str, row: Row | None, target: str, direction: str, until: str, keyword: str) -> str:
    parts = [tool]
    if tool == "run_keyword":
        parts.append(keyword)
    if tool == "scroll":
        parts.append(direction)
        if until:
            parts.append(f'until "{until}"')
    elif tool == "press_key":
        parts.append(target)
    else:
        parts.append(_target_text(row, target))
    return " ".join(p for p in parts if p)


def _ladder(row: Row, snap: Snapshot) -> tuple[list[str], bool]:
    """The live ladder of ``row``; a row with no unique locator falls back to its label."""
    try:
        return live_ladder(row, snap), False
    except TargetError:
        return acting_ladder(row, snap), True


def _expand_refs(state: _SessionState, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Row]]:
    """Element params naming a ref become that row's live ladder; timeouts are clamped."""
    snap = state.refs.latest()
    expanded: dict[str, Any] = {}
    rows: dict[str, Row] = {}
    for name, value in params.items():
        if "element" in name and isinstance(value, str) and REF_RE.match(value.strip()):
            row = state.refs.resolve(value)
            if row is not None and snap is not None:
                expanded[name] = live_ladder(row, snap)
                rows[name] = row
                continue
        if name in _TIMEOUT_PARAMS and isinstance(value, str):
            value = _clamp_timeout(value)
        expanded[name] = value
    return expanded, rows


@dataclass
class _Act:
    tool: str
    verb: str
    target: str = ""
    text: str = ""
    direction: str = ""
    until: str = ""
    timeout_s: int = 10
    repeat: int = 1
    clear_first: bool = False
    keyword: str = ""
    params: dict[str, str] = field(default_factory=dict)
    present: list[str] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    screen: str = "diff"


async def _perform(sid: str, state: _SessionState, act: _Act) -> _Reply:
    """Resolve the target, run the planned keywords, re-read the screen and judge the step."""
    prev = state.refs.latest() or await asyncio.to_thread(_try_read, partial(_read_sync, state))
    keywords = _agent_keywords()
    row: Row | None = None
    ladder: list[str] | None = None
    by_label = False
    params: dict[str, Any] = {}
    element_rows: dict[str, Row] = {}
    if act.verb in ("tap", "type"):
        row = state.refs.resolve(act.target)
        if row is not None:
            ladder, by_label = _ladder(row, prev)
    elif act.verb == "keyword":
        params, element_rows = _expand_refs(state, act.params)
        row = next(iter(element_rows.values()), None)
    hint = validate(
        act.verb, row if act.verb != "keyword" else None, act.target, act.text, act.direction, act.until,
        keyword=act.keyword, params=params if act.verb == "keyword" else None, keywords=keywords, snap=prev,
    )
    if hint:
        raise _invalid(act.tool, hint, act.keyword, keywords)
    calls = plan_call(
        act.verb, row if act.verb != "keyword" else None, act.target, text=act.text, direction=act.direction,
        until=act.until, timeout_s=act.timeout_s, repeat=act.repeat, clear_first=act.clear_first,
        ladder=ladder, keyword=act.keyword, params=params if act.verb == "keyword" else None, keywords=keywords,
        driver=state.driver,
    )
    tried: list[str] | str = ladder or act.target
    if act.verb == "keyword" and element_rows:
        tried = next(iter(params[name] for name in element_rows))
    ok, result, strategy, failure = True, None, None, None
    for call in calls:
        try:
            result, won, silent = await _run_call(sid, call)
        except (HTTPException, OpticsError) as exc:
            failure = _keyword_failure(exc, act.tool, tried)
            ok = False
            break
        strategy = won or strategy
        if silent:
            failure = _Failure(
                "silent_failure", f"the keyword logged an error and did not raise: {silent}.",
                code="E0401", message=silent,
            )
            ok = False
            break
    if failure is not None and failure.kind in ("session_lost", "unknown_session"):
        raise failure
    typed = typed_value(calls) or (act.text if act.verb == "type" else "")
    if ok:
        # Typed text is not in the rows, so waiting for the screen to change only adds delay.
        reads = 0 if typed else 2
        read = partial(
            settle, partial(_read_sync, state), prev.digest if prev else "", pause_s=_SETTLE_PAUSE_S,
            extra_reads=reads,
        )
    else:
        read = partial(_read_sync, state, _ERROR_ROWS)
    cur = await asyncio.to_thread(_try_read, read)
    d = diff(prev, cur) if cur is not None else None
    checks: list[Check] = []
    untested: list[Check] = []
    if cur is not None:
        checks = check_expectations(cur, act.present, act.absent, before=prev)
        kept = without_typed_value(checks, typed)
        untested = [c for c in checks if c not in kept]
        checks = kept
    if d is not None:
        verified = judge_step(ok, d, checks)
    else:
        verified = "unknown" if ok else "fail"
    vision = (row is None or by_label) and strategy in _VISION_STRATEGIES
    if vision and verified == "pass":
        verified = "unknown"
    saved: dict[str, tuple[Row, list[str]]] = {}
    slots = {"element": row} if row is not None and act.verb != "keyword" else element_rows
    for name, slot_row in slots.items():
        with contextlib.suppress(TargetError):
            rungs = saved_ladder(slot_row, prev)
            if by_label and ladder:
                rungs = [*ladder, *(r for r in rungs if r not in ladder)]
            saved[name] = (slot_row, rungs)
    state.steps.append(StepRecord(act.tool, calls, ok, verified, saved, screen=prev))
    keyword = calls[-1].keyword if calls else None
    target = None
    if row is not None or act.target:
        target = TargetOut(
            ref=row.ref if row else None, label=row.label if row else act.target,
            ladder=list(tried) if isinstance(tried, list) else [tried], strategy=strategy,
        )
    outcome = ActionOutcome(
        ok=ok, verified=verified, verb=act.tool, keyword=keyword, target=target, checks=_checks_out(checks),
        screen=_screen_out(d, cur, full=act.screen == "full" or not ok) if cur is not None else None,
        result=result,
    )
    head = _head(act.tool, row, act.target, act.direction, act.until, act.keyword)
    via = f" ({strategy})" if strategy else ""
    status = f"ok{via}" if ok else "FAIL"
    unasked = ok and cur is not None and not checks and not untested
    hint_line = " (add expect_present/expect_absent to verify)" if unasked else ""
    lines = [f"{head} -> {status} · verified: {verified}{hint_line}"]
    lines += [f'check {c.expect} "{c.text}": {"met" if c.met else "not met"}' for c in checks]
    lines += [f'check {c.expect} "{c.text}": not checked, a typed value is not in the rows' for c in untested]
    if cur is None:
        lines.append(f"{_UNREADABLE} Expectations were not checked.")
    if ok and keyword in NON_VERIFYING_KEYWORDS:
        lines.append(f"note: {keyword} reports ok even when nothing moved; read the screen line")
    if by_label:
        lines.append(f"note: {row.ref} has no unique locator, so its label was used")
    if vision:
        lines.append(f'note: "{act.target}" was found by {strategy}, not by an exact locator')
    if ok and act.verb == "keyword" and result is not None:
        lines.append(f"result: {_short(result)}")
    if failure is not None:
        head_line = f"[{failure.code}] {failure.fact}" if failure.code else failure.fact
        lines += [head_line, *failure.extra, f"Next: {ADVICE.get(failure.kind, ADVICE['other'])}"]
        outcome.error = ErrorOut(code=failure.code, kind=failure.kind, message=failure.message)
        if d is not None:
            lines.append(render_diff(d, cur) if failure.kind == "silent_failure" else render_rows(cur))
    elif d is not None and act.screen == "full":
        lines.append(render_rows(cur))
    elif d is not None and act.screen == "diff":
        lines.append(render_diff(d, cur))
    return _Reply(_fit(lines), outcome.model_dump(exclude_none=True), is_error=not ok)


async def _act(act: _Act, session_id: str) -> Any:
    reply = await _guarded(
        session_id, act.tool, {"verb": act.tool, "ok": False, "verified": "fail"},
        lambda sid, state: _perform(sid, state, act),
    )
    return _to_result(reply)


def _is_locator(item: str) -> bool:
    return utils.determine_element_type(item.strip()) in _LOCATOR_TYPES


async def _check(sid: str, state: _SessionState, present: list[str], absent: list[str], timeout_s: int) -> _Reply:
    present = [p for p in present if p.strip()]
    absent = [a for a in absent if a.strip()]
    hint = validate("check", None, present=present, absent=absent)
    if hint:
        raise _invalid("check", hint)
    bad = [a for a in absent if _is_locator(a)]
    if bad:
        raise _invalid("check", f'absent "{bad[0]}" is a locator; use visible text or a ref')
    locators = [p for p in present if _is_locator(p)]
    texts = [p.strip().removeprefix("text=") for p in present if not _is_locator(p)]
    deadline = _now() + timeout_s
    while True:
        snap = await asyncio.to_thread(_try_read, partial(_read_sync, state))
        checks = check_expectations(snap, texts, absent) if snap is not None else []
        if (snap is not None and all(c.met for c in checks)) or _now() >= deadline:
            break
        await asyncio.sleep(_POLL_S)
    if snap is None:
        raise _Unreadable()
    for item in locators:
        remaining = str(max(1, math.ceil(deadline - _now())))
        call = KeywordCall(
            "assert_presence", {"elements": item, "timeout_str": remaining, "rule": "any", "fail": "False"}, None,
        )
        try:
            result, _, _ = await _run_call(sid, call)
        except (HTTPException, OpticsError) as exc:
            failure = _keyword_failure(exc, "check", item)
            if failure.kind in ("session_lost", "unknown_session"):
                raise failure from exc
            result = False
        checks.append(Check(item, "present", bool(result)))
    passed = all(c.met for c in checks)
    items, notes = recordable_present(present, snap)
    recordable = plan_call("check", None, "", present=items) if items else []
    state.steps.append(StepRecord(
        "check", recordable, passed, "pass" if passed else "fail", absent=bool(absent), notes=notes,
    ))
    lines = [f"check -> {'passed' if passed else 'not passed'}"]
    lines += [f'{c.expect} "{c.text}": {"met" if c.met else "not met"}' for c in checks]
    if not passed:
        lines.append(render_rows(replace(snap, rows=snap.rows[:_ERROR_ROWS], hidden=snap.hidden + max(len(snap.rows) - _ERROR_ROWS, 0))))
    outcome = CheckOutcome(passed=passed, checks=_checks_out(checks), digest=snap.digest)
    return _Reply(_fit(lines), outcome.model_dump(exclude_none=True))


def _jpeg(png: bytes) -> tuple[bytes, float]:
    frame = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise _Failure("other", "The screenshot could not be decoded.")
    height, width = frame.shape[:2]
    scale = min(1.0, _JPEG_EDGE / max(height, width))
    if scale < 1.0:
        frame = cv2.resize(frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok:
        raise _Failure("other", "The screenshot could not be encoded.")
    return buffer.tobytes(), scale


async def _observe(state: _SessionState, query: str, screenshot: bool, max_rows: int) -> _Reply:
    png = None
    if screenshot:
        png = await asyncio.to_thread(state.observer.strategy_manager.capture_screenshot_bytes)
    snap = await asyncio.to_thread(_read_sync, state, max_rows, query, png)
    images: list[Any] = []
    scale = None
    if png:
        jpeg, scale = _jpeg(png)
        images.append(Image(data=jpeg, format="jpeg").to_image_content())
    outcome = ObserveOutcome(digest=snap.digest, rows=[_row_out(r, scale=scale) for r in snap.rows], hidden=snap.hidden)
    return _Reply(_fit(render_rows(snap).split("\n")), outcome.model_dump(exclude_none=True), images=images)


def _positional(keyword: str, values: dict[str, Any]) -> list[str]:
    return runner_params(param_names(_all_keyword_functions().get(keyword)), values)


def _locator_key(locator: str) -> str:
    return "".join(locator.strip().removeprefix("xpath=").removeprefix("css=").replace("'", '"').split())


def _row_for_locator(snap: Snapshot | None, element: Any) -> Row | None:
    """The row a raw locator names by its loc, id rung or id, else by the ids and texts it quotes."""
    if snap is None or not isinstance(element, str) or not element.strip():
        return None
    wanted = _locator_key(element)
    for row in snap.all_rows:
        if row.rid and _locator_key(row.rid) == wanted:
            return row
        with contextlib.suppress(TargetError):
            if any(_locator_key(rung) == wanted for rung in live_ladder(row, snap)):
                return row
    ids = set(_CSS_ID_RE.findall(_QUOTED_RE.sub("", element)))
    texts: set[str] = set()
    for name, double, single in _ATTR_VALUE_RE.findall(element):
        if name in _ID_ATTRS:
            ids.add(double or single)
        if name in _TEXT_ATTRS:
            texts.add(double or single)
    named = [r for r in snap.all_rows if (r.rid and r.rid in ids) or (r.label and r.label in texts)]
    pool = [r for r in named if "input" in r.act] or named
    return next((r for r in pool if holds_secret(r, snap)), pool[0] if pool else None)


def _secret(row: Row | None, element: Any, snap: Snapshot | None) -> bool:
    if row is not None and holds_secret(row, snap):
        return True
    raw = str(element or "")
    if (
        row is None and snap is not None and utils.determine_element_type(raw) in ("XPath", "CSS")
        and any(holds_secret(r, snap) for r in snap.all_rows)
    ):
        return True
    words = [row.label, row.rid, row.loc] if row is not None else []
    return "SecureTextField" in raw or any(is_secret_name(w) for w in [*words, raw] if w)


def _steps_payload(state: _SessionState) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    module_rows: list[list[str]] = []
    element_rows: list[list[str]] = []
    names: dict[str, str] = {}
    taken: set[str] = set()
    notes: list[str] = []
    focused: tuple[Row | None, Any, Snapshot | None] = (None, None, None)
    for n, step in enumerate(state.steps, 1):
        display = ", ".join(expose_api._humanize_keyword(c.keyword) for c in step.calls) or "Check"
        steps.append({"n": n, "verb": step.tool, "keyword": display, "ok": step.ok, "verified": step.verified})
        if not step.ok:
            continue
        if step.absent:
            notes.append("absent checks are not recordable as keywords")
        notes += step.notes
        for call in step.calls:
            values: dict[str, Any] = dict(call.params)
            for param, (row, ladder) in step.elements.items():
                if param not in values:
                    continue
                if row.ref not in names:
                    names[row.ref] = element_name(row, taken)
                    taken.add(names[row.ref])
                    element_rows.append([names[row.ref], *ladder])
                values[param] = f"${{{names[row.ref]}}}"
            value_param = TYPED_VALUE_PARAMS.get(call.keyword)
            if value_param in values:
                if "element" in step.elements:
                    row, element, screen = step.elements["element"][0], None, step.screen
                elif "element" in call.params:
                    element, screen = call.params["element"], step.screen
                    row = _row_for_locator(screen, element)
                else:
                    row, element, screen = focused
                if _secret(row, element, screen):
                    base = Row(ref="", label=row.label, cls="", act=(), rid=row.rid) if row else Row("", "secret", "", ())
                    secret_name = element_name(base, taken)
                    taken.add(secret_name)
                    element_rows.append([secret_name, "<redacted>"])
                    values[value_param] = f"${{{secret_name}}}"
                    notes.append(f"replace <redacted> for {secret_name} before running")
            module_rows.append(["recorded", expose_api._humanize_keyword(call.keyword), *_positional(call.keyword, values)])
        if "element" in step.elements:
            focused = (step.elements["element"][0], None, step.screen)
        elif raw := next((c.params["element"] for c in reversed(step.calls) if "element" in c.params), None):
            focused = (_row_for_locator(step.screen, raw), raw, step.screen)
    id_width = max([1, *(len(r) - 1 for r in element_rows)])
    element_header = ["Element_Name", "Element_ID", *(f"Element_ID_{i}" for i in range(2, id_width + 1))]
    param_width = max([2, *(len(r) - 2 for r in module_rows)])
    module_header = ["module_name", "module_step", *(f"param_{i}" for i in range(1, param_width + 1))]
    return {
        "steps": steps,
        "modules_csv": _write_csv(module_header, module_rows),
        "elements_csv": _write_csv(element_header, element_rows),
        "notes": list(dict.fromkeys(notes)),
    }


def _write_csv(header: list[str], rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([utils.escape_csv_value(str(v)) for v in row])
    return buffer.getvalue()


def _reflect_keyword_params(method: Callable[..., Any]) -> list[inspect.Parameter]:
    """Real (name, default) params of an API method, excluding self."""
    return [
        p for name, p in inspect.signature(method).parameters.items()
        if name != "self" and p.kind not in _VARIADIC
    ]


def _make_keyword_tool(slug: str, params: list[inspect.Parameter]) -> Callable[..., Any]:
    """A str-typed wrapper that dispatches ``slug`` through execute_keyword; element refs expand."""

    async def wrapper(**kwargs: Any) -> Any:
        session_id = kwargs.pop("session_id")
        await _sweep()
        state = _STATES.get(session_id)
        request_params = _stringify_params(kwargs)
        if state is not None:
            try:
                request_params, _ = _expand_refs(state, request_params)
            except TargetError as exc:
                raise ToolError(str(exc)) from exc
            state.last_used = _now()
        request = expose_api.ExecuteRequest(
            mode=expose_api.MODE_KEYWORD, keyword=slug, params=request_params,
        )
        lock = state.lock if state is not None else contextlib.nullcontext()
        try:
            async with lock:
                response = await expose_api.execute_keyword(session_id, request)
        except HTTPException as exc:
            raise ToolError(_http_detail(exc.detail)) from exc
        except OpticsError as exc:  # pragma: no cover - defensive
            raise ToolError(str(exc)) from exc
        data = getattr(response, "data", None) or {}
        if "healed" in data:
            return data
        return data.get(_RESULT_KEY, data)

    synth = [inspect.Parameter("session_id", inspect.Parameter.KEYWORD_ONLY, annotation=str)]
    annotations: dict[str, Any] = {"session_id": str}
    for param in params:
        if param.default is inspect.Parameter.empty:
            synth.append(inspect.Parameter(param.name, inspect.Parameter.KEYWORD_ONLY, annotation=str))
            annotations[param.name] = str
        elif param.default is None:
            synth.append(inspect.Parameter(
                param.name, inspect.Parameter.KEYWORD_ONLY, annotation=str | None, default=None,
            ))
            annotations[param.name] = str | None
        else:
            synth.append(inspect.Parameter(
                param.name, inspect.Parameter.KEYWORD_ONLY, annotation=str, default=str(param.default),
            ))
            annotations[param.name] = str
    wrapper.__signature__ = inspect.Signature(synth)  # type: ignore[attr-defined]
    wrapper.__annotations__ = {**annotations, "return": Any}
    wrapper.__name__ = slug
    return wrapper


def _is_keyword_tool(name: str, seen: set[str], allow_coordinate_taps: bool) -> bool:
    if name.startswith(("_", "test")) or name in seen:
        return False
    if name in _RESOURCE_ONLY_KEYWORDS:
        return False
    return allow_coordinate_taps or name not in _COORDINATE_TAP_KEYWORDS


def _iter_keyword_tools() -> list[tuple[str, str, Callable[..., Any]]]:
    """(slug, description, wrapper) for every keyword of the full toolset."""
    tools: list[tuple[str, str, Callable[..., Any]]] = []
    seen: set[str] = set()
    allow_coordinate_taps = _coordinate_taps_allowed()
    for cls in _KEYWORD_CLASSES:
        for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            if not _is_keyword_tool(name, seen, allow_coordinate_taps):
                continue
            seen.add(name)
            description = inspect.getdoc(method) or expose_api._humanize_keyword(name)
            tools.append((name, description, _make_keyword_tool(name, _reflect_keyword_params(method))))
    return tools


async def _observe_keyword(session_id: str, keyword: str, params: dict[str, Any] | None = None) -> Any:
    """Run a read-only observer keyword and return its unwrapped result."""
    try:
        response = await expose_api.run_keyword_endpoint(session_id, keyword, params)
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


_SID = Annotated[str, Field(description="Omit if only one is open.")]
_TARGET = Annotated[str, Field(description="Ref like e7, exact label, or locator (css=, xpath=, text=).")]
_PRESENT = Annotated[list[str], Field(description="Texts/refs shown after.")]
_ABSENT = Annotated[list[str], Field(description="Texts/refs gone after.")]
_SCREEN = Annotated[Literal["diff", "full", "none"], Field(description="Screen in reply.")]


def build_server(toolset: str | None = None) -> "FastMCP":
    """The FastMCP server with the ``core`` (default) or ``full`` toolset and the state resources."""
    _require_fastmcp()
    toolset = _resolve_toolset(toolset)
    mcp = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS, version=VERSION)
    act_schema = _output_schema(ActionOutcome)

    def add(fn: Callable[..., Any], name: str, annotations: Any, output: type[BaseModel], description: str) -> None:
        tool = Tool.from_function(
            fn, name=name, description=description, annotations=annotations,
            output_schema=act_schema if output is ActionOutcome else _output_schema(output),
        )
        tool.parameters = _lean_inputs(tool.parameters)
        mcp.add_tool(tool)

    async def start_session(
        driver: Annotated[str, Field(description="appium, playwright, selenium, ...")] = "appium",
        app: Annotated[str, Field(description="URL or app id to open.")] = "",
        url: Annotated[str, Field(description="Driver server URL.")] = "",
        capabilities: Annotated[dict[str, Any], Field(description="Driver capabilities.")] = {},  # noqa: B006
        elements_sources: Annotated[
            list[str] | None, Field(description="Omit for the driver's own.")
        ] = None,
        text_detection: Annotated[list[str], Field(description="OCR engines.")] = [],  # noqa: B006
        image_detection: Annotated[list[str], Field(description="Image engines.")] = [],  # noqa: B006
        project_path: Annotated[str, Field(description="Folder with image templates.")] = "",
        ai_self_heal: Annotated[bool | None, Field(description="Omit for the default.")] = None,
        llm_provider: Annotated[str, Field(description="Self-heal LLM engine.")] = "",
        llm_model: Annotated[str, Field(description="Self-heal LLM model.")] = "",
        strict_element_match: Annotated[bool, Field(description="No fuzzy text matches.")] = False,
    ) -> Any:
        if url or capabilities:
            driver_sources: list[Any] = [
                {driver: {"enabled": True, "url": url or None, "capabilities": dict(capabilities)}}
            ]
        else:
            driver_sources = [driver]
        config = expose_api.SessionConfig(
            driver_sources=driver_sources,
            elements_sources=elements_sources if elements_sources is not None else _default_sources_for_driver(driver),
            text_detection=list(text_detection),
            image_detection=list(image_detection),
            project_path=project_path or None,
            ai_self_heal=ai_self_heal,
            llm_provider=llm_provider or None,
            llm_model=llm_model or None,
            strict_element_match=strict_element_match,
        )
        try:
            response = await expose_api.create_session(config)
        except HTTPException as exc:
            code, message, _ = _error_parts(exc)
            failure = _Failure(error_kind(code) if code else "other", message, code=code)
            return _to_result(_failure_reply(failure))
        sid = response.session_id
        await _sweep()
        session = expose_api.session_manager.get_session(sid)
        observer = await asyncio.to_thread(session.optics.build, ActionKeyword) if session else None
        _STATES[sid] = _SessionState(RefTable(), observer, driver=driver.strip().lower())
        lease = _idle_lease_s()
        outcome = SessionOutcome(
            session_id=sid, driver=driver, driver_id=str(response.driver_id) if response.driver_id else None,
            idle_lease_s=lease, status="started",
        ).model_dump(exclude_none=True)
        head = f"session {sid} ({driver}) · " + (f"idle lease {lease} s" if lease else "no idle lease")
        if not app:
            return _to_result(_Reply(head, outcome))
        launch = _Act("launch_app", "open", text=app, screen="full")
        reply = await _guarded(sid, "launch_app", {}, lambda s, st: _perform(s, st, launch))
        if reply.is_error:
            outcome["error"] = reply.structured.get("error")
        return _to_result(_Reply(f"{head}\n{reply.text}", outcome, is_error=reply.is_error))

    async def terminate_session(session_id: _SID = "") -> Any:
        try:
            sid, _ = await _state(session_id)
        except _Failure as failure:
            return _to_result(_failure_reply(failure))
        _STATES.pop(sid, None)
        await _end_session(sid)
        outcome = SessionOutcome(session_id=sid, status="terminated").model_dump(exclude_none=True)
        return _to_result(_Reply(f"session {sid} terminated", outcome))

    async def observe(
        query: Annotated[str, Field(description="Only rows whose label or id holds this.")] = "",
        screenshot: Annotated[bool, Field(description="Add a JPEG of the screen.")] = False,
        max_rows: Annotated[int, Field(ge=1, le=300, description="Most rows.")] = 80,
        session_id: _SID = "",
    ) -> Any:
        reply = await _guarded(
            session_id, "observe", {}, lambda sid, state: _observe(state, query, screenshot, max_rows),
        )
        return _to_result(reply)

    async def tap(
        target: _TARGET,
        repeat: Annotated[int, Field(ge=1, le=20, description="Taps in a row.")] = 1,
        expect_present: _PRESENT = [],  # noqa: B006 - read only
        expect_absent: _ABSENT = [],  # noqa: B006 - read only
        screen: _SCREEN = "diff",
        session_id: _SID = "",
    ) -> Any:
        return await _act(_Act(
            "tap", "tap", target=target, repeat=repeat, present=expect_present, absent=expect_absent, screen=screen,
        ), session_id)

    async def type_text(
        target: _TARGET,
        text: Annotated[str, Field(description="Text to type.")],
        clear_first: Annotated[bool, Field(description="Clear first.")] = False,
        expect_present: _PRESENT = [],  # noqa: B006 - read only
        expect_absent: _ABSENT = [],  # noqa: B006 - read only
        screen: _SCREEN = "diff",
        session_id: _SID = "",
    ) -> Any:
        return await _act(_Act(
            "type_text", "type", target=target, text=text, clear_first=clear_first,
            present=expect_present, absent=expect_absent, screen=screen,
        ), session_id)

    async def scroll(
        direction: Annotated[
            Literal["up", "down", "left", "right"],
            Field(description="down shows what is below."),
        ],
        until: Annotated[str, Field(description="Scroll until this text shows (up/down).")] = "",
        timeout_s: Annotated[int, Field(ge=1, le=60, description="Limit for until.")] = 10,
        expect_present: _PRESENT = [],  # noqa: B006 - read only
        expect_absent: _ABSENT = [],  # noqa: B006 - read only
        screen: _SCREEN = "diff",
        session_id: _SID = "",
    ) -> Any:
        return await _act(_Act(
            "scroll", "scroll", direction=direction, until=until, timeout_s=timeout_s,
            present=expect_present, absent=expect_absent, screen=screen,
        ), session_id)

    async def press_key(
        key: Annotated[str, Field(description="back (any driver), an Android KeyEvent number, or a web key.")],
        expect_present: _PRESENT = [],  # noqa: B006 - read only
        expect_absent: _ABSENT = [],  # noqa: B006 - read only
        screen: _SCREEN = "diff",
        session_id: _SID = "",
    ) -> Any:
        return await _act(_Act(
            "press_key", "key", target=key, text=key, present=expect_present, absent=expect_absent, screen=screen,
        ), session_id)

    async def launch_app(
        app: Annotated[str, Field(description="URL, package or bundle id; empty: the configured app.")] = "",
        expect_present: _PRESENT = [],  # noqa: B006 - read only
        expect_absent: _ABSENT = [],  # noqa: B006 - read only
        screen: _SCREEN = "diff",
        session_id: _SID = "",
    ) -> Any:
        return await _act(_Act(
            "launch_app", "open", target=app, text=app, present=expect_present, absent=expect_absent, screen=screen,
        ), session_id)

    async def check(
        present: Annotated[list[str], Field(description="Texts, refs or locators on screen.")] = [],  # noqa: B006
        absent: Annotated[list[str], Field(description="Texts or refs not on screen.")] = [],  # noqa: B006
        timeout_s: Annotated[int, Field(ge=0, le=30, description="Seconds to wait.")] = 5,
        session_id: _SID = "",
    ) -> Any:
        reply = await _guarded(
            session_id, "check", {}, lambda sid, state: _check(sid, state, present, absent, timeout_s),
        )
        return _to_result(reply)

    keyword_names = tuple(sorted(_agent_keywords()))
    keyword_type = Literal[keyword_names]  # type: ignore[valid-type]

    async def run_keyword(
        keyword: Annotated[keyword_type, Field(description="See optics://keywords.")],
        params: Annotated[dict[str, str], Field(description=(
            "Params by name; an element may be a ref."
        ))] = {},  # noqa: B006 - read only
        expect_present: _PRESENT = [],  # noqa: B006 - read only
        expect_absent: _ABSENT = [],  # noqa: B006 - read only
        screen: _SCREEN = "diff",
        session_id: _SID = "",
    ) -> Any:
        return await _act(_Act(
            "run_keyword", "keyword", keyword=keyword, params=dict(params),
            present=expect_present, absent=expect_absent, screen=screen,
        ), session_id)

    async def screenshot(session_id: str) -> "Image":
        """Capture the current screen and return it as a rendered PNG image."""
        raw = _decode_screenshot(await _observe_keyword(session_id, "capture_screenshot"))
        return Image(data=raw, format="png")

    act_hints = _annotations()
    core = [
        (start_session, "start_session", act_hints, SessionOutcome,
         "Start a session; returns its id and the first screen."),
        (terminate_session, "terminate_session", _annotations(destructive=True, idempotent=True), SessionOutcome,
         "End a session."),
        (observe, "observe", _annotations(read_only=True, idempotent=True), ObserveOutcome,
         "List the screen as rows with refs."),
        (tap, "tap", act_hints, ActionOutcome, "Tap an element."),
        (type_text, "type_text", act_hints, ActionOutcome, "Type text into a field."),
        (scroll, "scroll", act_hints, ActionOutcome, "Scroll the screen."),
        (press_key, "press_key", act_hints, ActionOutcome, "Press a key."),
        (launch_app, "launch_app", act_hints, ActionOutcome, "Open an app or a URL."),
        (check, "check", _annotations(read_only=True, idempotent=True), CheckOutcome,
         "Verdict on what is on the screen."),
        (run_keyword, "run_keyword", _annotations(destructive=True), ActionOutcome,
         "Run any optics keyword."),
    ]
    reflected: list[str] = []
    if toolset == "full":
        for slug, description, wrapper in _iter_keyword_tools():
            mcp.add_tool(Tool.from_function(
                wrapper, name=slug, description=description, annotations=_keyword_annotations(slug),
            ))
            reflected.append(slug)
        mcp.add_tool(Tool.from_function(
            screenshot, name="screenshot", annotations=_annotations(read_only=True, idempotent=True),
        ))
    for fn, name, hints, output, description in core:
        if name not in reflected:
            add(fn, name, hints, output, description)

    @mcp.resource("optics://keywords", mime_type="application/json")
    def keywords_catalog() -> list[dict[str, Any]]:
        """The keywords ``run_keyword`` reaches (name, slug, description, params)."""
        reachable = _agent_keywords()
        return [info.model_dump() for info in expose_api.discover_keywords() if info.keyword_slug in reachable]

    @mcp.resource("optics://project-format", mime_type="text/markdown")
    def project_format() -> str:
        """How optics stores a test suite, so you can write one and run it with `optics execute`."""
        return _PROJECT_FORMAT_DOC

    @mcp.resource("optics://session/{session_id}/screenshot", mime_type="image/png")
    async def screenshot_resource(session_id: str) -> bytes:
        """Current screen as raw PNG bytes."""
        return _decode_screenshot(await _observe_keyword(session_id, "capture_screenshot"))

    @mcp.resource("optics://session/{session_id}/source", mime_type="application/json")
    async def page_source(session_id: str) -> Any:
        """Current page source / UI hierarchy."""
        return await _observe_keyword(session_id, "capture_pagesource")

    @mcp.resource("optics://session/{session_id}/elements", mime_type="application/json")
    async def interactive_elements(session_id: str) -> Any:
        """Compact on-screen elements as {i, label, cls, bounds, act, rid?, loc?}, without refs."""
        return await _observe_keyword(session_id, "get_interactive_elements", {"compact": "true"})

    @mcp.resource("optics://session/{session_id}/screen_elements", mime_type="application/json")
    async def screen_elements(session_id: str) -> Any:
        """Deprecated: captured screen elements; use the elements resource or `observe`."""
        return await _observe_keyword(session_id, "get_screen_elements")

    @mcp.resource("optics://session/{session_id}/steps", mime_type="application/json")
    def session_steps(session_id: str) -> dict[str, Any]:
        """The steps this session ran, with the module and element CSV rows to replay them."""
        state = _STATES.get(session_id)
        if state is None:
            raise ToolError(str(_unknown_session(session_id)))
        return _steps_payload(state)

    return mcp


def run_mcp_server(
    transport: str = "stdio", host: str = "127.0.0.1", port: int = 8090, toolset: str | None = None,
) -> None:
    """Build and run the Optics MCP server on stdio or http."""
    _require_fastmcp()
    if transport == "stdio":
        # A stdio client may never read stderr; a full pipe must not block the logger.
        decouple_console_logs()
    mcp = build_server(toolset)
    internal_logger.info(
        "Starting Optics MCP server (transport=%s%s)",
        transport,
        f", {host}:{port}" if transport != "stdio" else "",
    )
    previous_handler = signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        if transport == "stdio":
            mcp.run(transport="stdio")
        else:
            mcp.run(transport=transport, host=host, port=port)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        _STATES.clear()
        expose_api.session_manager.terminate_all()
    if transport == "stdio" and not drain_console_logs():
        # The listener is stuck writing to an unread stderr while holding the stdout
        # buffer lock, so a normal interpreter shutdown would hang or abort.
        os._exit(0)
