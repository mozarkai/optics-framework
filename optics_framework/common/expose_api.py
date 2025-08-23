import json
import uuid
import asyncio
import warnings
from typing import Optional, Dict, Any, List, Union, cast
from fastapi import FastAPI, HTTPException
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
from optics_framework.common.config_handler import Config, DependencyConfig
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
    """
    mode: str
    test_case: Optional[str] = None
    keyword: Optional[str] = None
    params: List[str] = []

class SessionResponse(BaseModel):
    """
    Response model for session creation.
    """
    session_id: str
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

@app.get("/", response_model=HealthCheckResponse, status_code=status.HTTP_200_OK)
async def health_check():
    """
    Health check endpoint for Optics Framework API.
    Returns API status and version.
    """
    return HealthCheckResponse(status="Optics Framework API is running", version=VERSION)

@app.post("/{device_id}/v1/sessions/start", response_model=SessionResponse)
async def create_session(device_id: str, config: SessionConfig):
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
        await execute_keyword(device_id, session_id, launch_request)
        return SessionResponse(session_id=session_id)
    except Exception as e:
        internal_logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=500, detail=f"Session creation failed: {e}") from e

@app.post("/{device_id}/v1/sessions/{session_id}/action")
async def execute_keyword(device_id: str, session_id: str, request: ExecuteRequest):
    """
    Execute a keyword in the specified session.
    Returns execution status and result.
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if request.mode != "keyword" or not request.keyword:
        raise HTTPException(status_code=400, detail="Only keyword mode with a keyword is supported")

    engine = ExecutionEngine(session_manager)
    execution_id = str(uuid.uuid4())

    execution_params = ExecutionParams(
        session_id=session_id,
        mode="keyword",
        keyword=request.keyword,
        params=request.params,
        runner_type="keyword",
        use_printer=False
    )

    try:
        await session.event_queue.put(ExecutionEvent(
            execution_id=execution_id,
            status="RUNNING",
            message=f"Starting keyword: {request.keyword}"
        ).model_dump())

        result = await engine.execute(execution_params)

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
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}") from e

# Helper for DRY keyword execution endpoints
def run_keyword_endpoint(device_id: str, session_id: str, keyword: str, params: List[str] = []) -> Any:
    """
    Helper to execute a keyword for a session using the execute_keyword endpoint.
    """
    request = ExecuteRequest(mode="keyword", keyword=keyword, params=params)
    return execute_keyword(device_id, session_id, request)


@app.get("/{device_id}/v1/session/{session_id}/screenshot")
async def capture_screenshot(device_id: str, session_id: str):
    """
    Capture a screenshot in the specified session.
    Returns the screenshot result.
    """
    return await run_keyword_endpoint(device_id, session_id, "capture_screenshot")

@app.get("/{device_id}/v1/session/{session_id}/elements")
async def get_elements(device_id: str, session_id: str):
    """
    Get interactive elements from the current session screen.
    Returns the elements result.
    """
    return await run_keyword_endpoint(device_id, session_id, "get_interactive_elements")

@app.get("/{device_id}/v1/session/{session_id}/source")
async def get_pagesource(device_id: str, session_id: str):
    """
    Capture the page source from the current session.
    Returns the page source result.
    """
    return await run_keyword_endpoint(device_id, session_id, "capture_pagesource")

@app.get("/{device_id}/v1/session/{session_id}/screen_elements")
async def screen_elements(device_id: str, session_id: str):
    """
    Capture and get screen elements from the current session.
    Returns the screen elements result.
    """
    return await run_keyword_endpoint(device_id, session_id, "capture_and_get_screen_elements")

@app.get("/{device_id}/v1/sessions/{session_id}/events")
async def stream_events(device_id: str, session_id: str):
    """
    Stream execution events for the specified session using Server-Sent Events (SSE).
    """
    session = session_manager.get_session(session_id)
    if not session:
        internal_logger.error(f"Session not found for event streaming: {session_id}")
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
    internal_logger.info(f"Starting event stream for session {session_id}")
    return EventSourceResponse(event_generator(session))

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

@app.delete("/{device_id}/v1/sessions/{session_id}/stop", response_model=TerminationResponse)
async def delete_session(device_id: str, session_id: str):
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
        await execute_keyword(device_id, session_id, kill_request)
    except Exception as e:
        internal_logger.error(f"Failed to terminate session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Session termination failed: {e}") from e
    session_manager.terminate_session(session_id)
    internal_logger.info(f"Terminated session: {session_id}")
    return TerminationResponse()
