# Architecture Decision Records (ADRs)

This document records the key architectural decisions made in the Optics Framework, including the rationale, alternatives considered, and trade-offs. ADRs help understand why the framework is designed the way it is and provide context for future changes.

## What are ADRs?

Architecture Decision Records (ADRs) are documents that capture important architectural decisions made during the development of the framework. Each ADR describes:

- **Context**: The situation and requirements
- **Decision**: What was decided
- **Rationale**: Why this decision was made
- **Alternatives**: Other options considered
- **Consequences**: Impact and trade-offs

## ADR Format

Each ADR follows this structure:

```markdown
## ADR-XXX: [Title]

**Status:** [Proposed | Accepted | Deprecated | Superseded]

**Context:**
[The situation and requirements]

**Decision:**
[What was decided]

**Rationale:**
[Why this decision was made]

**Alternatives Considered:**
[Other options that were evaluated]

**Consequences:**
[Positive and negative impacts]
```

## Key Architectural Decisions

### ADR-001: Linked List Structure for Test Execution Hierarchy

**Status:** Accepted

**Context:**
The framework needs to represent test execution hierarchy (TestSuite → TestCase → Module → Keyword) in a way that supports:

- Sequential execution
- Dynamic modification
- State tracking per node
- Memory efficiency

**Decision:**
Use a linked list structure with node classes (TestCaseNode, ModuleNode, KeywordNode) instead of nested lists or trees.

**Rationale:**

1. **Sequential Execution**: Linked lists naturally represent sequential execution flow
2. **Memory Efficiency**: No array overhead, only stores necessary links
3. **Dynamic Structure**: Easy to add/remove nodes during execution
4. **State Tracking**: Each node can track its own execution state independently
5. **Traversal Simplicity**: Simple forward traversal matches execution flow

**Alternatives Considered:**

1. **Nested Lists/Dictionaries**:

   - Pros: Simple structure, easy to serialize
   - Cons: Less memory efficient, harder to modify during execution
   - Rejected: Doesn't support dynamic modification well

2. **Tree Structure**:

   - Pros: Hierarchical representation
   - Cons: More complex, overhead for parent/child relationships
   - Rejected: Overkill for sequential execution

3. **Array/List with Indices**:

   - Pros: Simple indexing
   - Cons: Reallocation overhead, less flexible
   - Rejected: Doesn't support dynamic structure well

**Consequences:**

- ✅ Efficient sequential execution
- ✅ Low memory overhead
- ✅ Easy to modify structure
- ✅ Natural state tracking
- ⚠️ No random access (must traverse)
- ⚠️ More complex serialization if needed

**Implementation:**
```python
class TestCaseNode(Node):
    modules_head: Optional[ModuleNode] = None
    next: Optional['TestCaseNode'] = None
```

---

### ADR-002: Factory Pattern with Dynamic Discovery

**Status:** Accepted

**Context:**
The framework needs to support multiple drivers, element sources, and vision models that can be added without modifying core code. Components should be discoverable and instantiable based on configuration.

**Decision:**
Use a factory pattern with dynamic module discovery and automatic registration. Factories scan package directories, discover implementations, and instantiate them based on configuration.

**Rationale:**

1. **Extensibility**: New engines can be added without core code changes
2. **Automatic Discovery**: No manual registration required
3. **Interface-Based**: Components discovered by interface implementation
4. **Configuration-Driven**: Selection based on configuration, not code
5. **Lazy Loading**: Modules loaded only when needed

**Alternatives Considered:**

1. **Manual Registration**:

   - Pros: Explicit control, no discovery overhead
   - Cons: Requires code changes for new engines
   - Rejected: Less extensible

2. **Plugin System**:

   - Pros: Standard plugin architecture
   - Cons: More complex, requires plugin infrastructure
   - Rejected: Overkill for current needs

3. **Dependency Injection Container**:

   - Pros: Standard DI pattern
   - Cons: More complex, requires container setup
   - Rejected: Too heavyweight

**Consequences:**

- ✅ Highly extensible
- ✅ No code changes for new engines
- ✅ Automatic discovery
- ✅ Interface-based selection
- ⚠️ Discovery overhead on first use
- ⚠️ Requires consistent naming conventions

**Implementation:**
```python
class GenericFactory:
    @classmethod
    def register_package(cls, package: str) -> None:
        # Recursively discover modules
        # Register module paths
```

---

### ADR-003: Strategy Pattern for Element Location

**Status:** Accepted

**Context:**
Elements can be located using multiple methods (XPath, text, OCR, image matching). The framework needs to try multiple strategies automatically until one succeeds (self-healing).

**Decision:**
Use the Strategy pattern with a StrategyManager that tries multiple location strategies in priority order until one succeeds.

**Rationale:**

1. **Self-Healing**: Automatic fallback to alternative methods
2. **Flexibility**: Easy to add new location strategies
3. **Priority-Based**: Fastest strategies tried first
4. **Separation of Concerns**: Each strategy is independent
5. **Extensibility**: New strategies can be added easily

**Alternatives Considered:**

1. **Single Method with Internal Fallback**:

   - Pros: Simpler implementation
   - Cons: Harder to extend, less flexible
   - Rejected: Not extensible enough

2. **Chain of Responsibility**:

   - Pros: Standard pattern for fallback
   - Cons: More complex, less explicit
   - Rejected: Strategy pattern is clearer

3. **Template Method**:

   - Pros: Code reuse
   - Cons: Less flexible, harder to extend
   - Rejected: Doesn't support multiple independent strategies well

**Consequences:**

- ✅ Self-healing test automation
- ✅ Easy to add new strategies
- ✅ Clear priority ordering
- ✅ Independent strategy implementations
- ⚠️ Multiple strategy attempts may be slower
- ⚠️ Requires strategy coordination

**Implementation:**
```python
class StrategyManager:
    def locate(self, element: str, index: int = 0):
        for strategy in self.locator_strategies:
            try:
                result = strategy.locate(element, index)
                if result is not None:
                    yield LocateResult(result, strategy)
            except Exception:
                continue
```

---

### ADR-004: Fallback Parameter System

**Status:** Accepted

**Context:**
Keywords need to support multiple fallback values for parameters (e.g., try multiple element identifiers). This should be automatic and transparent to users.

**Decision:**
Implement a `@fallback_params` decorator that automatically tries all combinations of fallback parameter values until one succeeds.

**Rationale:**

1. **User-Friendly**: Simple API, automatic fallback
2. **Flexible**: Supports multiple fallback parameters
3. **Transparent**: Works automatically without user intervention
4. **Error Aggregation**: Collects errors from all attempts
5. **Type-Safe**: Uses type hints for detection

**Alternatives Considered:**

1. **Manual Fallback in Keywords**:

   - Pros: Explicit control
   - Cons: Code duplication, error-prone
   - Rejected: Too much boilerplate

2. **Separate Fallback Keyword**:

   - Pros: Explicit fallback
   - Cons: More verbose, less intuitive
   - Rejected: Not user-friendly

3. **Configuration-Based Fallback**:

   - Pros: Centralized configuration
   - Cons: Less flexible, harder to use
   - Rejected: Too rigid

**Consequences:**

- ✅ Simple API for users
- ✅ Automatic fallback handling
- ✅ Supports multiple fallback parameters
- ✅ Error aggregation for debugging
- ⚠️ Exponential growth with multiple fallback params
- ⚠️ May try many combinations

**Implementation:**
```python
@fallback_params
def press_element(self, element: fallback_str, ...):
    # Automatically tries all combinations
```

---

### ADR-005: Queue-Based Logging System

**Status:** Accepted

**Context:**
Logging needs to be thread-safe, non-blocking, and support multiple logger instances (internal vs execution). Logging should not slow down test execution.

**Decision:**
Use queue-based logging with QueueHandler and background listeners. Separate loggers for internal operations and execution events.

**Rationale:**

1. **Thread-Safe**: Queues are thread-safe by design
2. **Non-Blocking**: Log writes don't block execution
3. **Background Processing**: Logs processed asynchronously
4. **Separation of Concerns**: Different loggers for different purposes
5. **Performance**: Doesn't impact execution speed

**Alternatives Considered:**

1. **Direct Logging**:

   - Pros: Simple, immediate
   - Cons: Blocking, may slow execution
   - Rejected: Performance impact

2. **File-Based Only**:

   - Pros: Simple
   - Cons: No console output, harder to debug
   - Rejected: Need console output for development

3. **Single Logger**:

   - Pros: Simpler
   - Cons: Can't separate internal vs execution logs
   - Rejected: Need separation for clarity

**Consequences:**

- ✅ Non-blocking execution
- ✅ Thread-safe logging
- ✅ Better performance
- ✅ Separated log streams
- ⚠️ More complex implementation
- ⚠️ Queue management overhead

**Implementation:**
```python
execution_queue_handler = QueueHandler(self.execution_log_queue)
self.execution_logger.addHandler(self.execution_queue_handler)
```

---

### ADR-006: Instance Caching in Factories

**Status:** Accepted

**Context:**
Factory instantiation can be expensive. The same components may be requested multiple times. Need to balance performance with memory usage.

**Decision:**
Cache factory instances by module name. Return cached instance if available, otherwise create and cache new instance.

**Rationale:**

1. **Performance**: Reduces instantiation overhead
2. **Singleton Behavior**: Ensures one instance per module name
3. **Memory Efficiency**: Reuses instances instead of creating duplicates
4. **Simple**: Easy to implement and understand

**Alternatives Considered:**

1. **No Caching**:

   - Pros: Simple, always fresh instances
   - Cons: Performance overhead, potential duplicates
   - Rejected: Too slow for repeated access

2. **Weak Reference Caching**:

   - Pros: Automatic cleanup
   - Cons: More complex, instances may be garbage collected
   - Rejected: Too complex for current needs

3. **LRU Cache**:

   - Pros: Bounded memory usage
   - Cons: More complex, may evict needed instances
   - Rejected: Overkill, instances should persist

**Consequences:**

- ✅ Faster instantiation after first use
- ✅ Singleton behavior per module
- ✅ Reduced memory allocation
- ⚠️ Instances persist in memory
- ⚠️ Manual cache clearing needed

**Implementation:**
```python
if name in cls._registry.instances:
    return cls._registry.instances[name]
# Create and cache
```

---

### ADR-007: Context Variables for Test Context

**Status:** Accepted

**Context:**
Components need access to current test case name without explicit parameter passing. This should work across async operations and threads.

**Decision:**
Use Python's `contextvars` module to provide thread-local and async-safe test context.

**Rationale:**

1. **Async-Safe**: Automatically propagated to async tasks
2. **Thread-Safe**: Each thread has its own context
3. **No Parameter Passing**: Access context without explicit parameters
4. **Standard Library**: Uses standard Python feature
5. **Isolation**: Context isolated per execution context

**Alternatives Considered:**

1. **Thread-Local Storage**:

   - Pros: Simple
   - Cons: Not async-safe, doesn't propagate to async tasks
   - Rejected: Doesn't work with async

2. **Explicit Parameter Passing**:

   - Pros: Explicit, clear
   - Cons: Verbose, pollutes method signatures
   - Rejected: Too verbose

3. **Global Variable**:

   - Pros: Simple access
   - Cons: Not thread-safe, race conditions
   - Rejected: Not safe for concurrent execution

**Consequences:**

- ✅ Async-safe context propagation
- ✅ Thread-safe
- ✅ Clean API (no parameter passing)
- ✅ Standard library solution
- ⚠️ Requires Python 3.7+
- ⚠️ Context must be set explicitly

**Implementation:**
```python
from contextvars import ContextVar
current_test_case: ContextVar[str] = ContextVar("current_test_case", default=None)
```

---

### ADR-008: Self-Healing Decorator Pattern

**Status:** Accepted

**Context:**
Action keywords need automatic element location with fallback strategies. This should be transparent to keyword implementations and handle errors gracefully.

**Decision:**
Use a `@with_self_healing` decorator that wraps action methods to provide automatic element location, strategy fallback, and error handling.

**Rationale:**

1. **Separation of Concerns**: Location logic separated from action logic
2. **Reusability**: Same decorator for all action methods
3. **Transparency**: Keyword implementations don't need location logic
4. **Error Handling**: Centralized error handling and aggregation
5. **Screenshot Management**: Automatic screenshot capture and saving

**Alternatives Considered:**

1. **Location in Each Keyword**:

   - Pros: Explicit control
   - Cons: Code duplication, error-prone
   - Rejected: Too much duplication

2. **Base Class with Location**:

   - Pros: Code reuse
   - Cons: Inheritance complexity, less flexible
   - Rejected: Decorator is more flexible

3. **Separate Location Service**:

   - Pros: Explicit service
   - Cons: More verbose, requires manual calls
   - Rejected: Decorator is cleaner

**Consequences:**

- ✅ Clean keyword implementations
- ✅ Reusable location logic
- ✅ Automatic error handling
- ✅ Screenshot management
- ⚠️ Decorator complexity
- ⚠️ May hide location failures

**Implementation:**
```python
@with_self_healing
def press_element(self, element: str, ..., *, located: Any = None):
    # located parameter provided by decorator
```

---

### ADR-009: Percentage-Based AOI Coordinates

**Status:** Accepted

**Context:**
Area of Interest (AOI) needs to work across different screen sizes and resolutions. Absolute pixel coordinates won't work for different devices.

**Decision:**
Use percentage-based coordinates (0-100) for AOI parameters. Convert to pixel coordinates based on screenshot dimensions.

**Rationale:**

1. **Screen Size Agnostic**: Works on any screen size
2. **Device Independent**: Same percentages work on different devices
3. **Intuitive**: Easy to understand (50% = half screen)
4. **Flexible**: Supports any screen resolution
5. **Portable**: Test cases work across devices

**Alternatives Considered:**

1. **Absolute Pixel Coordinates**:

   - Pros: Precise control
   - Cons: Device-specific, doesn't scale
   - Rejected: Not portable

2. **Normalized Coordinates (0-1)**:

   - Pros: Standard normalization
   - Cons: Less intuitive than percentages
   - Rejected: Percentages are more intuitive

3. **Relative Coordinates**:

   - Pros: Relative to element
   - Cons: More complex, requires reference element
   - Rejected: Too complex

**Consequences:**

- ✅ Works on any screen size
- ✅ Device-independent
- ✅ Intuitive percentage values
- ✅ Portable tests
- ⚠️ Less precise than pixels
- ⚠️ Conversion overhead

**Implementation:**
```python
def calculate_aoi_bounds(screenshot_shape, aoi_x, aoi_y, aoi_width, aoi_height):
    # Convert percentages to pixels
    x1 = int(width * (aoi_x / 100))
    y1 = int(height * (aoi_y / 100))
```

---

### ADR-010: Screenshot Streaming with Deduplication

**Status:** Superseded. Assertions now poll every strategy in one loop and share one screenshot per round between the vision strategies.

**Context:**
Timeout-based element location requires continuous screenshot capture. Need to avoid processing duplicate frames and manage memory efficiently.

**Decision:**
Use queue-based screenshot streaming with SSIM-based deduplication. Capture screenshots in background thread, deduplicate using structural similarity, store in filtered queue.

**Rationale:**

1. **Non-Blocking**: Capture doesn't block execution
2. **Deduplication**: Reduces processing of similar frames
3. **Memory Management**: Bounded queues prevent memory issues
4. **Efficiency**: Only process unique frames
5. **Background Processing**: Doesn't slow down execution

**Alternatives Considered:**

1. **No Deduplication**:

   - Pros: Simple
   - Cons: Processes many duplicate frames
   - Rejected: Too inefficient

2. **Hash-Based Deduplication**:

   - Pros: Fast comparison
   - Cons: Doesn't handle minor variations
   - Rejected: SSIM is more robust

3. **Fixed Interval Capture**:

   - Pros: Predictable
   - Cons: May miss changes, inefficient
   - Rejected: Less efficient than streaming

**Consequences:**
- ✅ Efficient frame processing
- ✅ Non-blocking capture
- ✅ Memory bounded
- ✅ Handles screen changes
- ⚠️ SSIM computation overhead
- ⚠️ Queue management complexity

**Implementation:**
```python
class ScreenshotStream:
    def process_screenshot_queue(self):
        similarity = ssim(gray_last_frame, gray_frame)
        if similarity >= 0.75:
            # Skip duplicate
```

---

### ADR-011: Multiple Logger Instances

**Status:** Accepted

**Context:**
Framework needs to separate internal debugging logs from execution event logs. Different log levels and formats are needed for different purposes.

**Decision:**
Use two separate logger instances: `internal_logger` for framework operations and `execution_logger` for test execution events.

**Rationale:**

1. **Separation of Concerns**: Different logs for different purposes
2. **Different Formats**: Internal logs can be more verbose
3. **Different Levels**: Can set different log levels
4. **User Experience**: Execution logs are user-facing
5. **Debugging**: Internal logs help with framework debugging

**Alternatives Considered:**

1. **Single Logger**:

   - Pros: Simpler
   - Cons: Can't separate concerns, mixed output
   - Rejected: Need separation

2. **Logger Hierarchy**:

   - Pros: Standard logging hierarchy
   - Cons: More complex, propagation issues
   - Rejected: Two loggers are sufficient

3. **Custom Logging System**:

   - Pros: Full control
   - Cons: More complex, reinventing wheel
   - Rejected: Standard logging is better

**Consequences:**

- ✅ Clear separation of logs
- ✅ Different formats and levels
- ✅ Better user experience
- ✅ Easier debugging
- ⚠️ More complex configuration
- ⚠️ Two loggers to manage

**Implementation:**
```python
internal_logger = logging.getLogger("optics.internal")
execution_logger = logging.getLogger("optics.execution")
```

---

### ADR-012: InstanceFallback Wrapper

**Status:** Accepted

**Context:**
When multiple drivers or element sources are configured, the framework should automatically try each one until one succeeds. This provides resilience and fallback capabilities.

**Decision:**
Wrap multiple instances in an `InstanceFallback` class that automatically tries each instance on method calls until one succeeds.

**Rationale:**

1. **Automatic Fallback**: No manual fallback logic needed
2. **Transparent**: Works like a single instance
3. **Resilient**: Continues working if one instance fails
4. **Simple API**: Users don't need to handle fallback
5. **Flexible**: Supports any number of instances

**Alternatives Considered:**

1. **Manual Fallback in Code**:

   - Pros: Explicit control
   - Cons: Code duplication, error-prone
   - Rejected: Too much boilerplate

2. **Proxy Pattern**:

   - Pros: Standard pattern
   - Cons: More complex, less transparent
   - Rejected: InstanceFallback is simpler

3. **Configuration-Based Selection**:

   - Pros: Explicit selection
   - Cons: No automatic fallback
   - Rejected: Need automatic fallback

**Consequences:**

- ✅ Automatic fallback
- ✅ Transparent usage
- ✅ Resilient to failures
- ✅ Simple API
- ⚠️ May hide failures
- ⚠️ Performance impact if many instances

**Implementation:**
```python
class InstanceFallback:
    def __getattr__(self, attr):
        for instance in self.instances:
            try:
                return getattr(instance, attr)(*args, **kwargs)
            except Exception:
                continue
```

---

### ADR-013: YAML and CSV Dual Format Support

**Status:** Accepted

**Context:**
Users have different preferences for test data format. Some prefer CSV (spreadsheet-friendly), others prefer YAML (more structured). Framework should support both.

**Decision:**
Support both CSV and YAML formats for test cases, modules, and elements. Use content-based file discovery to identify file types.

**Rationale:**

1. **User Choice**: Supports different user preferences
2. **Flexibility**: Users can choose best format for their needs
3. **Content-Based**: Files identified by content, not just extension
4. **Merging**: Multiple files of same type are merged
5. **Backward Compatible**: Supports existing CSV-based projects

**Alternatives Considered:**

1. **CSV Only**:

   - Pros: Simpler, one format
   - Cons: Less flexible, harder for complex data
   - Rejected: Too limiting

2. **YAML Only**:

   - Pros: More structured, better for complex data
   - Cons: Less spreadsheet-friendly
   - Rejected: CSV is important for many users

3. **JSON Support**:

   - Pros: Standard format
   - Cons: Less human-readable, another format to support
   - Rejected: YAML is more readable

**Consequences:**

- ✅ Flexible format choice
- ✅ Content-based discovery
- ✅ File merging support
- ✅ Backward compatible
- ⚠️ More complex file reading
- ⚠️ Two formats to maintain

**Implementation:**
```python
class CSVDataReader(DataReader):
    def read_test_cases(self, source: str) -> TestCases:
        # Read CSV

class YAMLDataReader(DataReader):
    def read_test_cases(self, source: str) -> TestCases:
        # Read YAML
```

---

### ADR-014: Session-Based Architecture

**Status:** Accepted

**Context:**
Framework needs to support multiple concurrent test executions, each with its own configuration, state, and resources. Need isolation between executions.

**Decision:**
Use session-based architecture where each test execution has its own session with isolated configuration, drivers, and state.

**Rationale:**

1. **Isolation**: Each session is independent
2. **Concurrency**: Supports multiple concurrent executions
3. **Resource Management**: Clear lifecycle for resources
4. **Configuration**: Per-session configuration
5. **State Management**: Session-scoped state

**Alternatives Considered:**

1. **Global State**:

   - Pros: Simpler
   - Cons: No isolation, race conditions
   - Rejected: Not safe for concurrent execution

2. **Thread-Local Storage**:

   - Pros: Automatic isolation
   - Cons: Not async-safe, less explicit
   - Rejected: Sessions are more explicit

3. **Context Manager**:

   - Pros: Automatic cleanup
   - Cons: Less flexible, harder to share
   - Rejected: Sessions need more flexibility

**Consequences:**

- ✅ Session isolation
- ✅ Concurrent execution support
- ✅ Clear resource lifecycle
- ✅ Per-session configuration
- ⚠️ Session management overhead
- ⚠️ Need to pass session_id

**Implementation:**
```python
class SessionManager:
    def create_session(self, config, ...) -> str:
        session_id = str(uuid4())
        session = Session(session_id, config, ...)
        self.sessions[session_id] = session
        return session_id
```

---

### ADR-015: Builder Pattern for Component Construction

**Status:** Accepted

**Context:**
Complex component hierarchies need to be constructed with proper dependency injection. Components have dependencies on each other (e.g., element sources need drivers).

**Decision:**
Use Builder pattern (OpticsBuilder) to construct component hierarchies with automatic dependency injection.

**Rationale:**

1. **Complex Construction**: Handles complex component setup
2. **Dependency Injection**: Automatically injects dependencies
3. **Fluent API**: Method chaining for readability
4. **Validation**: Can validate configuration during construction
5. **Flexibility**: Supports different component combinations

**Alternatives Considered:**

1. **Direct Instantiation**:

   - Pros: Simple, explicit
   - Cons: Manual dependency management, error-prone
   - Rejected: Too error-prone

2. **Factory Methods**:

   - Pros: Encapsulates creation
   - Cons: Less flexible, harder to extend
   - Rejected: Builder is more flexible

3. **Dependency Injection Container**:

   - Pros: Standard DI pattern
   - Cons: More complex, requires container
   - Rejected: Builder is simpler for this use case

**Consequences:**

- ✅ Handles complex construction
- ✅ Automatic dependency injection
- ✅ Fluent API
- ✅ Configuration validation
- ⚠️ More complex than direct instantiation
- ⚠️ Builder state management

**Implementation:**
```python
builder = OpticsBuilder(session)
builder.add_driver(config)
builder.add_element_source(config)
driver = builder.get_driver()
```

---

## Historical Context

### Framework Evolution

The Optics Framework has evolved through several key phases:

**Phase 1: Initial Design (Early Development)**

- Focus on basic automation capabilities
- Single driver support (Appium)
- Simple element location (XPath only)
- CSV-based test cases

**Phase 2: Vision Integration**

- Added OCR and image matching capabilities
- Multiple location strategies
- Self-healing mechanism
- Template-based image location

**Phase 3: Extensibility**

- Factory pattern for dynamic discovery
- Multiple driver support
- Plugin architecture for engines
- Strategy pattern for location

**Phase 4: API and CLI**

- REST API layer
- CLI interface
- Session management
- Event system

**Phase 5: Advanced Features**

- Fallback parameters
- AOI support
- Screenshot streaming
- Performance optimizations

### Design Principles Evolution

**Early Principles:**

- Simplicity
- Ease of use
- CSV-based (no code)

**Current Principles:**

- Modularity
- Extensibility
- Resilience (self-healing)
- a bit more focus on Performance
- Separation of concerns

### Key Design Influences

1. **Robot Framework**: Keyword-based approach, library pattern
2. **Selenium/Appium**: Driver abstraction, element location
3. **Factory Pattern**: Dynamic component creation
4. **Strategy Pattern**: Multiple location methods
5. **Builder Pattern**: Complex object construction

### Breaking Changes and Migrations

**None documented yet** - Framework is still in active development.

Future ADRs should document:

- Breaking changes
- Migration guides
- Deprecation notices
- Version compatibility

## Contributing ADRs

When making significant architectural decisions:

1. **Create ADR**: Document the decision using the ADR format
2. **Number Sequentially**: Use ADR-XXX format
3. **Update This Document**: Add to the list above
4. **Review**: Get team review before implementation
5. **Status Tracking**: Update status as decision evolves

## ADR Status Values

- **Proposed**: Decision under consideration
- **Accepted**: Decision made and implemented
- **Deprecated**: Decision replaced by newer ADR
- **Superseded**: Decision replaced by ADR-XXX

## Related Documentation

- [Architecture Overview](../architecture.md) - High-level architecture
- [Components](components.md) - Component implementations
- [Strategies](strategies.md) - Strategy pattern implementation
- [Execution](execution.md) - Execution architecture
- [Extending](extending.md) - Extension guidelines
