from uuid import uuid4
from enum import Enum
from typing import Optional, Dict, List, Callable, Any
from pydantic import BaseModel, Field
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.error import OpticsError, Code

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

    def add_keyword(self, keyword: 'KeywordNode'):
        if not self.keywords_head:
            self.keywords_head = keyword
        else:
            current = self.keywords_head
            while current.next:
                current = current.next
            current.next = keyword

    def remove_keyword(self, keyword_name: str):
        current = self.keywords_head
        previous = None
        while current:
            if current.name == keyword_name:
                if previous:
                    previous.next = current.next
                else:
                    self.keywords_head = current.next
                return
            previous = current
            current = current.next

    def get_keyword(self, keyword_name: str) -> Optional['KeywordNode']:
        current = self.keywords_head
        while current:
            if current.name == keyword_name:
                return current
            current = current.next
        return None


class TestCaseNode(Node):
    modules_head: Optional[ModuleNode] = None
    next: Optional['TestCaseNode'] = None

    def add_module(self, module: 'ModuleNode'):
        if not self.modules_head:
            self.modules_head = module
        else:
            current = self.modules_head
            while current.next:
                current = current.next
            current.next = module

    def remove_module(self, module_name: str):
        current = self.modules_head
        previous = None
        while current:
            if current.name == module_name:
                if previous:
                    previous.next = current.next
                else:
                    self.modules_head = current.next
                return
            previous = current
            current = current.next

    def get_module(self, module_name: str) -> Optional['ModuleNode']:
        current = self.modules_head
        while current:
            if current.name == module_name:
                return current
            current = current.next
        return None


class TestSuite(BaseModel):
    test_cases_head: Optional[TestCaseNode] = None

    def add_test_case(self, test_case: TestCaseNode):
        if not self.test_cases_head:
            self.test_cases_head = test_case
        else:
            current = self.test_cases_head
            while current.next:
                current = current.next
            current.next = test_case

    def remove_test_case(self, test_case_name: str):
        current = self.test_cases_head
        previous = None
        while current:
            if current.name == test_case_name:
                if previous:
                    previous.next = current.next
                else:
                    self.test_cases_head = current.next
                return
            previous = current
            current = current.next

    def get_test_case(self, test_case_name: str) -> Optional[TestCaseNode]:
        current = self.test_cases_head
        while current:
            if current.name == test_case_name:
                return current
            current = current.next
        return None


# Data Models
class ModuleData(BaseModel):
    """Structure for module definitions."""
    modules: Dict[str, List[tuple[str, List[str]]]] = Field(default_factory=dict)

    def add_module_definition(self, name: str, definition: List[tuple[str, List[str]]]):
        self.modules[name] = definition

    def remove_module_definition(self, name: str):
        if name in self.modules:
            del self.modules[name]

    def get_module_definition(self, name: str) -> Optional[List[tuple[str, List[str]]]]:
        return self.modules.get(name)


class ElementData(BaseModel):
    """Structure for elements.

    Elements may hold multiple locator/representation values per key. The
    runner/consumers should try values in order as fallbacks until one
    succeeds.
    """
    elements: Dict[str, List[str]] = Field(default_factory=dict)

    def add_element(self, name: str, value: str):
        """Append a value for an element key.

        Args:
            name: element key/name
            value: locator or representation string (xpath, image path, text, etc.)
        """
        if name not in self.elements:
            self.elements[name] = []
        self.elements[name].append(value)

    def remove_element(self, name: str):
        """Remove all values for a key."""
        if name in self.elements:
            del self.elements[name]

    def get_element(self, name: str) -> Optional[List[str]]:
        """Return the list of values for a key (alias for get_values).

        Note: callers that previously expected a single string should call
        `get_first(name)` to obtain the highest-priority value.
        """
        return self.elements.get(name)

    def get_first(self, name: str) -> Optional[str]:
        """Return the first (highest-priority) value for a key, or None."""
        vals = self.elements.get(name)
        return vals[0] if vals else None

    def resolve_with_fallback(
        self,
        name: str,
        resolver: Callable[[str], Any],
        on_error: Optional[Callable[[Exception, str], None]] = None,
        max_attempts: Optional[int] = None,
    ) -> Any:
        """Try resolving each stored value for `name` using `resolver` until one succeeds.

        Args:
            name: element key
            resolver: function that takes a single value string and returns a result or raises on failure
            on_error: optional callback called as on_error(exception, tried_value) for each failure
            max_attempts: optional cap on attempts (overrides len(values))

        Returns:
            The first successful result returned by resolver.

        Raises:
            OpticsError(Code.X0201) if no values succeed or OpticsError(Code.E0201) if key missing.
        """
        values = self.get_element(name)
        if not values:
            raise OpticsError(Code.E0201, f"Element not found: {name}")

        attempts = 0
        cap = max_attempts if (max_attempts is not None) else len(values)
        last_exc: Optional[Exception] = None
        for v in values:
            if attempts >= cap:
                break
            attempts += 1
            try:
                return resolver(v)
            except Exception as e:
                last_exc = e
                if on_error:
                    try:
                        on_error(e, v)
                    except Exception:
                        internal_logger.debug("on_error callback raised an exception", exc_info=True)
                # continue to next value

        # exhausted
        raise OpticsError(Code.X0201, f"Element '{name}' not found after attempting {attempts} value(s); last error: {getattr(last_exc, 'args', last_exc)}")


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

    def add_api(self, api: ApiDefinition):
        self.apis[api.name] = api

    def remove_api(self, api_name: str):
        if api_name in self.apis:
            del self.apis[api_name]

    def get_api(self, api_name: str) -> Optional[ApiDefinition]:
        return self.apis.get(api_name)


class ApiData(BaseModel):
    global_defaults: Dict[str, Any] = Field(default_factory=dict)
    collections: Dict[str, ApiCollection] = Field(default_factory=dict)

    def add_collection(self, collection: ApiCollection):
        self.collections[collection.name] = collection

    def remove_collection(self, collection_name: str):
        if collection_name in self.collections:
            del self.collections[collection_name]

    def get_collection(self, collection_name: str) -> Optional[ApiCollection]:
        return self.collections.get(collection_name)


class TemplateData(BaseModel):
    """Structure for template image mappings."""
    templates: Dict[str, str] = Field(default_factory=dict)

    def add_template(self, name: str, path: str):
        self.templates[name] = path

    def remove_template(self, name: str):
        if name in self.templates:
            del self.templates[name]

    def get_template_path(self, name: str) -> Optional[str]:
        return self.templates.get(name)
