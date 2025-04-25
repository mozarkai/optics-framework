from uuid import uuid4
from enum import Enum
from typing import Optional, Dict, List, Callable
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
