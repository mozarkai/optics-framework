# Help Wanted

!!! info "Contributing to Optics Framework"
    We welcome contributions from the community! This document outlines areas where the Optics Framework could benefit from your help. Whether you're a beginner or an experienced developer, there's something for everyone.

This document identifies specific areas for improvement across the Optics Framework. Each section includes the current state, goals, implementation details, and priority levels to help you choose where to contribute.

## Priority and Difficulty Levels

Each item is tagged with:

**Priority Levels:**

- **High Priority**: Critical for production use or security
- **Medium Priority**: Important for usability and completeness
- **Low Priority**: Nice to have improvements

**Difficulty Levels:**

- **Beginner**: Good first contribution, minimal framework knowledge needed
- **Intermediate**: Requires some framework knowledge
- **Advanced**: Requires deep framework understanding

---

## 1. Stateless API Layer

**Priority**: High | **Difficulty**: Advanced

### Current State

The API layer maintains state in `SessionManager` which stores sessions in memory. Sessions cannot be migrated between instances, making it difficult to scale horizontally or recover from instance failures.

### Goal

Make the API layer stateless so sessions can be migrated/moved from one instance to another without losing context. This enables:

- Horizontal scaling of API instances
- Session recovery after instance failures
- Load balancing across multiple instances
- Zero-downtime deployments

### Key Areas

#### Session Storage

**Current**: Sessions stored in-memory only (`optics_framework/common/session_manager.py`)

**Needed**:

- External session storage (database, Redis, etc.)
- Session serialization/deserialization
- Session state export/import functionality

#### Session Context

**Current**: Session contains driver instances, element sources, vision models that cannot be serialized

**Needed**:

- Serialize all session state (configuration, driver state, element sources, vision models)
- Reconstruct session from serialized state
- Handle driver reconnection on migration
- Store driver session IDs and connection details

#### API Endpoints

**New Endpoints Needed**:

- `POST /v1/sessions/{id}/export` - Export session state as JSON
- `POST /v1/sessions/import` - Import session state and recreate session
- `POST /v1/sessions/{id}/migrate` - Migrate session to another instance

### Files to Modify

- `optics_framework/common/session_manager.py` - Add serialization support
- `optics_framework/common/expose_api.py` - Add migration endpoints
- `optics_framework/common/models.py` - Add session state models (Pydantic)
- `optics_framework/common/optics_builder.py` - Support session reconstruction

### Related Documentation

- [Design: Stateless API Layer](stateless_api_design.md) — implementation design (Layer 1, anticipating multi-pod + device scheduling)
- [Session Management](../architecture/components.md#sessionmanager)
- [REST API Layer](../architecture/api_layer.md)

---

## 2. Parallel Strategy Execution

**Priority**: High | **Difficulty**: Advanced

### Current State

Strategies are currently executed **sequentially** (one after another) in a fallback chain. When locating an element, the framework tries Strategy 1, waits for it to complete (success or failure), then tries Strategy 2, and so on. This sequential approach, while reliable, is slower than necessary because:

1. **Independent strategies wait unnecessarily**: Many strategies can run simultaneously since they don't depend on each other
2. **Total execution time is sum of all strategies**: If Strategy 1 takes 2s and Strategy 2 takes 3s, total time is 5s even if Strategy 2 could succeed immediately
3. **Resource underutilization**: CPU, I/O, and network resources are idle while waiting for sequential execution

**Example of Current Sequential Flow**:

```python
# Current: Sequential execution
1. Try XPathStrategy.find_element() → Wait 2s → Fail
2. Try TextElementStrategy.find_element() → Wait 1s → Fail
3. Try TextDetectionStrategy.find_element() → Wait 3s → Success
# Total time: 6 seconds
```

### Goal

Implement **parallel strategy execution** where multiple independent strategies run simultaneously, significantly reducing element location time while maintaining the same reliability and fallback behavior.

**Benefits**:

- **Faster element location**: Strategies execute concurrently, returning the first successful result
- **Better resource utilization**: CPU, I/O, and network resources used efficiently
- **Improved user experience**: Tests execute faster, especially with multiple fallback strategies
- **Maintains reliability**: Still supports fallback, but optimizes for the common case

**Example of Desired Parallel Flow**:

```python
# Desired: Parallel execution
1. Start XPathStrategy.find_element() → (runs in background)
2. Start TextElementStrategy.find_element() → (runs in background)
3. Start TextDetectionStrategy.find_element() → (runs in background)
4. First successful result returns → Total time: ~3 seconds (longest strategy)
```

### Execution Semantics

**Critical Requirement**: Only one strategy should execute the final keyword/action. Once a strategy successfully performs the keyword, all remaining strategies must be immediately aborted.

**Execution Rules**:

1. **Parallel Location Phase**: Multiple strategies run in parallel to locate the element
   - All strategies attempt to find the element simultaneously
   - First successful location result is selected
   - Remaining location attempts are cancelled/aborted

2. **Single Action Execution**: Only the winning strategy executes the keyword/action
   - The strategy that successfully located the element proceeds to execute the action
   - Other strategies are aborted before they can execute any actions
   - This prevents duplicate actions (e.g., clicking the same button twice)

3. **Fallback Behavior**: If the selected strategy fails to execute the keyword
   - Abort the failed strategy
   - Move to the next successful location result (if available)
   - If no other strategies succeeded in location, try the next strategy in sequence

**Example Flow**:

```python
# Parallel location phase
strategy1_result = await XPathStrategy.locate(element)      # Success: found element
strategy2_result = await TextStrategy.locate(element)      # Success: found element
strategy3_result = await OCRStrategy.locate(element)       # Still running...

# Strategy 1 wins (first success)
if strategy1_result:
    # Abort strategy 2 and 3 immediately
    abort_strategy(strategy2)
    abort_strategy(strategy3)

    # Only strategy 1 executes the keyword
    try:
        await strategy1.execute_keyword("press_element", strategy1_result)
        return success  # Done! No other strategies execute
    except Exception:
        # Strategy 1 failed to execute, try strategy 2
        abort_strategy(strategy1)
        await strategy2.execute_keyword("press_element", strategy2_result)
        return success
```

**Implementation Requirements**:

- **Cancellation Tokens**: Use `asyncio.CancelledError` or cancellation tokens to abort strategies
- **Early Exit**: Return immediately when first strategy succeeds
- **Resource Cleanup**: Properly clean up aborted strategies (close connections, release locks)
- **Error Handling**: Handle cancellation gracefully without side effects

### Key Scenarios

#### Scenario 1: XPath with Parallel Coordinate Discovery

**Current Behavior**:

When given an XPath, the framework:
1. Tries `find_element()` using the XPath directly
2. If that fails, tries other strategies sequentially

**Desired Parallel Behavior**:

When given an XPath, simultaneously:

1. **Primary path**: Execute `find_element()` using the XPath directly
2. **Parallel path**: Search page source to find the XPath and extract coordinates
3. **Fallback path**: If XPath is not accessible, use coordinates from page source

**Implementation Flow**:

```python
# Parallel execution
async def locate_with_xpath(element: str):
    tasks = [
        # Task 1: Direct findElement
        find_element_direct(element),

        # Task 2: Parse page source for XPath and get coordinates
        parse_page_source_for_coordinates(element),

        # Task 3: Alternative XPath variations
        try_xpath_variations(element)
    ]

    # Return first successful result
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return first_successful_result(results)
```

#### Scenario 2: Text with Parallel XPath Discovery and OCR

**Current Behavior**:

When given text, the framework:
1. Tries text-based element location
2. If that fails, tries OCR sequentially
3. If that fails, tries other strategies

**Desired Parallel Behavior**:

When given text, simultaneously:

1. **Path A**: Search page source to find text and convert to XPath, then execute `find_element()`
2. **Path B**: Capture screenshot and use OCR/text detection to find coordinates
3. **Path C**: Try direct text matching in element source

**Implementation Flow**:

```python
# Parallel execution for text
async def locate_with_text(text: str):
    tasks = [
        # Task 1: Page source → XPath → findElement
        page_source_to_xpath_to_find_element(text),

        # Task 2: Screenshot → OCR → Coordinates
        screenshot_ocr_to_coordinates(text),

        # Task 3: Direct text element location
        direct_text_element_location(text)
    ]

    # Return first successful result
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return first_successful_result(results)
```

#### Scenario 3: Image Template with Parallel Detection Methods

**Current Behavior**:

When given an image template, tries image detection strategies sequentially.

**Desired Parallel Behavior**:

Simultaneously:
1. Template matching (OpenCV)
2. Remote OIR service
3. Alternative image detection models

### Implementation Approach

#### Architecture Changes

**1. Strategy Execution Model**

Convert from sequential to parallel execution with abort capability:

```python
# Current: Sequential
class StrategyManager:
    def locate(self, element: str):
        for strategy in self.strategies:
            result = strategy.locate(element)  # Blocks here
            if result:
                return result
        raise ElementNotFoundError()

# Desired: Parallel with abort
class StrategyManager:
    async def locate(self, element: str):
        # Create tasks with cancellation support
        tasks = {
            strategy: asyncio.create_task(strategy.locate_async(element))
            for strategy in self.strategies
        }

        # Wait for first success, then abort others
        for strategy, task in tasks.items():
            try:
                result = await task
                if result:
                    # Abort all remaining tasks
                    for other_strategy, other_task in tasks.items():
                        if other_strategy != strategy and not other_task.done():
                            other_task.cancel()
                    return result
            except asyncio.CancelledError:
                continue

        raise ElementNotFoundError()
```

**2. Strategy Interface Updates**

Make strategies async-compatible:

```python
# Current
class LocatorStrategy(ABC):
    def locate(self, element: str, index: int = 0):
        # Synchronous implementation
        pass

# Desired
class LocatorStrategy(ABC):
    async def locate_async(self, element: str, index: int = 0):
        # Asynchronous implementation
        pass

    def locate(self, element: str, index: int = 0):
        # Synchronous wrapper for backward compatibility
        return run_async(self.locate_async(element, index))
```

**3. Parallel Execution Groups with Abort**

Group strategies that can run in parallel with abort capability:

```python
class ParallelStrategyGroup:
    """Groups strategies that can execute in parallel"""

    def __init__(self, strategies: List[LocatorStrategy]):
        self.strategies = strategies
        self.priority = min(s.priority for s in strategies)

    async def execute_parallel(self, element: str):
        """Execute all strategies in parallel, return first success, abort others"""
        # Create cancellable tasks
        tasks = {
            strategy: asyncio.create_task(strategy.locate_async(element))
            for strategy in self.strategies
        }

        # Wait for first success
        for strategy, task in tasks.items():
            try:
                result = await task
                if result:
                    # Abort all remaining tasks
                    self._abort_remaining(tasks, strategy)
                    return result
            except asyncio.CancelledError:
                continue

        return None

    def _abort_remaining(self, tasks: dict, winner: LocatorStrategy):
        """Cancel all tasks except the winner"""
        for strategy, task in tasks.items():
            if strategy != winner and not task.done():
                task.cancel()
```

**4. Dependency Management**

Identify which strategies can run in parallel vs. which must be sequential:

```python
class StrategyDependencyGraph:
    """Manages strategy dependencies and parallel execution"""

    def can_run_parallel(self, strategy1: LocatorStrategy, strategy2: LocatorStrategy) -> bool:
        """Check if two strategies can run in parallel"""
        # Strategies can run in parallel if:
        # 1. They don't modify shared state
        # 2. They don't depend on each other's results
        # 3. They use different resources (e.g., page source vs screenshot)
        return (
            not strategy1.modifies_shared_state() and
            not strategy2.modifies_shared_state() and
            strategy1.resource_type() != strategy2.resource_type()
        )
```

#### Specific Implementation Patterns

**Pattern 1: XPath with Coordinate Discovery**

```python
async def locate_xpath_parallel(element: str):
    """Locate element by XPath with parallel coordinate discovery"""

    async def direct_find():
        """Direct findElement using XPath"""
        return await element_source.locate(element)

    async def page_source_coords():
        """Parse page source to find XPath and get coordinates"""
        page_source, _ = await element_source.get_page_source()
        xpath_node = parse_xpath_from_page_source(page_source, element)
        if xpath_node:
            return get_coordinates_from_node(xpath_node)
        return None

    # Execute both in parallel
    results = await asyncio.gather(
        direct_find(),
        page_source_coords(),
        return_exceptions=True
    )

    # Prefer direct find, fallback to coordinates
    if results[0] and not isinstance(results[0], Exception):
        return results[0]
    if results[1] and not isinstance(results[1], Exception):
        return results[1]
    raise ElementNotFoundError()
```

**Pattern 2: Text with Multi-Path Discovery**

```python
async def locate_text_parallel(text: str):
    """Locate element by text with parallel multi-path discovery"""

    async def page_source_path():
        """Page source → Find text → Convert to XPath → findElement"""
        page_source, _ = await element_source.get_page_source()
        xpath = find_text_in_page_source(page_source, text)
        if xpath:
            return await element_source.locate(xpath)
        return None

    async def screenshot_ocr_path():
        """Screenshot → OCR → Find coordinates"""
        screenshot = await element_source.capture()
        text_locations = await text_detection.find_element(screenshot, text)
        if text_locations:
            return text_locations[0]  # Return first match
        return None

    async def direct_text_path():
        """Direct text element location"""
        return await element_source.locate_by_text(text)

    # Execute all three paths in parallel
    results = await asyncio.gather(
        page_source_path(),
        screenshot_ocr_path(),
        direct_text_path(),
        return_exceptions=True
    )

    # Return first successful result
    for result in results:
        if result and not isinstance(result, Exception):
            return result
    raise ElementNotFoundError()
```

**Pattern 3: Resource-Aware Parallel Execution**

```python
class ResourceAwareStrategyExecutor:
    """Executes strategies in parallel while managing resources"""

    def __init__(self):
        self.page_source_lock = asyncio.Lock()
        self.screenshot_lock = asyncio.Lock()
        self.driver_lock = asyncio.Lock()

    async def execute_parallel(self, strategies: List[LocatorStrategy], element: str):
        """Execute strategies in parallel with resource management"""

        # Group strategies by resource type
        page_source_strategies = [s for s in strategies if s.uses_page_source()]
        screenshot_strategies = [s for s in strategies if s.uses_screenshot()]
        driver_strategies = [s for s in strategies if s.uses_driver()]

        # Execute groups in parallel, but serialize within groups if needed
        tasks = []

        if page_source_strategies:
            tasks.append(self._execute_with_lock(
                page_source_strategies, element, self.page_source_lock
            ))

        if screenshot_strategies:
            tasks.append(self._execute_with_lock(
                screenshot_strategies, element, self.screenshot_lock
            ))

        if driver_strategies:
            tasks.append(self._execute_with_lock(
                driver_strategies, element, self.driver_lock
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return first_successful_result(results)
```

### Key Implementation Areas

#### 1. Async Strategy Interface

**Files to Modify**:

- `optics_framework/common/strategies.py` - Add async methods to `LocatorStrategy`
- All strategy implementations - Add `locate_async()` methods

**Changes Needed**:

- Convert `locate()` methods to async
- Maintain backward compatibility with sync wrappers
- Update strategy base class

#### 2. Parallel Execution Engine

**Files to Create/Modify**:

- `optics_framework/common/parallel_strategy_executor.py` - New parallel executor
- `optics_framework/common/strategies.py` - Update `StrategyManager` for parallel execution

**Features**:

- Parallel task execution using `asyncio.create_task()` and cancellation
- First-success-wins semantics with immediate abort of remaining strategies
- Exception handling and aggregation
- Resource management (locks for shared resources)
- Cancellation token support for graceful abort
- Only winning strategy executes the keyword/action

#### 3. Strategy Dependency Analysis

**Files to Create**:

- `optics_framework/common/strategy_dependencies.py` - Dependency analysis

**Features**:

- Identify which strategies can run in parallel
- Group strategies by resource requirements
- Manage resource locks (page source, screenshot, driver)

#### 4. Configuration and Control

**Files to Modify**:

- `optics_framework/common/config_handler.py` - Add parallel execution config
- `optics_framework/common/models.py` - Add configuration models

**Configuration Options**:

```yaml
strategy_execution:
  mode: "parallel"  # "sequential" or "parallel"
  max_parallel_strategies: 5
  timeout_per_strategy: 10
  resource_locks:
    page_source: true
    screenshot: true
    driver: false
```

### Performance Considerations

#### Expected Improvements

- **XPath location**: 40-60% faster (2-3 strategies in parallel)
- **Text location**: 50-70% faster (3-4 strategies in parallel)
- **Image location**: 30-50% faster (2-3 detection methods in parallel)
- **Overall test execution**: 20-40% faster for tests with many element locations

#### Resource Management

- **CPU**: Better utilization with parallel I/O operations
- **Memory**: Slight increase due to parallel task execution
- **Network**: Better utilization for remote services (OCR, OIR)
- **Driver**: Careful locking to prevent driver conflicts

### Testing Requirements

#### Unit Tests

- Test parallel execution with mock strategies
- Test first-success-wins behavior
- Test abort/cancellation of remaining strategies when one succeeds
- Test that only one strategy executes the keyword/action
- Test exception handling
- Test resource locking
- Test graceful cancellation cleanup

#### Integration Tests

- Test with real drivers and element sources
- Test performance improvements
- Test backward compatibility
- Test configuration options

### Migration Path

#### Phase 1: Add Async Support (Non-Breaking)

- Add async methods alongside sync methods
- Maintain backward compatibility
- No behavior changes

#### Phase 2: Parallel Execution (Opt-In)

- Add configuration option for parallel execution
- Default to sequential for safety
- Allow users to opt-in

#### Phase 3: Parallel by Default

- Make parallel execution the default
- Keep sequential as fallback option
- Optimize based on real-world usage

### Files to Modify

**Core Strategy System**:

- `optics_framework/common/strategies.py` - Add parallel execution support
- `optics_framework/common/execution.py` - Update execution flow

**Strategy Implementations**:

- `optics_framework/common/strategies.py` - Update all strategy classes
- All element source implementations - Add async support

**Configuration**:

- `optics_framework/common/config_handler.py` - Add parallel execution config
- `optics_framework/common/models.py` - Add configuration models

**New Files**:

- `optics_framework/common/parallel_strategy_executor.py` - Parallel executor
- `optics_framework/common/strategy_dependencies.py` - Dependency management

### Related Documentation

- [Strategy Pattern](../architecture/strategies.md)
- [Execution Architecture](../architecture/execution.md)

### Challenges and Considerations

#### Challenge 1: Resource Conflicts

**Problem**: Multiple strategies accessing the same resource (driver, page source) simultaneously

**Solution**:
- Use asyncio locks for shared resources
- Group strategies by resource type
- Serialize access to shared resources

#### Challenge 2: Backward Compatibility

**Problem**: Existing code expects synchronous strategy execution

**Solution**:
- Maintain sync wrappers for async methods
- Use `run_async()` utility for compatibility
- Gradual migration path

#### Challenge 3: Error Handling

**Problem**: Multiple strategies may fail in parallel

**Solution**:
- Aggregate all exceptions
- Return most informative error
- Log all strategy attempts

#### Challenge 4: Performance Tuning

**Problem**: Too many parallel tasks may degrade performance

**Solution**:
- Configurable max parallel strategies
- Resource-aware task scheduling
- Monitor and optimize based on metrics

#### Challenge 5: Ensuring Single Action Execution

**Problem**: Multiple strategies might try to execute the same keyword/action

**Solution**:
- Implement strict abort mechanism when first strategy succeeds
- Use cancellation tokens to immediately stop remaining strategies
- Only allow the winning strategy to proceed to action execution
- Add validation to prevent duplicate actions
- Implement proper cleanup for aborted strategies

---

## 3. Flexible Driver-Specific Strategies

**Priority**: High | **Difficulty**: Intermediate

### Current State

Strategies are generic and work across all drivers. Driver-specific capabilities (Playwright roles, CSS selectors) are not leveraged, limiting the framework's ability to use the best features of each driver.

### Goal

Enable custom strategies that are specific to individual drivers, allowing each driver to expose its unique capabilities while maintaining the fallback mechanism.

### Key Areas

#### Playwright-Specific Strategies

Playwright provides powerful locator methods that are not currently used:

- `get_by_role()` - Locate by ARIA role (button, textbox, etc.)
- `get_by_test_id()` - Locate by test ID attribute
- `get_by_label()` - Locate by associated label
- `get_by_placeholder()` - Locate by placeholder text
- `get_by_text()` - Locate by visible text
- `get_by_title()` - Locate by title attribute
- `get_by_alt_text()` - Locate by alt text (for images)

**Example**:

```python
# Current: Generic text strategy
element = "Submit Button"

# With Playwright role strategy:
element = "role:button[name='Submit']"
```

#### Selenium/Playwright CSS Strategies

Currently only XPath is supported. CSS selectors are more performant and readable:

- CSS selector support (not just XPath)
- CSS pseudo-selectors (`:first-child`, `:nth-of-type`, etc.)
- CSS attribute selectors (`[data-testid="submit"]`)

#### Appium-Specific Strategies

- UI Automator selectors (Android)
- iOS predicate strings
- Accessibility ID strategies
- Class name strategies

#### Strategy Registration

**Needed**:

- Driver-specific strategy factory
- Strategy priority per driver
- Dynamic strategy discovery mechanism
- Strategy registration API for drivers

### Files to Modify

- `optics_framework/common/strategies.py` - Add driver-specific strategy support
- `optics_framework/engines/drivers/playwright.py` - Add Playwright-specific strategies
- `optics_framework/engines/drivers/selenium.py` - Add CSS selector strategies
- `optics_framework/engines/drivers/appium.py` - Add Appium-specific strategies

### Related Documentation

- [Strategy Pattern](../architecture/strategies.md)
- [Extending the Framework](../architecture/extending.md#creating-new-strategies)

---

## 4. Additional Image Detection Drivers

**Priority**: Medium | **Difficulty**: Intermediate to Advanced

### Current State

Only two image detection models are available:
- **TemplateMatch** (OpenCV-based) - Local template matching
- **RemoteOIR** (Remote service) - Remote object image recognition

### Goal

Add more image detection/vision models to provide users with more options, better accuracy, and different use cases.

### Potential Additions

#### YOLO Integration

Object detection using YOLO (You Only Look Once) models:
- Real-time object detection
- Pre-trained models for UI elements
- Custom model support

**File to Create**: `optics_framework/engines/vision_models/image_models/yolo.py`

#### Cloud Vision Services

- **AWS Rekognition**: Amazon's image recognition service
- **Azure Computer Vision**: Microsoft's vision API
- **Google Cloud Vision**: Google's image analysis API (for object detection, not just OCR)

**Files to Create**:
- `optics_framework/engines/vision_models/image_models/aws_rekognition.py`
- `optics_framework/engines/vision_models/image_models/azure_vision.py`
- `optics_framework/engines/vision_models/image_models/google_vision_detection.py`

#### Deep Learning Models

- **TensorFlow/PyTorch Models**: Support for custom trained models
- Pre-trained models for UI element detection
- Model loading and inference

**File to Create**: `optics_framework/engines/vision_models/image_models/tensorflow_model.py`

#### Advanced OpenCV Algorithms

- **ORB** (Oriented FAST and Rotated BRIEF)
- **BRISK** (Binary Robust Invariant Scalable Keypoints)
- **AKAZE** (Accelerated-KAZE)
- Feature matching alternatives to SIFT

### Implementation Guidelines

All image detection models must implement the `ImageInterface`:

```python
from optics_framework.common.image_interface import ImageInterface

class YourImageDetection(ImageInterface):
    def find_element(self, frame, element, index=0):
        # Implementation
        pass

    def element_exist(self, frame, element):
        # Implementation
        pass

    def assert_elements(self, frame, elements, rule="all"):
        # Implementation
        pass
```

### Files to Create

- `optics_framework/engines/vision_models/image_models/yolo.py`
- `optics_framework/engines/vision_models/image_models/aws_rekognition.py`
- `optics_framework/engines/vision_models/image_models/azure_vision.py`
- `optics_framework/engines/vision_models/image_models/tensorflow_model.py`

### Related Documentation

- [Vision Models](../architecture/engines.md#vision-models)
- [Extending Vision Models](../architecture/extending.md#creating-new-image-detection-models)

---

## 5. Interactive Test Creation

**Priority**: Medium | **Difficulty**: Advanced

### Current State

Tests are created via CSV/YAML files or programmatically. There's no interactive test authoring tool, making it difficult for non-technical users to create tests.

### Goal

Support interactive test creation where users can:
- Record interactions automatically
- Visually select elements on screen
- Generate test steps automatically
- Edit tests in a visual interface
- Preview test execution step-by-step

### Key Features

#### Test Recorder

Record user interactions and generate test steps:
- Capture mouse clicks, keyboard input, scrolls
- Automatically detect element locators
- Generate test steps in CSV/YAML format
- Support for multiple strategies (XPath, text, image)

#### Visual Element Selector

Click on screen to select elements:
- Highlight elements on hover
- Show element information (XPath, text, attributes)
- Suggest multiple locator strategies
- Validate element locators

#### Test Editor

Visual editor for test cases:
- Drag-and-drop test step reordering
- Edit step parameters
- Add/remove test steps
- Preview test structure

#### Element Inspector

Inspect and validate element locators:
- Show all available locators for an element
- Test locator validity
- Suggest better locators
- Show element hierarchy

### Potential Implementation

- **Web-based UI**: React/Vue.js frontend with FastAPI backend
- **Browser Extension**: Chrome/Firefox extension for recording
- **Desktop Application**: Electron app for test authoring
- **CLI Integration**: Extend existing CLI with interactive mode

### Files to Create

- `optics_framework/helper/recorder.py` - Test recording functionality
- `optics_framework/helper/inspector.py` - Element inspection tools
- `optics_framework/api/recorder_api.py` - API endpoints for recorder
- Web UI components (separate repo or `optics_framework/web_ui/`)

### Related Documentation

- [CLI Usage](../usage/CLI_usage.md)
- [User Workflow](../user_workflow.md)

---

## 6. Code Simplification & Unification

**Priority**: Medium | **Difficulty**: Intermediate

### Current State

Analysis reveals several areas with duplicate code and similar patterns that could be unified to reduce maintenance burden and improve code quality.

### Areas Identified

#### Driver Method Patterns

Similar implementations across Appium, Selenium, and Playwright:
- `press_element()`, `enter_text()`, `clear_text()`, `scroll()` have similar patterns
- Common error handling
- Similar event logging

**Files**:

- `optics_framework/engines/drivers/appium.py`
- `optics_framework/engines/drivers/selenium.py`
- `optics_framework/engines/drivers/playwright.py`

**Solution**: Create base driver mixins or helper classes

#### Element Source Patterns

Similar code in find_element, page_source, screenshot classes:
- Common initialization patterns
- Similar error handling
- Common interface implementations

**Files**: `optics_framework/engines/elementsources/*.py`

**Solution**: Create common base class for element source implementations

#### Vision Model Patterns

OCR models share similar structure:
- Common text detection patterns
- Similar configuration handling
- Common error handling

**Files**: `optics_framework/engines/vision_models/ocr_models/*.py`

**Solution**: Enhanced base classes for vision models

#### Strategy Factory Pattern

Current implementation has hard-coded strategy registry:
- Strategy registration is static
- Limited flexibility for custom strategies

**File**: `optics_framework/common/strategies.py`

**Solution**: Make strategy registration more flexible and dynamic

#### Session Management

Complex builder pattern could be simplified:
- Many intermediate steps
- Could use simpler factory pattern in some cases

**File**: `optics_framework/common/optics_builder.py`

**Solution**: Simplify builder pattern or add convenience methods

### Specific Unification Opportunities

1. **Base Driver Mixin**: Create `BaseWebDriverMixin` for common web driver methods
2. **Element Source Base**: Common base class for element source implementations
3. **Vision Model Base**: Enhanced base classes for vision models
4. **Strategy Registry**: Make strategy registration more flexible
5. **Configuration Normalization**: Unify configuration handling across components
6. **Error Handling Utilities**: Standardize error handling patterns

### Files to Create/Modify

**Create**:
- `optics_framework/common/base_driver_mixin.py` - Common driver methods
- `optics_framework/common/base_element_source.py` - Common element source base
- `optics_framework/common/error_utils.py` - Error handling utilities

**Modify**:
- `optics_framework/common/strategies.py` - Flexible strategy registration
- `optics_framework/common/optics_builder.py` - Simplify builder pattern
- All driver implementations to use mixins
- All element source implementations to use base class

### Related Documentation

- [Architecture Overview](../architecture.md)
- [Extending the Framework](../architecture/extending.md)

---

## 7. Test Coverage

**Priority**: High | **Difficulty**: Beginner to Intermediate

### Current State

Current test structure shows limited coverage. Many core components lack comprehensive tests.

### Missing Tests For

- Most engine implementations (drivers, element sources, vision models)
- Strategy manager and location strategies
- Event system and event handlers
- Session management edge cases
- Error handling scenarios
- Factory pattern implementations
- Screenshot streaming
- API layer endpoints
- CLI commands

### Test Files to Create

#### Unit Tests

- `tests/units/engines/drivers/test_ble.py` - BLE driver tests
- `tests/units/engines/drivers/test_playwright.py` - Playwright driver tests
- `tests/units/engines/drivers/test_appium.py` - Appium driver tests (if missing)
- `tests/units/engines/elementsources/test_playwright_find_element.py`
- `tests/units/engines/elementsources/test_playwright_page_source.py`
- `tests/units/engines/elementsources/test_playwright_screenshot.py`
- `tests/units/common/test_strategies.py` - Strategy manager tests
- `tests/units/common/test_session_manager.py` - Session management tests
- `tests/units/common/test_screenshot_stream.py` - Screenshot streaming tests
- `tests/units/api/test_verifier.py` - Verifier API tests
- `tests/units/common/test_factories.py` - Factory pattern tests

#### Integration Tests

- `tests/integration/test_api_endpoints.py` - API endpoint integration tests
- `tests/integration/test_session_lifecycle.py` - Full session lifecycle
- `tests/integration/test_driver_fallback.py` - Driver fallback mechanism

#### Functional Tests

- `tests/functional/test_end_to_end.py` - End-to-end test execution
- `tests/functional/test_vision_detection.py` - Vision detection workflows

### Testing Guidelines

- Use `pytest` for all tests
- Follow existing test patterns in `tests/units/`
- Use fixtures from `tests/units/conftest.py`
- Aim for >80% code coverage
- Test both success and failure scenarios
- Include edge cases and error conditions

### Related Documentation

- [Developer Guide](developer_guide.md#run-tests)
- [Architecture Overview](../architecture.md)

---

## 8. Security Improvements

**Priority**: High | **Difficulty**: Intermediate

### Current State

From `docs/architecture/api_layer.md`, several security improvements are needed for production use.

### CORS Configuration

**Current**: CORS allows all origins (`allow_origins=["*"]`)

**Needed**: Configurable CORS with environment-based restrictions

**File**: `optics_framework/common/expose_api.py`

**Implementation**:
```python
# Allow configuration via environment variables
allowed_origins = os.getenv("OPTICS_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if "*" not in allowed_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Authentication

**Current**: No authentication currently

**Needed**:
- API key authentication
- Bearer token authentication
- Session-based authentication

**Files to Modify**:
- `optics_framework/common/expose_api.py` - Add authentication middleware
- `optics_framework/common/auth.py` - Create authentication module (new file)

### Input Validation

**Current**: Using Pydantic for validation

**Enhancements Needed**:
- Rate limiting per IP/session
- Request size limits
- SQL injection prevention (if adding database support)
- XSS prevention for string inputs

### Related Documentation

- [REST API Layer](../architecture/api_layer.md#security-considerations)
- [Security Policy](https://github.com/mozarkai/optics-framework/blob/main/SECURITY.md)

---

## 9. Feature Development

**Priority**: Medium to High | **Difficulty**: Intermediate to Advanced

### MCP Servicer

**From Roadmap**: Introduce a dedicated service to handle MCP (Model Context Protocol)

**Goal**: Improve scalability and modularity across the framework

**Needed**:
- MCP protocol implementation
- Service architecture design
- Integration with existing framework

### Omniparser Integration

**From Roadmap**: Seamlessly integrate Omniparser for robust and flexible element extraction and location

**Goal**: Enable more flexible element parsing capabilities

**Needed**:
- Omniparser library integration
- Parser configuration
- Strategy integration

### Playwright Integration Enhancements

**From Roadmap**: Add support for Playwright (partially implemented)

**Needed**:
- Complete remaining Playwright methods
- Add driver-specific strategies (see section 2)
- Improve error handling

### Audio Support

**From Roadmap**: Extend the framework to support audio inputs and outputs

**Goal**: Enable testing and verification of voice-based or sound-related interactions

**Needed**:
- Audio input capture
- Audio output verification
- Speech recognition integration
- Audio comparison utilities

### Session Persistence

**Identified in**: `docs/architecture/components.md`

**Current**: Sessions are in-memory only, lost on process termination

**Needed**:
- Session serialization
- Session recovery on restart
- Session state snapshots
- Persistent storage backend

**Note**: This overlaps with section 1 (Stateless API Layer) but focuses on persistence rather than migration.

### Additional Drivers

- iOS driver enhancements
- Additional browser drivers (Edge, Safari)
- IoT device drivers
- Desktop application drivers (Windows, macOS, Linux)

---

## 10. Performance Optimizations

**Priority**: Medium | **Difficulty**: Intermediate

### Areas Identified

#### Screenshot Streaming

**Current**: SSIM computation overhead could be optimized

**Opportunities**:
- Parallel SSIM computation
- Optimize image comparison algorithms
- Cache comparison results
- Use faster similarity metrics where appropriate

**File**: `optics_framework/common/screenshot_stream.py`

#### Strategy Execution

**Current**: Strategies executed sequentially

**Opportunities**:
- Parallel strategy attempts where safe
- Early exit optimization
- Strategy result caching

**File**: `optics_framework/common/strategies.py`

#### Factory Caching

**Current**: Instance caching implemented

**Enhancements**:
- Cache invalidation strategies
- Memory-aware caching
- Cache size limits

**File**: `optics_framework/common/base_factory.py`

#### Event Processing

**Current**: Events processed individually

**Opportunities**:
- Batch events for better throughput
- Async event processing
- Event queue optimization

**Files**:
- `optics_framework/common/events.py`
- `optics_framework/common/eventSDK.py`

#### Image Processing

**Current**: OCR and template matching operations

**Opportunities**:
- Parallel image processing
- Image caching
- Optimize OpenCV operations
- GPU acceleration where available

**Files**:
- `optics_framework/engines/vision_models/image_models/templatematch.py`
- `optics_framework/engines/vision_models/ocr_models/*.py`

### Related Documentation

- [Architecture Decisions](../architecture/decisions.md)
- [Performance Considerations](../architecture/components.md#performance-considerations)

---

## 11. Documentation

**Priority**: Medium | **Difficulty**: Beginner to Intermediate

### Documentation Gaps

#### API Examples

**Current**: API reference exists but lacks practical examples

**Needed**:
- More code examples in API reference
- Common use case examples
- Error handling examples

**Files**: `docs/api_reference.md` (Python API) and `docs/usage/REST_API_usage.md` (REST API)

#### Troubleshooting Guides

**Current**: Limited troubleshooting information

**Needed**:
- Common issues and solutions
- Debugging guides
- Performance troubleshooting
- Error code reference with solutions

#### Performance Tuning

**Needed**: Guide for optimizing framework performance

**Topics**:
- Configuration optimization
- Strategy selection guidance
- Memory management
- Parallel execution tips

#### Migration Guides

**Needed**: When breaking changes occur

**Topics**:
- Version migration guides
- Configuration migration
- API migration guides

#### Video Tutorials

**Needed**: Screen recordings for complex workflows

**Topics**:
- Getting started walkthrough
- Creating your first test
- Advanced features
- Troubleshooting common issues

#### Best Practices

**Needed**: Comprehensive best practices guide

**Topics**:
- Test design patterns
- Element locator best practices
- Configuration management
- CI/CD integration
- Error handling strategies

#### Integration Examples

**Needed**: Examples with CI/CD systems

**Examples**:
- GitHub Actions
- Jenkins
- GitLab CI
- Azure DevOps
- CircleCI

### Related Documentation

- [Contributing Guidelines](contributing_guidelines.md)
- [Developer Guide](developer_guide.md#documentation-changes)

---

## 12. Infrastructure

**Priority**: Low to Medium | **Difficulty**: Intermediate

### CI/CD Enhancements

#### Test Coverage Reporting

**Needed**:

- Automated coverage reports
- Coverage badges
- Coverage trend tracking
- Coverage thresholds

#### Performance Benchmarking

**Needed**:

- Automated performance tests
- Performance regression detection
- Benchmark comparisons
- Performance reports

#### Security Scanning Automation

**Needed**:

- Automated dependency scanning
- Code security scanning
- Container security scanning
- Automated security updates

### Tooling

#### Pre-commit Hooks Enhancements

**Current**: Basic hooks in place

**Enhancements**:

- Additional linting rules
- Documentation checks
- Test coverage checks
- Commit message validation improvements

#### Code Quality Metrics Dashboard

**Needed**:

- Code quality metrics
- Technical debt tracking
- Code complexity metrics
- Maintainability index

#### Dependency Update Automation

**Needed**:

- Automated dependency updates
- Security update prioritization
- Update testing automation
- Changelog generation

### Related Documentation

- [Developer Guide](developer_guide.md)
- [CI/CD Workflows](https://github.com/mozarkai/optics-framework/tree/main/.github/workflows)

---

## Getting Started

### Choose an Area

1. Review the sections above and identify an area that interests you
2. Check the priority and difficulty levels
3. Review related documentation
4. Look at existing code to understand patterns

### Before You Start

1. Read the [Contributing Guidelines](contributing_guidelines.md)
2. Read the [Developer Guide](developer_guide.md)
3. Set up your development environment
4. Familiarize yourself with the codebase

### Making Your Contribution

1. **Fork the Repository**: Create your own fork
2. **Create a Branch**: Use a descriptive branch name
3. **Make Changes**: Follow coding standards and patterns
4. **Write Tests**: Add tests for your changes
5. **Update Documentation**: Update relevant docs
6. **Submit PR**: Create a pull request with clear description

### Need Help?

- Open an issue on GitHub for questions
- Check existing documentation
- Review similar implementations in the codebase
- Ask in pull request comments

### Recognition

Contributors will be:
- Listed in the project's contributors
- Credited in release notes
- Acknowledged in documentation (for significant contributions)

---

## Summary

This document outlines many opportunities to improve the Optics Framework. Whether you're interested in:

- **Architecture**: Stateless API, flexible strategies
- **Features**: New drivers, interactive tools
- **Quality**: Tests, documentation, code simplification
- **Performance**: Optimizations and enhancements
- **Security**: Authentication, validation, hardening

There's something for everyone. We appreciate your interest in contributing and look forward to your contributions!

!!! tip "Start Small"
    If you're new to the project, consider starting with documentation improvements or test coverage. These are great ways to learn the codebase while making valuable contributions.
