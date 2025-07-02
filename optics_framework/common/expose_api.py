import json
import uuid
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from optics_framework.common.session_manager import SessionManager, Session
from optics_framework.common.execution import (
    ExecutionEngine,
    ExecutionParams,
    TestCaseNode,
    ElementData,
)
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.config_handler import Config, DependencyConfig

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
    driver_sources: List[str]
    elements_sources: List[str] = []
    text_detection: List[str] = []
    image_detection: List[str] = []
    project_path: Optional[str] = None
    appium_url: Optional[str] = None
    appium_config: Optional[Dict[str, Any]] = None

class AppiumUpdateRequest(BaseModel):
    session_id: str
    url: str
    capabilities: Dict[str, Any]

class ExecuteRequest(BaseModel):
    mode: str
    test_case: Optional[str] = None
    keyword: Optional[str] = None
    params: List[str] = []

class SessionResponse(BaseModel):
    session_id: str
    status: str = "created"

class ExecutionResponse(BaseModel):
    execution_id: str
    status: str = "started"
    data: Optional[Dict[str, Any]] = None

class TerminationResponse(BaseModel):
    status: str = "terminated"

class ExecutionEvent(BaseModel):
    execution_id: str
    status: str
    message: Optional[str] = None

@app.post("/v1/sessions/start", response_model=SessionResponse)
async def create_session(config: SessionConfig):
    try:
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
            project_path=config.project_path
        )
        session_id = session_manager.create_session(session_config)
        internal_logger.info(
            f"Created session {session_id} with config: {config.model_dump()}"
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
        raise HTTPException(status_code=500, detail=f"Session creation failed: {e}")

@app.post("/v1/sessions/{session_id}/action")
async def execute_keyword(session_id: str, request: ExecuteRequest):
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
        test_cases=TestCaseNode(id="direct", name="direct_keyword"),
        modules={},
        elements=ElementData(),
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
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}")

@app.get("/session/{session_id}/screenshot")
async def capture_screenshot(session_id: str):
    request = ExecuteRequest(mode="keyword", keyword="capture_screenshot", params=[])
    return await execute_keyword(session_id, request)

@app.get("/session/{session_id}/elements")
async def get_elements(session_id: str):
    request = ExecuteRequest(mode="keyword", keyword="get_interactive_elements", params=[])
    return await execute_keyword(session_id, request)

@app.get("/session/{session_id}/source")
async def get_pagesource(session_id: str):
    request = ExecuteRequest(mode="keyword", keyword="capture_pagesource", params=[])
    return await execute_keyword(session_id, request)

@app.get("/session/{session_id}/screen_elements")
async def screen_elements(session_id: str):
    request = ExecuteRequest(mode="keyword", keyword="capture_and_get_screen_elements", params=[])
    return await execute_keyword(session_id, request)

@app.get("/v1/sessions/{session_id}/events")
async def stream_events(session_id: str):
    session = session_manager.get_session(session_id)
    if not session:
        internal_logger.error(f"Session not found for event streaming: {session_id}")
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
    internal_logger.info(f"Starting event stream for session {session_id}")
    return EventSourceResponse(event_generator(session))

async def event_generator(session: Session):
    while True:
        try:
            event = await session.event_queue.get()
            internal_logger.debug(f"Streaming event for session {session.session_id}: {event}")
            yield {"data": json.dumps(event)}
        except Exception as e:
            internal_logger.error(f"Error in event streaming for session {session.session_id}: {e}")
            yield {"data": json.dumps(ExecutionEvent(
                execution_id="unknown",
                status="ERROR",
                message=f"Event streaming failed: {e}"
            ).model_dump())}
            break

@app.delete("/v1/sessions/{session_id}/stop", response_model=TerminationResponse)
async def delete_session(session_id: str):
    try:
        session_manager.terminate_session(session_id)
        internal_logger.info(f"Terminated session: {session_id}")
        return TerminationResponse()
    except Exception as e:
        internal_logger.error(f"Failed to terminate session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Session termination failed: {e}")

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)
