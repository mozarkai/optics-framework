import base64
import binascii
import json
import os
import re
import shutil
import tempfile
import uuid
import inspect
import importlib
import pkgutil
import asyncio
import warnings
import hashlib
from contextlib import asynccontextmanager
from itertools import product
from typing import Annotated, Optional, Dict, Any, List, Union, cast, Callable, Tuple, NamedTuple
from fastapi import FastAPI, HTTPException, Query, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import status
from pydantic import BaseModel, ValidationError
from sse_starlette.sse import EventSourceResponse
from optics_framework.common.session_manager import (
    SessionManager,
    Session,
    SessionOwnedElsewhere,
    SessionStoreUnavailable,
    build_session_store_from_env,
    lease_ttl_from_env,
)
from optics_framework.common.models import ApiData, SessionState
from optics_framework.common.execution import (
    ExecutionEngine,
    ExecutionParams,
)
from optics_framework.common.logging_config import internal_logger, reconfigure_logging
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.common.runner.keyword_register import KeywordRegistry
from optics_framework.common.utils import _is_list_type
from optics_framework.api import ActionKeyword, AppManagement, FlowControl, Verifier
from optics_framework.helper.execute import discover_templates
from optics_framework.helper.version import VERSION

@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    """Run the session lease keepalive for the server's lifetime. The heartbeat
    renews leases of live-but-idle sessions so they are not reclaimed by another
    pod while this one still holds the runtime (a no-op under the in-memory
    store)."""
    session_manager.start_heartbeat()
    try:
        yield
    finally:
        await session_manager.stop_heartbeat()


app = FastAPI(title="Optics Framework API", version="1.0", lifespan=_lifespan)
# Store backend is selected from the environment (in-memory by default, Redis
# for multi-worker/multi-pod deployment). Every endpoint reaches session state
# through this one manager, so the API layer is stateless behind a shared store.
session_manager = SessionManager(
    store=build_session_store_from_env(), lease_ttl_s=lease_ttl_from_env()
)

# --- API / HTTP messages ---
SESSION_NOT_FOUND = "Session not found"
MSG_ONLY_KEYWORD_MODE_SUPPORTED = "Only keyword mode with a keyword is supported"
MSG_INVALID_API_DATA = "Invalid api_data:"
MSG_SESSION_CREATION_FAILED = "Session creation failed:"
MSG_EXECUTION_FAILED = "Execution failed:"
MSG_SESSION_TERMINATION_FAILED = "Session termination failed:"
MSG_INVALID_BASE64_IMAGE = "Invalid base64 image data:"

# --- Execution / request ---
MODE_KEYWORD = "keyword"
RUNNER_TYPE_KEYWORD = "keyword"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAIL = "FAIL"
STATUS_HEARTBEAT = "HEARTBEAT"
STATUS_ERROR = "ERROR"
STATUS_CANCELLED = "CANCELLED"
KEYWORD_LAUNCH_APP = "launch_app"
KEYWORD_CLOSE_AND_TERMINATE_APP = "close_and_terminate_app"
KEY_RESULT = "result"
EXECUTION_ID_HEARTBEAT = "heartbeat"
EXECUTION_ID_UNKNOWN = "unknown"

# --- Config / dependency keys ---
SOURCE_APPIUM = "appium"
KEY_DRIVER_SOURCES = "driver_sources"
KEY_ELEMENTS_SOURCES = "elements_sources"
KEY_TEXT_DETECTION = "text_detection"
KEY_IMAGE_DETECTION = "image_detection"
KEY_API = "api"
KEY_ENABLED = "enabled"
KEY_URL = "url"
KEY_CAPABILITIES = "capabilities"

# --- Response / workspace keys ---
KEY_SCREENSHOT = "screenshot"
KEY_ELEMENTS = "elements"
KEY_SOURCE = "source"
KEY_SOURCE_TIMESTAMP = "sourceTimestamp"
KEY_SCREENSHOT_FAILED = "screenshotFailed"
KEY_TYPE = "type"
KEY_MESSAGE = "message"
KEY_TIMESTAMP = "timestamp"
KEY_DATA = "data"
STATUS_CREATED = "created"
STATUS_STARTED = "started"
STATUS_TERMINATED = "terminated"
STATUS_DETACHED = "detached"
STATUS_IMPORTED = "imported"
STATUS_OK = "ok"
KEY_WORKSPACE_HASH = "workspace_hash"
WORKSPACE_TYPE_HEARTBEAT = "heartbeat"
WORKSPACE_TYPE_ERROR = "error"

# --- Other ---
HEALTH_STATUS_RUNNING = "Optics Framework API is running"
LOG_LEVEL_DEBUG = "DEBUG"
TEMPLATE_EXT_PNG = ".png"
TEMP_DIR_PREFIX = "optics_request_"
PKG_OPTICS_API = "optics_framework.api"
PARAM_FILTER_CONFIG = "filter_config"
DATA_URL_PREFIX = "data:"
BASE64_SEPARATOR = ";base64,"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SessionOwnedElsewhere)
async def _session_owned_elsewhere_handler(_request: Request, exc: SessionOwnedElsewhere):
    """A live lease is held by another instance (Layer 2). Signal a conflict so
    the caller retries against the owning pod rather than seeing a 500."""
    internal_logger.warning("Session %s is owned by another instance", exc.session_id)
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


@app.exception_handler(SessionStoreUnavailable)
async def _session_store_unavailable_handler(_request: Request, exc: SessionStoreUnavailable):
    """The shared session store is unreachable after bounded retries (Layer 2).
    Signal a retryable 503 rather than a 500 so the caller/LB backs off."""
    internal_logger.error("Session store unavailable: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Session store temporarily unavailable"},
    )


class AppiumUpdateRequest(BaseModel):
    """
    Request model for updating Appium session configuration.
    """

    session_id: str
    url: str
    capabilities: Dict[str, Any]


class ExecuteRequest(BaseModel):
    """
    Request model for executing a keyword or test case.
    Supports both positional and named parameters with fallback support.
    Optional template_images allows inline template images for vision-based
    keywords (e.g. Press Element with an image): keys are logical names,
    values are base64-encoded image bytes (raw base64 or data URL).
    """

    mode: str
    test_case: Optional[str] = None
    keyword: Optional[str] = None
    params: Union[List[Union[str, List[str]]], Dict[str, Union[str, List[str]]]] = []
    template_images: Optional[Dict[str, str]] = None


class TemplateUploadRequest(BaseModel):
    """Request model for uploading a template image to a session."""

    name: str
    image_base64: str


class SessionResponse(BaseModel):
    """
    Response model for session creation.
    """

    session_id: str
    driver_id: Optional[str] = None
    status: str = STATUS_CREATED


class ExecutionResponse(BaseModel):
    """
    Response model for execution results.
    """

    execution_id: str
    status: str = STATUS_STARTED
    data: Optional[Dict[str, Any]] = None


class TerminationResponse(BaseModel):
    """
    Response model for session termination.
    """

    status: str = STATUS_TERMINATED


class MigrationResponse(BaseModel):
    """
    Response model for session detach/migrate.
    """

    session_id: str
    status: str = STATUS_DETACHED


class ExecutionEvent(BaseModel):
    """
    Event model for execution status updates.
    """

    execution_id: str
    status: str
    message: Optional[str] = None


class HealthCheckResponse(BaseModel):
    status: str
    version: str


class KeywordParameter(BaseModel):
    name: str
    type: str
    default: Any = None


class KeywordInfo(BaseModel):
    keyword: str
    keyword_slug: str
    description: str
    parameters: List[KeywordParameter]


def _humanize_keyword(name: str) -> str:
    """Convert a snake_case method name into a human-friendly title.

    Examples:
      press_element -> Press Element
      get_driver_session_id -> Get Driver Session Id
    """
    # Replace underscores with spaces, split on spaces and capitalize each word
    parts = [p for p in name.replace("_", " ").split(" ") if p]
    return " ".join(p.capitalize() for p in parts)


def _make_dependency_entry(name: str, cfg: Any, top_level_url: Optional[str] = None, top_level_capabilities: Optional[Dict[str, Any]] = None) -> Dict[str, DependencyConfig]:
    """Create a dependency mapping {name: DependencyConfig} from cfg which may be None, bool, or dict.

    This helper centralizes the conversion logic so callers (including SessionConfig._normalize_item)
    can remain small and simpler to analyze.
    """
    # Default values
    enabled = True
    url: Optional[str] = top_level_url if name == SOURCE_APPIUM else None
    capabilities: Dict[str, Any] = top_level_capabilities or {}

    if cfg is None:
        # keep defaults: enabled=True
        pass
    elif isinstance(cfg, bool):
        enabled = cfg
    elif isinstance(cfg, dict):
        enabled = cfg.get(KEY_ENABLED, True)
        url = cfg.get(KEY_URL) or (top_level_url if name == SOURCE_APPIUM else None)
        capabilities = cast(Dict[str, Any], cfg.get(KEY_CAPABILITIES)) if isinstance(cfg.get(KEY_CAPABILITIES), dict) else (top_level_capabilities or {})
    else:
        # Unknown scalar -> keep enabled True and defaults
        pass

    return {name: DependencyConfig(enabled=enabled, url=url, capabilities=capabilities)}

class SessionConfig(BaseModel):
    """
    Configuration for starting a new Optics session.

    This model accepts two formats for source lists:
    - Deprecated simple format: list of strings, e.g. ["appium", "selenium"]
    - New detailed format: list of dicts, e.g. [{"appium": {"enabled": True, "url": "...", "capabilities": {...}}}]

    Use `normalize_sources()` to convert entries into a consistent list of
    {name: DependencyConfig} mappings used by the server internals.
    """
    driver_sources: List[Union[str, Dict[str, Any]]] = []
    elements_sources: List[Union[str, Dict[str, Any]]] = []
    text_detection: List[Union[str, Dict[str, Any]]] = []
    image_detection: List[Union[str, Dict[str, Any]]] = []
    project_path: Optional[str] = None
    appium_url: Optional[str] = None
    appium_config: Optional[Dict[str, Any]] = None
    api_data: Optional[Dict[str, Any]] = None  # Inline API definitions only; file path not supported in REST

    def _normalize_item(self, item: Union[str, Dict[str, Any]], top_level_url: Optional[str] = None, top_level_capabilities: Optional[Dict[str, Any]] = None) -> Dict[str, DependencyConfig]:
        """Normalize a single source item into {name: DependencyConfig}.

        - If item is a string, return {item: DependencyConfig(enabled=True)}.
        - If item is a dict like {name: {...}}, map inner dict to DependencyConfig.
        - For 'appium' string entries, prefer top-level appium_url/appium_config when present.
        """
        if isinstance(item, str):
            if item == SOURCE_APPIUM:
                # prefer top-level appium settings when provided
                return {SOURCE_APPIUM: DependencyConfig(enabled=True, url=top_level_url, capabilities=top_level_capabilities or {})}
            return _make_dependency_entry(item, None, top_level_url=top_level_url, top_level_capabilities=top_level_capabilities)

        if isinstance(item, dict):
            # Expect single key mapping name -> config
            name = next(iter(item.keys()))
            cfg = item[name]
            return _make_dependency_entry(name, cfg, top_level_url=top_level_url, top_level_capabilities=top_level_capabilities)

        # Fallback
        raise ValueError(f"Unsupported source item type: {type(item)}")

    def normalize_sources(self) -> Dict[str, List[Dict[str, DependencyConfig]]]:
        """Return normalized driver/elements/text/image source lists as expected by internal setup.

        Each list item will be a dict mapping source name to a DependencyConfig instance.
        """
        driver = [self._normalize_item(i, top_level_url=self.appium_url, top_level_capabilities=self.appium_config) for i in (self.driver_sources or [])]
        elements = [self._normalize_item(i) for i in (self.elements_sources or [])]
        text = [self._normalize_item(i) for i in (self.text_detection or [])]
        image = [self._normalize_item(i) for i in (self.image_detection or [])]
        return {
            KEY_DRIVER_SOURCES: driver,
            KEY_ELEMENTS_SOURCES: elements,
            KEY_TEXT_DETECTION: text,
            KEY_IMAGE_DETECTION: image,
        }


def _parse_api_data_to_model(api_data: Dict[str, Any]) -> ApiData:
    """
    Parse api_data (inline API definition dict) into ApiData. Used at session
    creation and for the add-session-api endpoint. Does not accept file paths;
    callers must pass the parsed API definition to avoid path traversal from
    user-controlled input.
    """
    if not isinstance(api_data, dict):
        raise ValueError("api_data must be a dictionary (inline API definition)")
    content = api_data.get(KEY_API, api_data)
    try:
        return ApiData(**content)
    except ValidationError as e:
        raise ValueError(str(e)) from e


def _get_keyword_parameters(sig: inspect.Signature) -> List[KeywordParameter]:
    """Extract parameter info from a method signature."""
    params = []
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        param_type = (
            str(param.annotation)
            if param.annotation != inspect.Parameter.empty
            else "Any"
        )
        default = (
            param.default
            if param.default != inspect.Parameter.empty
            else None
        )
        params.append(
            KeywordParameter(
                name=pname, type=param_type, default=default
            )
        )
    return params

def _extract_keywords_from_class(cls) -> List[KeywordInfo]:
    """Extract keyword info from a class."""
    keywords = []
    for meth_name, meth in inspect.getmembers(cls, predicate=inspect.isfunction):
        if meth_name.startswith("_") or meth_name.startswith("test"):
            continue
        sig = inspect.signature(meth)
        params = _get_keyword_parameters(sig)
        doc = inspect.getdoc(meth) or ""
        keywords.append(
            KeywordInfo(
                keyword=_humanize_keyword(meth_name),
                keyword_slug=meth_name,
                description=doc,
                parameters=params,
            )
        )
    return keywords

def _extract_keywords_from_module(module) -> List[KeywordInfo]:
    """Extract all keyword infos from a module."""
    keywords = []
    for _, obj in inspect.getmembers(module):
        if inspect.isclass(obj) and obj.__module__ == module.__name__:
            keywords.extend(_extract_keywords_from_class(obj))
    return keywords

def discover_keywords() -> List[KeywordInfo]:
    """
    Discover all public methods in optics_framework.api.* classes that are likely to be used as keywords.
    Returns a list of KeywordInfo objects.
    """
    api_pkg = PKG_OPTICS_API
    keywords = []
    api_path = __import__(api_pkg, fromlist=[""]).__path__[0]
    for _, modname, ispkg in pkgutil.iter_modules([api_path]):
        if ispkg or modname.startswith("__"):
            continue
        module = importlib.import_module(f"{api_pkg}.{modname}")
        keywords.extend(_extract_keywords_from_module(module))
    return keywords

@app.get("/", response_model=HealthCheckResponse, status_code=status.HTTP_200_OK)
async def health_check():
    """
    Health check endpoint for Optics Framework API.
    Returns API status and version.
    """
    return HealthCheckResponse(status=HEALTH_STATUS_RUNNING, version=VERSION)

@app.post(
    "/v1/sessions/start",
    response_model=SessionResponse,
    responses={
        400: {"description": "Invalid API data in session config"},
        500: {"description": "Session creation or app launch failed"},
    },
)
async def create_session(config: SessionConfig):
    """
    Create a new Optics session with the provided configuration.
    Returns the session ID if successful.
    """
    try:
        # Check if any session is currently active
        active_sessions = (
            session_manager.sessions if hasattr(session_manager, "sessions") else {}
        )
        if active_sessions and len(active_sessions) > 0:
            internal_logger.warning(
                "Session creation attempted while another session is active."
            )

        # Deprecation warning: appium_url and appium_config are legacy top-level fields
        if config.appium_url is not None or config.appium_config is not None:
            msg = (
                "SessionConfig.appium_url and SessionConfig.appium_config are deprecated and will be removed in a future "
                "release. Please provide Appium configuration via a driver_sources entry (e.g. {'appium': {'url': '...', 'capabilities': {...}}})."
            )
            internal_logger.warning(msg)
            # Also emit a Python DeprecationWarning so callers and test suites can detect it
            warnings.warn(msg, DeprecationWarning, stacklevel=2)

        # Normalize incoming session config (supports deprecated string lists and new dict format)
        normalized = config.normalize_sources()
        driver_sources = normalized.get(KEY_DRIVER_SOURCES, [])
        elements_sources = normalized.get(KEY_ELEMENTS_SOURCES, [])
        text_detection = normalized.get(KEY_TEXT_DETECTION, [])
        image_detection = normalized.get(KEY_IMAGE_DETECTION, [])

        session_config = Config(
            driver_sources=driver_sources,
            elements_sources=elements_sources,
            text_detection=text_detection,
            image_detection=image_detection,
            project_path=config.project_path,
            log_level=LOG_LEVEL_DEBUG,
            save_captures=False  # do not save screenshots or pagesource when using `optics serve`
        )
        templates = (
            discover_templates(config.project_path) if config.project_path else None
        )
        apis: Optional[ApiData] = None
        if config.api_data is not None:
            try:
                apis = _parse_api_data_to_model(config.api_data)
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"{MSG_INVALID_API_DATA} {e}",
                ) from e
        session_id = session_manager.create_session(
            session_config,
            test_cases=None,
            modules=None,
            elements=None,
            apis=apis,
            templates=templates,
        )
        reconfigure_logging(session_config)
        internal_logger.info(
            "Created session %s with config: %s",
            session_id,
            config.model_dump()
        )

        launch_request = ExecuteRequest(
            mode=MODE_KEYWORD,
            keyword=KEYWORD_LAUNCH_APP,
            params=[]
        )
        driver_session = await execute_keyword(session_id, launch_request)
        return SessionResponse(
            session_id=session_id,
            driver_id=(driver_session.data or {}).get(KEY_RESULT)
        )
    except OpticsError as e:
        internal_logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=e.status_code, detail=e.to_payload(include_status=True)) from e
    except Exception as e:
        internal_logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=500, detail=f"{MSG_SESSION_CREATION_FAILED} {e}") from e


def _normalize_param_value(name: str, val: Union[str, List[str]], is_list_param: bool = False) -> List[str]:
    """
    Normalize a parameter value to a list of strings.
    Similar to _normalize_fallback_values in optics.py.

    Args:
        name: Parameter name for error messages
        val: Parameter value (str or List[str])
        is_list_param: Whether this parameter is expected to be a list type

    Returns:
        List[str]: Normalized list of strings. If is_list_param is True and val is a list,
                   returns a list containing a JSON-serialized version of the list.

    Raises:
        ValueError: If list is empty
        TypeError: If value is not str or List[str]
    """
    if val is None:
        return []
    if isinstance(val, list):
        if not val:
            raise ValueError(f"Empty list not allowed for parameter '{name}'")
        if not all(isinstance(x, str) for x in val):
            raise TypeError(f"Parameter '{name}' must be List[str], got mixed types")

        # If this is a list parameter, serialize it as JSON so it's treated as a single value
        if is_list_param:
            return [json.dumps(val)]
        return val
    if isinstance(val, str):
        return [val]
    raise TypeError(f"Parameter '{name}' must be str or List[str], got {type(val)}")


def _resolve_named_to_positional(
    method: Callable[..., Any],
    named_params: Dict[str, Union[str, List[str]]]
) -> List[List[str]]:
    """
    Convert named parameters to positional parameters based on method signature.

    Args:
        method: The keyword method to call
        named_params: Dictionary of named parameters

    Returns:
        List[List[str]]: List of normalized parameter lists in method signature order
    """
    sig = inspect.signature(method)
    param_names = []
    for p in sig.parameters.values():
        if p.name != "self":
            param_names.append(p.name)

    normalized_params = []
    for param_name in param_names:
        if param_name in named_params:
            normalized_params.append(_normalize_param_value(param_name, named_params[param_name]))
        else:
            # Parameter not provided, check if it has a default
            param = sig.parameters[param_name]
            if param.default != inspect.Parameter.empty:
                # Has default, skip this parameter (method will use its default)
                # We don't include it in the normalized params list
                continue
            else:
                # Required parameter missing
                raise ValueError(f"Required parameter '{param_name}' not provided in named params")

    return normalized_params


class _NamedParamsContext(NamedTuple):
    """Holds normalized named-param state for fallback execution."""

    normalized_param_lists: List[List[str]]
    provided_param_names: List[str]
    all_param_names: List[str]
    param_defaults: Dict[str, Any]
    param_is_list: Dict[str, bool]


def _keyword_execution_params(session_id: str, keyword: str, param_list: List[str]) -> ExecutionParams:
    """Build ExecutionParams for a single keyword run."""
    return ExecutionParams(
        session_id=session_id,
        mode=MODE_KEYWORD,
        keyword=keyword,
        params=param_list,
        runner_type=RUNNER_TYPE_KEYWORD,
        use_printer=False,
    )


def _should_reraise(e: BaseException) -> bool:
    """True if exception should be re-raised as-is (e.g. SystemExit)."""
    return isinstance(e, (SystemExit, KeyboardInterrupt, GeneratorExit))


def _decode_template_base64(value: str) -> bytes:
    """Decode base64 image bytes; supports raw base64 or data URL (data:...;base64,...)."""
    s = value.strip()
    if s.startswith(DATA_URL_PREFIX):
        idx = s.find(BASE64_SEPARATOR)
        if idx != -1:
            s = s[idx + len(BASE64_SEPARATOR):]
    return base64.b64decode(s)


def _write_bytes_to_path(path: str, data: bytes) -> None:
    """Write bytes to a file (sync). Use via asyncio.to_thread in async code."""
    with open(path, "wb") as f:
        f.write(data)


def _safe_template_filename(name: str) -> str:
    """
    Return a safe filename stem for a template from its logical name.
    Uses name when it contains only safe chars [a-zA-Z0-9_.-] and no path-like content.
    Raises ValueError for path-like or otherwise invalid names (we do not allow them).
    """
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("Template name must not contain path segments (/, \\, ..)")
    stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", name).strip("._")
    if not stem or stem in (".", ".."):
        raise ValueError("Template name is invalid or reserved")
    if len(stem) > 200:
        raise ValueError("Template name is too long")
    return stem


def _build_named_param_context(
    method: Callable[..., Any], params: Dict[str, Union[str, List[str]]]
) -> _NamedParamsContext:
    """Normalize named params and build context for fallback combos."""
    sig = inspect.signature(method)
    all_param_names = [p.name for p in sig.parameters.values() if p.name != "self"]
    normalized_param_lists: List[List[str]] = []
    provided_param_names: List[str] = []
    param_defaults: Dict[str, Any] = {}
    param_is_list: Dict[str, bool] = {}
    for param_name in all_param_names:
        param = sig.parameters[param_name]
        is_list_param = _is_list_type(param.annotation)
        param_is_list[param_name] = is_list_param
        if param_name in params:
            normalized_param_lists.append(
                _normalize_param_value(param_name, params[param_name], is_list_param)
            )
            provided_param_names.append(param_name)
        elif param.default != inspect.Parameter.empty:
            param_defaults[param_name] = param.default
        else:
            raise ValueError(f"Required parameter '{param_name}' not provided in named params")
    return _NamedParamsContext(
        normalized_param_lists=normalized_param_lists,
        provided_param_names=provided_param_names,
        all_param_names=all_param_names,
        param_defaults=param_defaults,
        param_is_list=param_is_list,
    )


def _build_positional_normalized(
    params: List[Union[str, List[str]]]
) -> List[List[str]]:
    """Normalize positional params to list-of-lists for fallback combos."""
    return [_normalize_param_value(f"param_{i}", p) for i, p in enumerate(params)]


def _combo_to_positional_named(
    combo: tuple,
    ctx: _NamedParamsContext,
) -> List[str]:
    """Map one named-param combo to positional args in method order."""
    combo_dict = dict(zip(ctx.provided_param_names, combo))
    out: List[str] = []
    for param_name in ctx.all_param_names:
        if param_name in combo_dict:
            out.append(combo_dict[param_name])
        elif param_name in ctx.param_defaults:
            default_val = ctx.param_defaults[param_name]
            if ctx.param_is_list.get(param_name, False) and isinstance(default_val, list):
                out.append(json.dumps(default_val))
            else:
                out.append(default_val if isinstance(default_val, str) else str(default_val))
    return out


async def _execute_no_params(
    engine: ExecutionEngine, session_id: str, keyword: str
) -> Any:
    """Run keyword with no params; raise RuntimeError on failure, re-raise SystemExit etc."""
    try:
        return await engine.execute(_keyword_execution_params(session_id, keyword, []))
    except Exception as e:
        if _should_reraise(e):
            raise
        raise RuntimeError(f"Keyword execution failed: {e}") from e


async def _try_combos_named(
    engine: ExecutionEngine,
    session_id: str,
    keyword: str,
    ctx: _NamedParamsContext,
) -> Any:
    """Try each named-param combo; return first success or raise RuntimeError."""
    errors: List[Tuple[tuple, str]] = []
    for combo in product(*ctx.normalized_param_lists):
        try:
            args = _combo_to_positional_named(combo, ctx)
            return await engine.execute(_keyword_execution_params(session_id, keyword, args))
        except Exception as e:
            if _should_reraise(e):
                raise
            errors.append((combo, repr(e)))
    if not errors:
        raise RuntimeError(f"No valid fallback values provided for keyword '{keyword}'")
    msg = "\n".join([f"{c} -> {err}" for c, err in errors])
    raise RuntimeError(f"All fallback attempts failed for keyword '{keyword}':\n{msg}")


async def _try_combos_positional(
    engine: ExecutionEngine,
    session_id: str,
    keyword: str,
    normalized_param_lists: List[List[str]],
) -> Any:
    """Try each positional combo; return first success or raise RuntimeError."""
    errors: List[Tuple[tuple, str]] = []
    for combo in product(*normalized_param_lists):
        try:
            return await engine.execute(
                _keyword_execution_params(session_id, keyword, list(combo))
            )
        except Exception as e:
            if _should_reraise(e):
                raise
            errors.append((combo, repr(e)))
    if not errors:
        raise RuntimeError(f"No valid fallback values provided for keyword '{keyword}'")
    msg = "\n".join([f"{c} -> {err}" for c, err in errors])
    raise RuntimeError(f"All fallback attempts failed for keyword '{keyword}':\n{msg}")


async def _execute_keyword_with_fallback(
    engine: ExecutionEngine,
    session_id: str,
    keyword: str,
    params: Union[List[Union[str, List[str]]], Dict[str, Union[str, List[str]]]],
    method: Callable[..., Any],
    session: Session,
) -> Any:
    """
    Execute a keyword via ExecutionEngine with fallback parameter support.
    Tries all combinations of fallback values until one succeeds.

    Args:
        engine: The ExecutionEngine instance
        session_id: The session ID
        keyword: The keyword name
        params: Either positional params (List) or named params (Dict)
        method: The keyword method (for signature inspection)
        session: The session object (for context)

    Returns:
        Any: Result from the first successful execution

    Raises:
        RuntimeError: If all fallback attempts fail
    """
    if isinstance(params, dict):
        ctx = _build_named_param_context(method, params)
        normalized_param_lists = ctx.normalized_param_lists
    else:
        ctx = None
        normalized_param_lists = _build_positional_normalized(params)

    if not normalized_param_lists:
        return await _execute_no_params(engine, session_id, keyword)
    if ctx is not None:
        return await _try_combos_named(engine, session_id, keyword, ctx)
    return await _try_combos_positional(engine, session_id, keyword, normalized_param_lists)


async def _setup_request_template_overrides(
    session: Session, template_images: Optional[Dict[str, str]]
) -> List[str]:
    """Write template images to a temp dir and update session.request_template_overrides. Returns temp dirs for cleanup."""
    if not template_images:
        return []
    temp_dir = tempfile.mkdtemp(prefix=TEMP_DIR_PREFIX)
    for name, b64_value in template_images.items():
        try:
            safe_stem = _safe_template_filename(name)
        except ValueError as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(e)) from e
        try:
            raw = _decode_template_base64(b64_value)
        except (binascii.Error, ValueError) as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"{MSG_INVALID_BASE64_IMAGE} {e}") from e
        path = os.path.join(temp_dir, f"{safe_stem}{TEMPLATE_EXT_PNG}")
        await asyncio.to_thread(_write_bytes_to_path, path, raw)
        session.request_template_overrides[name] = path
    return [temp_dir]


async def _handle_execution_failure(
    e: Exception, session: Session, execution_id: str, keyword: str
) -> None:
    """Put FAIL event and raise HTTPException. Never returns."""
    await session.event_queue.put(ExecutionEvent(
        execution_id=execution_id,
        status=STATUS_FAIL,
        message=f"Keyword {keyword} failed: {str(e)}"
    ).model_dump())
    if isinstance(e, OpticsError):
        raise HTTPException(status_code=e.status_code, detail=e.to_payload(include_status=True)) from e
    raise HTTPException(status_code=500, detail=f"{MSG_EXECUTION_FAILED} {str(e)}") from e


@app.post(
    "/v1/sessions/{session_id}/action",
    responses={
        400: {"description": "Invalid mode, missing keyword, or invalid template image"},
        404: {"description": "Session not found"},
        500: {"description": "Keyword execution failed"},
    },
)
async def execute_keyword(session_id: str, request: ExecuteRequest):
    """
    Execute a keyword in the specified session.
    Supports both positional and named parameters with fallback support.
    Optional template_images: names to base64 image data for vision-based keywords.
    Returns execution status and result.
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)

    if request.mode != MODE_KEYWORD or not request.keyword:
        raise HTTPException(status_code=400, detail=MSG_ONLY_KEYWORD_MODE_SUPPORTED)

    engine = ExecutionEngine(session_manager)
    execution_id = str(uuid.uuid4())
    request_temp_dirs = await _setup_request_template_overrides(session, request.template_images)
    session_manager.mark_busy(session_id, True)

    try:
        await session.event_queue.put(ExecutionEvent(
            execution_id=execution_id,
            status=STATUS_RUNNING,
            message=f"Starting keyword: {request.keyword}"
        ).model_dump())

        registry = KeywordRegistry()
        action_keyword = session.optics.build(ActionKeyword)
        app_management = session.optics.build(AppManagement)
        verifier = session.optics.build(Verifier)
        registry.register(action_keyword)
        registry.register(app_management)
        registry.register(verifier)
        registry.register(FlowControl(session=session, keyword_map=registry.keyword_map))

        keyword_slug = "_".join(request.keyword.split()).lower()
        method = registry.keyword_map.get(keyword_slug)

        if not method:
            raise OpticsError(Code.E0402, message=f"Keyword {request.keyword} not found")

        result = await _execute_keyword_with_fallback(
            engine, session_id, request.keyword, request.params, method, session
        )

        await session.event_queue.put(ExecutionEvent(
            execution_id=execution_id,
            status=STATUS_SUCCESS,
            message=f"Keyword {request.keyword} executed successfully"
        ).model_dump())

        return ExecutionResponse(
            execution_id=execution_id,
            status=STATUS_SUCCESS,
            data={KEY_RESULT: result} if not isinstance(result, dict) else result
        )

    except Exception as e:
        await _handle_execution_failure(e, session, execution_id, request.keyword)
    finally:
        session_manager.mark_busy(session_id, False)
        session.request_template_overrides.clear()
        for dir_path in request_temp_dirs:
            try:
                shutil.rmtree(dir_path, ignore_errors=True)
            except OSError as e:
                internal_logger.warning(
                    "Failed to remove request template directory %s: %s", dir_path, e
                )


@app.post(
    "/v1/sessions/{session_id}/templates",
    responses={
        400: {"description": "Invalid base64 image data or unsafe template name"},
        404: {"description": "Session not found"},
    },
)
async def upload_template(session_id: str, body: TemplateUploadRequest):
    """
    Upload a template image for the session. The name can be used in execute
    params (e.g. element) for vision-based keywords. Overwrites if name exists.
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
    try:
        raw = _decode_template_base64(body.image_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"{MSG_INVALID_BASE64_IMAGE} {e}") from e
    base_dir = session._inline_templates_dir
    os.makedirs(base_dir, exist_ok=True)
    try:
        safe_stem = _safe_template_filename(body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    path = os.path.join(base_dir, f"{safe_stem}{TEMPLATE_EXT_PNG}")
    await asyncio.to_thread(_write_bytes_to_path, path, raw)
    session.inline_templates[body.name] = path
    return {"name": body.name, "status": STATUS_OK}


@app.post(
    "/v1/sessions/{session_id}/api",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"description": "Invalid API data format"},
        404: {"description": "Session not found"},
    },
)
async def add_session_api(session_id: str, body: Annotated[Dict[str, Any], Body(...)]):
    """
    Add or replace API definitions for the session. Request body must be a JSON
    object: either { "api": { "collections": ... } } or the api content at root.
    Replaces session API data (same semantics as Add API keyword).
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
    try:
        api_data = _parse_api_data_to_model(body)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"{MSG_INVALID_API_DATA} {e}",
        ) from e
    session.apis = api_data


# Helper for DRY keyword execution endpoints
async def run_keyword_endpoint(
    session_id: str,
    keyword: str,
    params: Optional[Union[List[Union[str, List[str]]], Dict[str, Union[str, List[str]]]]] = None
) -> Any:
    """
    Helper to execute a keyword for a session using the execute_keyword endpoint.
    Supports both positional and named parameters with fallback support.
    """
    safe_params: Union[List[Union[str, List[str]]], Dict[str, Union[str, List[str]]]] = params or []
    request = ExecuteRequest(mode=MODE_KEYWORD, keyword=keyword, params=safe_params)
    return await execute_keyword(session_id, request)


@app.get("/v1/sessions/{session_id}/screenshot")
async def capture_screenshot(session_id: str):
    """
    Capture a screenshot in the specified session.
    Returns the screenshot result.
    """
    return await run_keyword_endpoint(session_id, "capture_screenshot")

@app.get("/v1/sessions/{session_id}/driver-id")
async def get_driver_session_id(session_id: str):
    """
    Get the underlying Driver session ID for this Optics session.
    Returns ExecutionResponse with the session id in data.result.
    """
    return await run_keyword_endpoint(session_id, "get_driver_session_id")

@app.get("/v1/sessions/{session_id}/elements")
async def get_elements(
    session_id: str,
    filter_config: Optional[List[str]] = Query(None, description="Filter types: all, interactive, buttons, inputs, images, text")
):
    """
    Get interactive elements from the current session screen.

    Args:
        session_id: The session ID
        filter_config: Optional list of filter types. Valid values:
            - "all": Show all elements (default when None or empty)
            - "interactive": Only interactive elements
            - "buttons": Only button elements
            - "inputs": Only input/text field elements
            - "images": Only image elements
            - "text": Only text elements
            Can be combined: ?filter_config=buttons&filter_config=inputs

    Returns:
        The elements result.
    """
    params: Optional[Dict[str, Union[str, List[str]]]] = None
    if filter_config:
        params = {PARAM_FILTER_CONFIG: filter_config}
    return await run_keyword_endpoint(session_id, "get_interactive_elements", params)

@app.get("/v1/sessions/{session_id}/source")
async def get_pagesource(session_id: str):
    """
    Capture the page source from the current session.
    Returns the page source result.
    """
    return await run_keyword_endpoint(session_id, "capture_pagesource")

@app.get("/v1/sessions/{session_id}/screen_elements")
async def screen_elements(session_id: str):
    """
    Capture and get screen elements from the current session.
    Returns the screen elements result.
    """
    return await run_keyword_endpoint(session_id, "get_screen_elements")

@app.get(
    "/v1/sessions/{session_id}/events",
    responses={
        404: {"description": "Session not found"},
    },
)
async def stream_events(session_id: str):
    """
    Stream execution events for the specified session using Server-Sent Events (SSE).
    """
    session = session_manager.get_session(session_id)
    if not session:
        internal_logger.error(f"Session not found for event streaming: {session_id}")
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
    internal_logger.info(f"Starting event stream for session {session_id}")
    return EventSourceResponse(event_generator(session))

@app.get("/v1/sessions/{session_id}/workspace/stream")
async def stream_workspace(
    session_id: str,
    interval_ms: int = Query(2000, description="Polling interval in milliseconds (minimum 500ms)"),
    include_source: bool = Query(False, description="Include page source in workspace data"),
    filter_config: Optional[List[str]] = Query(None, description="Filter types for elements: all, interactive, buttons, inputs, images, text")
):
    """
    Stream workspace data (screenshot, elements, optionally source) for the specified session using Server-Sent Events (SSE).
    Only emits updates when workspace data actually changes, reducing load on the driver.
    """
    session = session_manager.get_session(session_id)
    if not session:
        internal_logger.error(f"Session not found for workspace streaming: {session_id}")
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)

    # Enforce minimum interval to prevent tight loops
    interval_seconds = max(0.5, interval_ms / 1000.0)

    internal_logger.info(f"Starting workspace stream for session {session_id}, interval={interval_seconds}s, include_source={include_source}")
    return EventSourceResponse(workspace_generator(session, interval_seconds, include_source, filter_config))

@app.get("/v1/keywords", response_model=List[KeywordInfo])
async def list_keywords():
    """
    List all available keywords and their parameters.
    """
    return discover_keywords()


def _empty_workspace_data(include_source: bool) -> Dict[str, Any]:
    """Return empty workspace data for error fallback."""
    data: Dict[str, Any] = {
        KEY_SCREENSHOT: "",
        KEY_ELEMENTS: [],
        KEY_SCREENSHOT_FAILED: True,
    }
    if include_source:
        data[KEY_SOURCE] = ""
    return data


async def _capture_source_safe(verifier: Verifier) -> str:
    """Capture pagesource, returning empty string on failure."""
    try:
        source = await asyncio.to_thread(verifier.capture_pagesource)
        return source or ""
    except Exception as e:
        internal_logger.warning(f"Failed to capture pagesource: {e}")
        return ""


async def _gather_workspace_data(
    session: Session,
    include_source: bool = False,
    filter_config: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Gather workspace data (screenshot, elements, optionally source) from a session.
    Returns a dict with screenshot, elements, and optionally source.
    """
    try:
        verifier = session.optics.build(Verifier)

        screenshot_task = asyncio.create_task(asyncio.to_thread(verifier.capture_screenshot))
        elements_task = asyncio.create_task(asyncio.to_thread(verifier.get_interactive_elements, filter_config))

        screenshot, elements = await asyncio.gather(screenshot_task, elements_task)

        workspace_data: Dict[str, Any] = {
            KEY_SCREENSHOT: screenshot or "",
            KEY_ELEMENTS: elements or [],
            KEY_SCREENSHOT_FAILED: not screenshot,
        }

        if include_source:
            workspace_data[KEY_SOURCE] = await _capture_source_safe(verifier)

        return workspace_data
    except Exception as e:
        internal_logger.error(f"Error gathering workspace data: {e}")
        return _empty_workspace_data(include_source)

@asynccontextmanager
async def _open_stream_guard(session: Session):
    """Track open SSE streams on the session runtime; streams are
    instance-local, so detach/migrate refuses while one is open (design §9)."""
    runtime = session.runtime
    if runtime is not None:
        runtime.open_streams += 1
    try:
        yield
    finally:
        if runtime is not None:
            runtime.open_streams = max(0, runtime.open_streams - 1)


def _compute_workspace_hash(workspace_data: Dict[str, Any]) -> str:
    """
    Compute a hash of workspace data for change detection.
    Uses screenshot and elements, optionally source if included.
    """
    # Create a canonical representation for hashing
    hash_data = {
        KEY_SCREENSHOT: workspace_data.get(KEY_SCREENSHOT, ""),
        KEY_ELEMENTS: json.dumps(workspace_data.get(KEY_ELEMENTS, []), sort_keys=True),
    }
    if KEY_SOURCE in workspace_data:
        hash_data[KEY_SOURCE] = workspace_data.get(KEY_SOURCE, "")

    hash_str = json.dumps(hash_data, sort_keys=True)
    return hashlib.sha256(hash_str.encode()).hexdigest()

async def workspace_generator(
    session: Session,
    interval_seconds: float,
    include_source: bool = False,
    filter_config: Optional[List[str]] = None
):
    """
    Generator for streaming workspace updates with change detection.
    Only emits when workspace data actually changes.
    """
    HEARTBEAT_INTERVAL = 15.0  # seconds
    last_heartbeat = asyncio.get_event_loop().time()

    async with _open_stream_guard(session):
        while True:
            try:
                # Check if session still exists
                if not session_manager.get_session(session.session_id):
                    internal_logger.warning(f"Session {session.session_id} no longer exists, ending workspace stream")
                    break

                # Gather workspace data
                workspace_data = await _gather_workspace_data(session, include_source, filter_config)

                # Compute hash for change detection; kept in SessionState.metadata
                # so change detection survives rehydration on another instance.
                current_hash = _compute_workspace_hash(workspace_data)
                last_hash = session.state.metadata.get(KEY_WORKSPACE_HASH)

                # Only emit if data changed
                if last_hash is None or current_hash != last_hash:
                    session.state.metadata[KEY_WORKSPACE_HASH] = current_hash
                    session_manager.persist_state(session)
                    internal_logger.debug(f"Workspace data changed for session {session.session_id}, emitting update")
                    yield {KEY_DATA: json.dumps(workspace_data)}
                    last_heartbeat = asyncio.get_event_loop().time()
                else:
                    # No change, check if we need to send heartbeat
                    now = asyncio.get_event_loop().time()
                    if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                        internal_logger.debug(f"Heartbeat for workspace stream session {session.session_id}")
                        yield {KEY_DATA: json.dumps({KEY_TYPE: WORKSPACE_TYPE_HEARTBEAT, KEY_TIMESTAMP: now})}
                        last_heartbeat = now

                # Wait for next interval
                await asyncio.sleep(interval_seconds)

            except asyncio.CancelledError:
                internal_logger.warning(f"Workspace stream cancelled for session {session.session_id}")
                raise
            except Exception as e:
                internal_logger.error(f"Error in workspace stream for session {session.session_id}: {e}")
                yield {KEY_DATA: json.dumps({
                    KEY_TYPE: WORKSPACE_TYPE_ERROR,
                    KEY_MESSAGE: str(e),
                    KEY_SCREENSHOT: "",
                    KEY_ELEMENTS: [],
                    KEY_SCREENSHOT_FAILED: True
                })}
                # Wait before retrying to avoid tight error loops
                await asyncio.sleep(interval_seconds)

async def event_generator(session: Session):
    """
    Generator for streaming execution events and heartbeats for a session.
    Yields events as SSE data.
    """
    HEARTBEAT_INTERVAL = 15  # seconds
    async with _open_stream_guard(session):
        while True:
            try:
                try:
                    event = await asyncio.wait_for(session.event_queue.get(), timeout=HEARTBEAT_INTERVAL)
                    internal_logger.debug(f"Streaming event for session {session.session_id}: {event}")
                    yield {KEY_DATA: json.dumps(event)}
                except asyncio.TimeoutError:
                    # Send heartbeat if no event in interval
                    internal_logger.debug(f"Heartbeat for session {session.session_id}")
                    yield {KEY_DATA: json.dumps(ExecutionEvent(
                        execution_id=EXECUTION_ID_HEARTBEAT,
                        status=STATUS_HEARTBEAT,
                        message="No new event, sending heartbeat"
                    ).model_dump())}
                except Exception as exc:
                    internal_logger.error(f"Unexpected error while waiting for event: {exc}")
                    yield {KEY_DATA: json.dumps(ExecutionEvent(
                        execution_id=EXECUTION_ID_UNKNOWN,
                        status=STATUS_ERROR,
                        message=f"Unexpected error while waiting for event: {exc}"
                    ).model_dump())}
                    break
            except AttributeError as attr_err:
                internal_logger.error(f"AttributeError in event streaming for session {session.session_id}: {attr_err}")
                yield {KEY_DATA: json.dumps(ExecutionEvent(
                    execution_id=EXECUTION_ID_UNKNOWN,
                    status=STATUS_ERROR,
                    message=f"AttributeError: {attr_err}"
                ).model_dump())}
                break
            except asyncio.CancelledError as cancel_err:
                internal_logger.warning(f"Event streaming cancelled for session {session.session_id}: {cancel_err}")
                yield {KEY_DATA: json.dumps(ExecutionEvent(
                    execution_id=EXECUTION_ID_UNKNOWN,
                    status=STATUS_CANCELLED,
                    message=f"Event streaming cancelled: {cancel_err}"
                ).model_dump())}
                raise
            except Exception as e:
                internal_logger.error(f"General error in event streaming for session {session.session_id}: {e}")
                yield {KEY_DATA: json.dumps(ExecutionEvent(
                    execution_id=EXECUTION_ID_UNKNOWN,
                    status=STATUS_ERROR,
                    message=f"Event streaming failed: {e}"
                ).model_dump())}
                break

@app.delete(
    "/v1/sessions/{session_id}/stop",
    response_model=TerminationResponse,
    responses={
        500: {"description": "Session termination failed"},
    },
)
async def delete_session(session_id: str):
    """
    Terminate the specified session and clean up resources.
    Returns termination status.
    """
    kill_request = ExecuteRequest(
        mode=MODE_KEYWORD,
        keyword=KEYWORD_CLOSE_AND_TERMINATE_APP,
        params=[]
    )
    try:
        await execute_keyword(session_id, kill_request)
    except OpticsError as e:
        internal_logger.error(f"Failed to terminate session {session_id}: {e}")
        raise HTTPException(status_code=e.status_code, detail=e.to_payload(include_status=True)) from e
    except Exception as e:
        internal_logger.error(f"Failed to terminate session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"{MSG_SESSION_TERMINATION_FAILED} {e}") from e
    session_manager.terminate_session(session_id)
    internal_logger.info(f"Terminated session: {session_id}")
    return TerminationResponse()


@app.post(
    "/v1/sessions/{session_id}/export",
    response_model=SessionState,
    responses={
        404: {"description": "Session not found"},
    },
)
async def export_session(session_id: str):
    """
    Export the session's serializable state (config recipe, driver binding
    with a fresh reattach handle, inline template bytes). The JSON can be fed
    to /v1/sessions/import on another instance to resume the session.
    """
    try:
        return session_manager.export_state(session_id)
    except OpticsError as e:
        internal_logger.error(f"Failed to export session {session_id}: {e}")
        raise HTTPException(status_code=e.status_code, detail=e.to_payload(include_status=True)) from e


@app.post(
    "/v1/sessions/import",
    response_model=SessionResponse,
    responses={
        400: {"description": "Session already live here or invalid state"},
        500: {"description": "Rehydration or strict driver reattach failed"},
    },
)
async def import_session(state: SessionState):
    """
    Import a previously exported SessionState: store it and rehydrate through
    the same path a load balancer would exercise implicitly — rebuild the
    runtime and strictly reattach the driver to the live backend session.
    """
    try:
        session_id = session_manager.create_session_from_state(state)
    except OpticsError as e:
        internal_logger.error(f"Failed to import session {state.session_id}: {e}")
        raise HTTPException(status_code=e.status_code, detail=e.to_payload(include_status=True)) from e
    except Exception as e:
        internal_logger.error(f"Failed to import session {state.session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"{MSG_SESSION_CREATION_FAILED} {e}") from e
    return SessionResponse(session_id=session_id, status=STATUS_IMPORTED)


@app.post(
    "/v1/sessions/{session_id}/migrate",
    response_model=MigrationResponse,
    responses={
        400: {"description": "Session busy, has an open stream, or its driver is not migratable"},
        404: {"description": "Session not found"},
    },
)
async def migrate_session(session_id: str):
    """
    Detach the session from this instance: drop the local runtime while
    keeping the backend driver session and stored state alive. The next
    request for the session (routed anywhere) rehydrates it — in-cluster
    migration is emergent, not a bespoke path. Refused for sticky
    (non-migratable) drivers and for busy sessions.
    """
    try:
        state = session_manager.detach_session(session_id)
    except OpticsError as e:
        internal_logger.error(f"Failed to detach session {session_id}: {e}")
        raise HTTPException(status_code=e.status_code, detail=e.to_payload(include_status=True)) from e
    return MigrationResponse(session_id=state.session_id, status=STATUS_DETACHED)
