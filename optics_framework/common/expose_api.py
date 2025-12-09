import json
import uuid
import inspect
import importlib
import pkgutil
import asyncio
import warnings
from itertools import product
from typing import Optional, Dict, Any, List, Union, cast, Callable, get_origin, get_args
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi import status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from optics_framework.common.session_manager import SessionManager, Session
from optics_framework.common.execution import (
    ExecutionEngine,
    ExecutionParams,
)
from optics_framework.common.logging_config import internal_logger, reconfigure_logging
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.common.runner.keyword_register import KeywordRegistry
from optics_framework.api import ActionKeyword, AppManagement, FlowControl, Verifier
from optics_framework.helper.version import VERSION

app = FastAPI(title="Optics Framework API", version="1.0")
session_manager = SessionManager()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_NOT_FOUND = "Session not found"


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
    """

    mode: str
    test_case: Optional[str] = None
    keyword: Optional[str] = None
    params: Union[List[Union[str, List[str]]], Dict[str, Union[str, List[str]]]] = []


class SessionResponse(BaseModel):
    """
    Response model for session creation.
    """

    session_id: str
    driver_id: Optional[str] = None
    status: str = "created"


class ExecutionResponse(BaseModel):
    """
    Response model for execution results.
    """

    execution_id: str
    status: str = "started"
    data: Optional[Dict[str, Any]] = None


class TerminationResponse(BaseModel):
    """
    Response model for session termination.
    """

    status: str = "terminated"


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
    url: Optional[str] = top_level_url if name == "appium" else None
    capabilities: Dict[str, Any] = top_level_capabilities or {}

    if cfg is None:
        # keep defaults: enabled=True
        pass
    elif isinstance(cfg, bool):
        enabled = cfg
    elif isinstance(cfg, dict):
        enabled = cfg.get("enabled", True)
        url = cfg.get("url") or (top_level_url if name == "appium" else None)
        capabilities = cast(Dict[str, Any], cfg.get("capabilities")) if isinstance(cfg.get("capabilities"), dict) else (top_level_capabilities or {})
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

    def _normalize_item(self, item: Union[str, Dict[str, Any]], top_level_url: Optional[str] = None, top_level_capabilities: Optional[Dict[str, Any]] = None) -> Dict[str, DependencyConfig]:
        """Normalize a single source item into {name: DependencyConfig}.

        - If item is a string, return {item: DependencyConfig(enabled=True)}.
        - If item is a dict like {name: {...}}, map inner dict to DependencyConfig.
        - For 'appium' string entries, prefer top-level appium_url/appium_config when present.
        """
        if isinstance(item, str):
            if item == "appium":
                # prefer top-level appium settings when provided
                return {"appium": DependencyConfig(enabled=True, url=top_level_url, capabilities=top_level_capabilities or {})}
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
            "driver_sources": driver,
            "elements_sources": elements,
            "text_detection": text,
            "image_detection": image,
        }

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
    api_pkg = "optics_framework.api"
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
    return HealthCheckResponse(status="Optics Framework API is running", version=VERSION)

@app.post("/v1/sessions/start", response_model=SessionResponse)
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
        driver_sources = normalized.get("driver_sources", [])
        elements_sources = normalized.get("elements_sources", [])
        text_detection = normalized.get("text_detection", [])
        image_detection = normalized.get("image_detection", [])

        session_config = Config(
            driver_sources=driver_sources,
            elements_sources=elements_sources,
            text_detection=text_detection,
            image_detection=image_detection,
            project_path=config.project_path,
            log_level="DEBUG"
        )
        session_id = session_manager.create_session(
            session_config,
            test_cases=None,
            modules=None,
            elements=None,
            apis=None
        )
        reconfigure_logging(session_config)
        internal_logger.info(
            "Created session %s with config: %s",
            session_id,
            config.model_dump()
        )

        launch_request = ExecuteRequest(
            mode="keyword",
            keyword="launch_app",
            params=[]
        )
        driver_session = await execute_keyword(session_id, launch_request)
        return SessionResponse(
            session_id=session_id,
            driver_id=(driver_session.data or {}).get("result")
        )
    except Exception as e:
        internal_logger.error(f"Failed to create session: {e}")
        if isinstance(e, OpticsError):
            raise HTTPException(status_code=e.status_code, detail=e.to_payload(include_status=True)) from e
        raise HTTPException(status_code=500, detail=f"Session creation failed: {e}") from e


def _is_list_type(param_type: Any) -> bool:
    """
    Check if a parameter type annotation indicates it's a list type.

    Args:
        param_type: The type annotation from inspect.Parameter

    Returns:
        bool: True if the type is a list type (List[str], Optional[List[str]], etc.)
    """
    if param_type is None or param_type == inspect.Parameter.empty:
        return False

    # Handle Optional[List[str]] -> Union[List[str], None]
    origin = get_origin(param_type)
    if origin is Union:
        args = get_args(param_type)
        # Check if any of the union types is a list
        for arg in args:
            if get_origin(arg) is list or (hasattr(arg, '__origin__') and arg.__origin__ is list):
                return True

    # Handle List[str] directly
    if origin is list:
        return True

    # Handle typing.List[str] (Python < 3.9 compatibility)
    if hasattr(param_type, '__origin__') and param_type.__origin__ is list:
        return True

    return False


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


async def _execute_keyword_with_fallback(
    engine: ExecutionEngine,
    session_id: str,
    keyword: str,
    params: Union[List[Union[str, List[str]]], Dict[str, Union[str, List[str]]]],
    method: Callable[..., Any],
    session: Session
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
    # Detect parameter format and normalize
    if isinstance(params, dict):
        # Named parameters: convert to positional based on method signature
        sig = inspect.signature(method)
        all_param_names = [p.name for p in sig.parameters.values() if p.name != "self"]

        normalized_param_lists = []
        provided_param_names = []
        param_defaults = {}  # Store defaults for later use
        param_is_list = {}  # Track which parameters are list types
        for param_name in all_param_names:
            param = sig.parameters[param_name]
            is_list_param = _is_list_type(param.annotation)
            param_is_list[param_name] = is_list_param

            if param_name in params:
                normalized_param_lists.append(_normalize_param_value(param_name, params[param_name], is_list_param))
                provided_param_names.append(param_name)
            else:
                # Parameter not provided, check if it has a default
                if param.default != inspect.Parameter.empty:
                    # Has default - store it but don't include in fallback combinations
                    # We'll use the default value directly when building positional args
                    param_defaults[param_name] = param.default
                else:
                    # Required parameter missing
                    raise ValueError(f"Required parameter '{param_name}' not provided in named params")
    else:
        # Positional parameters: normalize each one
        normalized_param_lists = [
            _normalize_param_value(f"param_{i}", param)
            for i, param in enumerate(params)
        ]

    # Generate all combinations using itertools.product
    if not normalized_param_lists:
        # No parameters, just execute
        try:
            execution_params = ExecutionParams(
                session_id=session_id,
                mode="keyword",
                keyword=keyword,
                params=[],
                runner_type="keyword",
                use_printer=False
            )
            return await engine.execute(execution_params)
        except Exception as e:
            if isinstance(e, (SystemExit, KeyboardInterrupt, GeneratorExit)):
                raise
            raise RuntimeError(f"Keyword execution failed: {e}") from e

    # For named params, we need to convert to positional in method signature order
    if isinstance(params, dict):
        # Try each combination
        errors: List[tuple[tuple, str]] = []
        for combo in product(*normalized_param_lists):
            try:
                # Convert named params combo to positional args for ExecutionEngine
                # Map combo values to their parameter names, then build positional list in method order
                combo_dict = dict(zip(provided_param_names, combo))
                positional_args = []
                for param_name in all_param_names:
                    if param_name in combo_dict:
                        # Parameter was provided in request
                        param_value = combo_dict[param_name]
                        # If this is a list parameter and the value is a JSON string, keep it as-is
                        # Otherwise, it's already a string from the fallback mechanism
                        positional_args.append(param_value)
                    elif param_name in param_defaults:
                        # Parameter has a default - we need to include it
                        default_val = param_defaults[param_name]
                        # If it's a list parameter, serialize it as JSON
                        if param_is_list.get(param_name, False) and isinstance(default_val, list):
                            positional_args.append(json.dumps(default_val))
                        else:
                            # Convert default to string since ExecutionParams.params is List[str]
                            positional_args.append(str(default_val) if not isinstance(default_val, str) else default_val)
                    # If param not in combo_dict and not in param_defaults, it's required and should have been caught earlier

                execution_params = ExecutionParams(
                    session_id=session_id,
                    mode="keyword",
                    keyword=keyword,
                    params=positional_args,
                    runner_type="keyword",
                    use_printer=False
                )
                return await engine.execute(execution_params)
            except Exception as e:
                if isinstance(e, (SystemExit, KeyboardInterrupt, GeneratorExit)):
                    raise
                errors.append((combo, repr(e)))
    else:
        # Positional params: try each combination
        errors: List[tuple[tuple, str]] = []
        for combo in product(*normalized_param_lists):
            try:
                execution_params = ExecutionParams(
                    session_id=session_id,
                    mode="keyword",
                    keyword=keyword,
                    params=list(combo),
                    runner_type="keyword",
                    use_printer=False
                )
                return await engine.execute(execution_params)
            except Exception as e:
                if isinstance(e, (SystemExit, KeyboardInterrupt, GeneratorExit)):
                    raise
                errors.append((combo, repr(e)))

    # All combinations failed
    if errors:
        msg = "\n".join([f"{c} -> {err}" for c, err in errors])
        raise RuntimeError(
            f"All fallback attempts failed for keyword '{keyword}':\n{msg}"
        )
    raise RuntimeError(f"No valid fallback values provided for keyword '{keyword}'")


@app.post("/v1/sessions/{session_id}/action")
async def execute_keyword(session_id: str, request: ExecuteRequest):
    """
    Execute a keyword in the specified session.
    Supports both positional and named parameters with fallback support.
    Returns execution status and result.
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if request.mode != "keyword" or not request.keyword:
        raise HTTPException(status_code=400, detail="Only keyword mode with a keyword is supported")

    engine = ExecutionEngine(session_manager)
    execution_id = str(uuid.uuid4())

    try:
        await session.event_queue.put(ExecutionEvent(
            execution_id=execution_id,
            status="RUNNING",
            message=f"Starting keyword: {request.keyword}"
        ).model_dump())

        # Build keyword registry to get method signature for fallback handling
        registry = KeywordRegistry()
        action_keyword = session.optics.build(ActionKeyword)
        app_management = session.optics.build(AppManagement)
        verifier = session.optics.build(Verifier)
        registry.register(action_keyword)
        registry.register(app_management)
        registry.register(verifier)
        registry.register(FlowControl(session=session, keyword_map=registry.keyword_map))

        # Get the method to determine parameter structure
        keyword_slug = "_".join(request.keyword.split()).lower()
        method = registry.keyword_map.get(keyword_slug)

        if not method:
            raise OpticsError(
                Code.E0402,
                message=f"Keyword {request.keyword} not found"
            )

        # Execute with fallback support via ExecutionEngine
        result = await _execute_keyword_with_fallback(
            engine, session_id, request.keyword, request.params, method, session
        )

        await session.event_queue.put(ExecutionEvent(
            execution_id=execution_id,
            status="SUCCESS",
            message=f"Keyword {request.keyword} executed successfully"
        ).model_dump())

        return ExecutionResponse(
            execution_id=execution_id,
            status="SUCCESS",
            data={"result": result} if not isinstance(result, dict) else result
        )

    except Exception as e:
        await session.event_queue.put(ExecutionEvent(
            execution_id=execution_id,
            status="FAIL",
            message=f"Keyword {request.keyword} failed: {str(e)}"
        ).model_dump())
        if isinstance(e, OpticsError):
            raise HTTPException(status_code=e.status_code, detail=e.to_payload(include_status=True)) from e
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}") from e

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
    request = ExecuteRequest(mode="keyword", keyword=keyword, params=safe_params)
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
        params = {"filter_config": filter_config}
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

@app.get("/v1/sessions/{session_id}/events")
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

@app.get("/v1/keywords", response_model=List[KeywordInfo])
async def list_keywords():
    """
    List all available keywords and their parameters.
    """
    return discover_keywords()


async def event_generator(session: Session):
    """
    Generator for streaming execution events and heartbeats for a session.
    Yields events as SSE data.
    """
    HEARTBEAT_INTERVAL = 15  # seconds
    while True:
        try:
            try:
                event = await asyncio.wait_for(session.event_queue.get(), timeout=HEARTBEAT_INTERVAL)
                internal_logger.debug(f"Streaming event for session {session.session_id}: {event}")
                yield {"data": json.dumps(event)}
            except asyncio.TimeoutError:
                # Send heartbeat if no event in interval
                internal_logger.debug(f"Heartbeat for session {session.session_id}")
                yield {"data": json.dumps(ExecutionEvent(
                    execution_id="heartbeat",
                    status="HEARTBEAT",
                    message="No new event, sending heartbeat"
                ).model_dump())}
            except Exception as exc:
                internal_logger.error(f"Unexpected error while waiting for event: {exc}")
                yield {"data": json.dumps(ExecutionEvent(
                    execution_id="unknown",
                    status="ERROR",
                    message=f"Unexpected error while waiting for event: {exc}"
                ).model_dump())}
                break
        except AttributeError as attr_err:
            internal_logger.error(f"AttributeError in event streaming for session {session.session_id}: {attr_err}")
            yield {"data": json.dumps(ExecutionEvent(
                execution_id="unknown",
                status="ERROR",
                message=f"AttributeError: {attr_err}"
            ).model_dump())}
            break
        except asyncio.CancelledError as cancel_err:
            internal_logger.warning(f"Event streaming cancelled for session {session.session_id}: {cancel_err}")
            yield {"data": json.dumps(ExecutionEvent(
                execution_id="unknown",
                status="CANCELLED",
                message=f"Event streaming cancelled: {cancel_err}"
            ).model_dump())}
            raise
        except Exception as e:
            internal_logger.error(f"General error in event streaming for session {session.session_id}: {e}")
            yield {"data": json.dumps(ExecutionEvent(
                execution_id="unknown",
                status="ERROR",
                message=f"Event streaming failed: {e}"
            ).model_dump())}
            break

@app.delete("/v1/sessions/{session_id}/stop", response_model=TerminationResponse)
async def delete_session(session_id: str):
    """
    Terminate the specified session and clean up resources.
    Returns termination status.
    """
    kill_request = ExecuteRequest(
        mode="keyword",
        keyword="close_and_terminate_app",
        params=[]
    )
    try:
        await execute_keyword(session_id, kill_request)
    except Exception as e:
        internal_logger.error(f"Failed to terminate session {session_id}: {e}")
        if isinstance(e, OpticsError):
            raise HTTPException(status_code=e.status_code, detail=e.to_payload(include_status=True)) from e
        raise HTTPException(status_code=500, detail=f"Session termination failed: {e}") from e
    session_manager.terminate_session(session_id)
    internal_logger.info(f"Terminated session: {session_id}")
    return TerminationResponse()
