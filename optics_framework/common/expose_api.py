import json
import uuid
import asyncio
from typing import Optional, Dict, Any, List
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
from optics_framework.common.error import OpticsError
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

class SessionConfig(BaseModel):
    """
    Configuration for starting a new Optics session.
    """
    driver_sources: List[str]
    elements_sources: List[str] = []
    text_detection: List[str] = []
    image_detection: List[str] = []
    project_path: Optional[str] = None
    appium_url: Optional[str] = None
    appium_config: Optional[Dict[str, Any]] = None

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
        driver_sources = []
        for name in config.driver_sources:
            if name == "appium":
                driver_sources.append({
                    "appium": DependencyConfig(
                        enabled=True,
                        url=config.appium_url,
                        capabilities=config.appium_config or {}
                    )
                })
            else:
                driver_sources.append({name: DependencyConfig(enabled=True)})

        def build_source(source_list):
            return [{name: DependencyConfig(enabled=True)} for name in source_list]

        elements_sources = build_source(config.elements_sources)
        text_detection = build_source(config.text_detection)
        image_detection = build_source(config.image_detection)

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
        await execute_keyword(session_id, launch_request)
        return SessionResponse(session_id=session_id)
    except Exception as e:
        internal_logger.error(f"Failed to create session: {e}")
        if isinstance(e, OpticsError):
            raise HTTPException(status_code=e.status_code, detail=e.to_payload(include_status=True)) from e
        raise HTTPException(status_code=500, detail=f"Session creation failed: {e}") from e

@app.post("/v1/sessions/{session_id}/action")
async def execute_keyword(session_id: str, request: ExecuteRequest):
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
        runner_type="test_runner",
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
        if isinstance(e, OpticsError):
            raise HTTPException(status_code=e.status_code, detail=e.to_payload(include_status=True)) from e
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}") from e

# Helper for DRY keyword execution endpoints
def run_keyword_endpoint(session_id: str, keyword: str, params: List[str] = []) -> Any:
    """
    Helper to execute a keyword for a session using the execute_keyword endpoint.
    """
    request = ExecuteRequest(mode="keyword", keyword=keyword, params=params)
    return execute_keyword(session_id, request)


@app.get("/session/{session_id}/screenshot")
async def capture_screenshot(session_id: str):
    """
    Capture a screenshot in the specified session.
    Returns the screenshot result.
    """
    return await run_keyword_endpoint(session_id, "capture_screenshot")

@app.get("/session/{session_id}/driver-id")
async def get_driver_session_id(session_id: str):
    """
    Get the underlying Driver session ID for this Optics session.
    Returns ExecutionResponse with the session id in data.result.
    """
    return await run_keyword_endpoint(session_id, "get_driver_session_id")

@app.get("/session/{session_id}/elements")
async def get_elements(session_id: str):
    """
    Get interactive elements from the current session screen.
    Returns the elements result.
    """
    return await run_keyword_endpoint(session_id, "get_interactive_elements")

@app.get("/session/{session_id}/source")
async def get_pagesource(session_id: str):
    """
    Capture the page source from the current session.
    Returns the page source result.
    """
    return await run_keyword_endpoint(session_id, "capture_pagesource")

@app.get("/session/{session_id}/screen_elements")
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
    try:
        kill_request = ExecuteRequest(
            mode="keyword",
            keyword="close_and_terminate_app",
            params=[]
        )
        await execute_keyword(session_id, kill_request)
        session_manager.terminate_session(session_id)
        internal_logger.info(f"Terminated session: {session_id}")
        return TerminationResponse()
    except Exception as e:
        internal_logger.error(f"Failed to terminate session {session_id}: {e}")
        if isinstance(e, OpticsError):
            raise HTTPException(status_code=e.status_code, detail=e.to_payload(include_status=True)) from e
        raise HTTPException(status_code=500, detail=f"Session termination failed: {e}") from e
