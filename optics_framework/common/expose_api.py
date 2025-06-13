import json
import uuid
import asyncio
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from optics_framework.common.session_manager import SessionManager, Session
from optics_framework.common.execution import ExecutionEngine
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.config_handler import Config, DependencyConfig

app = FastAPI(title="Optics Framework API", version="1.0")
session_manager = SessionManager()


class SessionConfig(BaseModel):
    """Schema for session creation, aligned with Config expectations."""
    driver_sources: list[str]  # Names of enabled drivers
    elements_sources: list[str] = []
    text_detection: list[str] = []
    image_detection: list[str] = []
    project_path: Optional[str] = None


class ExecuteRequest(BaseModel):
    """Schema for execution requests."""
    mode: str  # "batch", "dry_run", or "keyword"
    test_case: Optional[str] = None
    keyword: Optional[str] = None
    params: list[str] = []


class SessionResponse(BaseModel):
    """Schema for session creation response."""
    session_id: str
    status: str = "created"


class ExecutionResponse(BaseModel):
    """Schema for execution response."""
    execution_id: str
    status: str = "started"


class TerminationResponse(BaseModel):
    """Schema for session termination response."""
    status: str = "terminated"


class ExecutionEvent(BaseModel):
    """Schema for execution event payloads."""
    execution_id: str
    status: str  # e.g., "ERROR", "FAIL", "SUCCESS"
    message: Optional[str] = None


@app.post("/v1/sessions", response_model=SessionResponse)
async def create_session(config: SessionConfig):
    """
    Creates a new session with the specified configuration.

    :param config: Configuration for the session (enabled dependency names).
    :return: Details of the created session.
    """
    try:
        # Transform SessionConfig into Config format
        session_config_dict = {
            "driver_sources": [{"name": DependencyConfig(enabled=True)} for _ in config.driver_sources],
            "elements_sources": [{"name": DependencyConfig(enabled=True)} for _ in config.elements_sources],
            "text_detection": [{"name": DependencyConfig(enabled=True)} for _ in config.text_detection],
            "image_detection": [{"name": DependencyConfig(enabled=True)} for _ in config.image_detection],
            "project_path": config.project_path
        }
        session_config = Config(**session_config_dict)
        session_id = session_manager.create_session(session_config)
        internal_logger.info(
            f"Created session {session_id} with config: {config.model_dump()}")
        return SessionResponse(session_id=session_id)
    except Exception as e:
        internal_logger.error(f"Failed to create session: {e}")
        raise HTTPException(
            status_code=500, detail=f"Session creation failed: {e}")


@app.post("/v1/sessions/{session_id}/execute", response_model=ExecutionResponse)
async def execute(
    session_id: str,
    request: ExecuteRequest,
    background_tasks: BackgroundTasks
):
    """
    Triggers execution in a session as a background task.

    :param session_id: ID of the session to execute in.
    :param request: Execution request details.
    :param background_tasks: FastAPI background task handler.
    :return: Execution start confirmation.
    """
    session = session_manager.get_session(session_id)
    if not session:
        internal_logger.error(f"Session not found: {session_id}")
        raise HTTPException(status_code=404, detail="Session not found")

    execution_id = str(uuid.uuid4())
    internal_logger.info(
        f"Starting execution {execution_id} for session {session_id} with request: {request.model_dump()}")

    engine = ExecutionEngine(session_manager)
    background_tasks.add_task(
        run_execution,
        engine,
        session_id,
        execution_id,
        request.mode,
        request.test_case,
        request.keyword,
        request.params,
        session.event_queue
    )
    return ExecutionResponse(execution_id=execution_id)


@app.get("/v1/sessions/{session_id}/events")
async def stream_events(session_id: str):
    """
    Streams execution events for a session via Server-Sent Events (SSE).

    :param session_id: ID of the session to stream events from.
    :return: SSE stream of execution events.
    """
    session = session_manager.get_session(session_id)
    if not session:
        internal_logger.error(f"Session not found for event streaming: {session_id}")
        raise HTTPException(status_code=404, detail="Session not found")

    internal_logger.info(f"Starting event stream for session {session_id}")
    return EventSourceResponse(event_generator(session))


@app.delete("/v1/sessions/{session_id}", response_model=TerminationResponse)
async def delete_session(session_id: str):
    """
    Terminates a session.

    :param session_id: ID of the session to terminate.
    :return: Termination confirmation.
    """
    try:
        session_manager.terminate_session(session_id)
        internal_logger.info(f"Terminated session: {session_id}")
        return TerminationResponse()
    except Exception as e:
        internal_logger.error(f"Failed to terminate session {session_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Session termination failed: {e}")


async def run_execution(
    engine: ExecutionEngine,
    session_id: str,
    execution_id: str,
    mode: str,
    test_case: Optional[str],
    keyword: Optional[str],
    params: list[str],
    event_queue: Optional[asyncio.Queue]
):
    """Runs the requested execution in the background."""
    session = session_manager.get_session(session_id)
    if not session or not event_queue:
        internal_logger.error(
            f"Session {session_id} not found or invalid during execution {execution_id}")
        if event_queue:
            await event_queue.put(
                ExecutionEvent(
                    execution_id=execution_id,
                    status="ERROR",
                    message="Session not found or invalid"
                ).model_dump()
            )
        return

    try:
        await engine.execute(
            session_id=session_id,
            mode=mode,
            test_case=test_case,
            keyword=keyword,
            params=params,
            event_queue=event_queue
        )
        internal_logger.info(f"Execution {execution_id} completed successfully")
        await event_queue.put(
            ExecutionEvent(
                execution_id=execution_id,
                status="SUCCESS",
                message="Execution completed"
            ).model_dump()
        )
    except Exception as e:
        internal_logger.error(f"Execution {execution_id} failed: {e}")
        await event_queue.put(
            ExecutionEvent(
                execution_id=execution_id,
                status="FAIL",
                message=f"Execution failed: {str(e)}"
            ).model_dump()
        )


async def event_generator(session: Session):
    """Generates SSE events from the session's event queue."""
    while True:
        try:
            event = await session.event_queue.get()
            internal_logger.debug(
                f"Streaming event for session {session.session_id}: {event}")
            yield {"data": json.dumps(event)}
        except Exception as e:
            internal_logger.error(
                f"Error in event streaming for session {session.session_id}: {e}")
            yield {"data": json.dumps(
                ExecutionEvent(
                    execution_id="unknown",
                    status="ERROR",
                    message=f"Event streaming failed: {e}"
                ).model_dump()
            )}
            break


# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)
