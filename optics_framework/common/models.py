from uuid import uuid4
from enum import Enum
from typing import Optional, Dict, List, Callable, Any
from pydantic import BaseModel, Field

# State Enum
class State(str, Enum):
    NOT_RUN = "NOT_RUN"
    RUNNING = "RUNNING"
    COMPLETED_PASSED = "COMPLETED_PASSED"
    COMPLETED_FAILED = "COMPLETED_FAILED"
    RETRYING = "RETRYING"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"

# Node Base Class
class Node(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    state: State = State.NOT_RUN
    attempt_count: int = 0
    max_attempts: int = 3
    last_failure_reason: Optional[str] = None

# Linked List Nodes
class KeywordNode(Node):
    params: List[str] = Field(default_factory=list)
    method_ref: Optional[Callable] = None
    next: Optional['KeywordNode'] = None


class ModuleNode(Node):
    keywords_head: Optional[KeywordNode] = None
    next: Optional['ModuleNode'] = None


class TestCaseNode(Node):
    modules_head: Optional[ModuleNode] = None
    next: Optional['TestCaseNode'] = None

# Data Models
class ElementData(BaseModel):
    """Structure for elements."""
    elements: Dict[str, str] = Field(default_factory=dict)


class RequestDefinition(BaseModel):
    method: str
    headers: Dict[str, str] = Field(default_factory=dict)
    body: Optional[Any] = None
    timeout: Optional[int] = None


class ExpectedResultDefinition(BaseModel):
    expected_status: Optional[int] = None
    json_schema: Optional[Dict[str, Any]] = None
    jsonpath_assertions: Optional[List[Dict[str, Any]]] = None
    extract: Optional[Dict[str, str]] = None


class ApiDefinition(BaseModel):
    name: str
    description: Optional[str] = None
    endpoint: str
    request: RequestDefinition
    expected_result: Optional[ExpectedResultDefinition] = None



class ApiCollection(BaseModel):
    name: str
    base_url: str
    global_headers: Dict[str, str] = Field(default_factory=dict)
    apis: Dict[str, ApiDefinition] = Field(default_factory=dict)


class ApiData(BaseModel):
    global_defaults: Dict[str, Any] = Field(default_factory=dict)
    collections: Dict[str, ApiCollection] = Field(default_factory=dict)
