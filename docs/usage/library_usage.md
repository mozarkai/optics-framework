# Library Usage Guide

This guide demonstrates how to use the Optics Framework as a Python library for programmatic test automation.

## Installation

Install the Optics Framework:

```bash
pip install optics-framework
```

## Quick Start

### Basic Example

```python
from optics_framework import Optics

# Initialize Optics
optics = Optics()

# Configure with driver and element sources
optics.setup(
    driver_sources=[{"appium": {"enabled": True, "url": "http://localhost:4723"}}],
    elements_sources=[{"appium_find_element": {"enabled": True}}]
)

# Launch app
optics.launch_app("com.example.app")

# Interact with elements
optics.press_element("submit_button")
optics.enter_text("username_field", "testuser")

# Verify elements
optics.validate_element("welcome_message")

# Cleanup
optics.quit()
```

## Configuration

### Configuration from Dictionary

```python
from optics_framework import Optics

config = {
    "driver_sources": [
        {
            "appium": {
                "enabled": True,
                "url": "http://localhost:4723",
                "capabilities": {
                    "platformName": "Android",
                    "deviceName": "emulator-5554"
                }
            }
        }
    ],
    "elements_sources": [
        {"appium_find_element": {"enabled": True}}
    ],
    "text_detection": [
        {"easyocr": {"enabled": True}}
    ],
    "image_detection": [
        {"templatematch": {"enabled": True}}
    ],
    "project_path": "/path/to/project"
}

optics = Optics(config=config)
```

### Configuration from YAML File

```python
from optics_framework import Optics

optics = Optics()
optics.setup_from_file("config.yaml")
```

**config.yaml:**
```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723"
      capabilities:
        platformName: "Android"
        deviceName: "emulator-5554"

elements_sources:
  - appium_find_element:
      enabled: true

text_detection:
  - easyocr:
      enabled: true

image_detection:
  - templatematch:
      enabled: true

project_path: "/path/to/project"
```

### Configuration from YAML String

```python
from optics_framework import Optics

yaml_config = """
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723"
elements_sources:
  - appium_find_element:
      enabled: true
"""

optics = Optics()
optics.setup(config=yaml_config)
```

## App Management

### Launching Apps

```python
# Launch app by package name
optics.launch_app("com.example.app")

# Launch other app
optics.launch_other_app("com.other.app")

# Get app version
version = optics.get_app_version()
print(f"App version: {version}")

# Get driver session ID
session_id = optics.get_driver_session_id()
print(f"Session ID: {session_id}")
```

### App Lifecycle

```python
# Launch app
optics.launch_app("com.example.app")

# Perform test operations
optics.press_element("button")

# Close and terminate app
optics.close_and_terminate_app()

# Or force terminate
optics.force_terminate_app()
```

## Element Interactions

### Pressing Elements

```python
# Press element by ID
optics.press_element("submit_button")

# Press with fallback (tries multiple identifiers)
optics.press_element(["button1", "button2", "//button[@id='submit']"])

# Press by coordinates
optics.press_by_coordinates(100, 200)

# Press by percentage (50% x, 50% y)
optics.press_by_percentage(50, 50)

# Press element with index (if multiple matches)
optics.press_element_with_index("button", index=0)
```

### Text Input

```python
# Enter text in element
optics.enter_text("username_field", "testuser")

# Enter text directly (without element)
optics.enter_text_direct("input_field", "text")

# Enter text using keyboard
optics.enter_text_using_keyboard("field", "text")

# Enter number
optics.enter_number("number_field", 123)

# Clear element text
optics.clear_element_text("input_field")
```

### Gestures

```python
# Swipe from (x1, y1) to (x2, y2)
optics.swipe(100, 200, 300, 400)

# Swipe by percentage (x%, y%, direction, swipe length % — all integers 0-100)
optics.swipe_percentage(50, 50, "up", 25)

# Swipe until element appears
optics.swipe_until_element_appears("target_element", direction="up")

# Swipe from element
optics.swipe_from_element("source_element", 300, 400)

# Scroll
optics.scroll(100, 200, 300, 400)

# Scroll until element appears
optics.scroll_until_element_appears("target_element", direction="down")

# Scroll from element
optics.scroll_from_element("source_element", 300, 400)
```

### Other Actions

```python
# Press keycode
optics.press_keycode(4)  # Back button

# Get text from element
text = optics.get_text("element_id")
print(f"Element text: {text}")

# Sleep
optics.sleep(2)  # Sleep for 2 seconds

# Execute script
result = optics.execute_script("mobile: shell", {"command": "echo hello"})
```

## Verification

### Element Validation

```python
# Validate single element exists
optics.validate_element("welcome_message")

# Assert presence with rules
optics.assert_presence(["element1", "element2"], rule="all")  # All must be present
optics.assert_presence(["element1", "element2"], rule="any")   # Any must be present

# Validate screen (multiple elements)
optics.validate_screen(["header", "content", "footer"])
```

### Screenshots and Page Source

```python
# Capture screenshot
screenshot = optics.capture_screenshot()
print(f"Screenshot: {screenshot}")

# Capture pagesource
page_source = optics.capture_pagesource()
print(f"Page source: {page_source}")

# Get interactive elements
elements = optics.get_interactive_elements()
for element in elements:
    print(f"Element: {element}")
```

## Flow Control

### Conditional Execution

```python
# Condition with variable
optics.condition("${status} == 'ready'", "proceed_keyword")

# Condition with expression
optics.condition("${count} > 10", "handle_large_count")
```

### Data Operations

```python
# Read data from CSV (element, source, optional query)
data = optics.read_data("${data}", "data.csv")
print(f"Data: {data}")

# Read with query: filter and column selection
data = optics.read_data("${users}", "users.csv", "status=='active';select=username,email")

# Read from a 2D list: first row = headers, following rows = data
data = optics.read_data("${items}", [["id", "name"], ["1", "a"], ["2", "b"]])
print(f"Data: {data}")

# Invoke API
response = optics.invoke_api("GET", "https://api.example.com/data")
print(f"API response: {response}")
```

### Loops

```python
# Run loop 5 times
optics.run_loop(5, "keyword_name")

# Run loop with condition
optics.run_loop(10, "process_item", condition="${status} == 'active'")
```

### Evaluation

```python
# Evaluate expression
result = optics.evaluate("${var1} + ${var2}")
print(f"Result: {result}")

# Date evaluation
future_date = optics.date_evaluate("today + 1 day")
past_date = optics.date_evaluate("today - 7 days")
```

## Element Management

### Adding Elements

```python
# Add element
optics.add_element("submit_button", "//button[@id='submit']")

# Get element value
element_value = optics.get_element_value("submit_button")
print(f"Element value: {element_value}")

# Use element in keywords
optics.press_element("submit_button")  # Uses stored element

# Use element variable
optics.press_element("${submit_button}")  # Resolves variable
```

### Element Variables

```python
# Add multiple elements
optics.add_element("username", "//input[@name='user']")
optics.add_element("password", "//input[@name='pass']")
optics.add_element("submit", "//button[@type='submit']")

# Use in sequence
optics.enter_text("${username}", "testuser")
optics.enter_text("${password}", "testpass")
optics.press_element("${submit}")
```

## Session Management

### Basic Session Lifecycle

```python
from optics_framework import Optics

# Create and configure session
optics = Optics()
optics.setup(config=config)

# Use session
optics.launch_app("com.example.app")
optics.press_element("button")

# Cleanup session
optics.quit()
```

### Context Manager Pattern

```python
from contextlib import contextmanager
from optics_framework import Optics

@contextmanager
def optics_session(config):
    """Context manager for automatic session cleanup."""
    optics = Optics()
    optics.setup(config=config)
    try:
        yield optics
    finally:
        optics.quit()

# Usage
config = {...}
with optics_session(config) as optics:
    optics.launch_app("com.example.app")
    optics.press_element("button")
    # Session automatically cleaned up on exit
```

### Multiple Sessions

```python
from optics_framework import Optics

# Create multiple instances for different sessions
optics1 = Optics(config=config1)
optics2 = Optics(config=config2)

# Each instance has its own session
optics1.launch_app("app1")
optics2.launch_app("app2")

# Cleanup both
optics1.quit()
optics2.quit()
```

## Error Handling

### Basic Error Handling

```python
from optics_framework import Optics
from optics_framework.common.error import OpticsError

optics = Optics()
optics.setup(config=config)

try:
    optics.press_element("nonexistent_element")
except OpticsError as e:
    print(f"Error code: {e.code}")
    print(f"Error message: {e.message}")
    print(f"Error details: {e.details}")
```

### Handling Specific Errors

```python
from optics_framework.common.error import OpticsError, Code

try:
    optics.press_element("button")
except OpticsError as e:
    if e.code == Code.E0101:
        print("Driver not initialized")
    elif e.code == Code.E0201:
        print("Element not found")
    else:
        print(f"Other error: {e.message}")
```

## Complete Example

```python
from optics_framework import Optics
from optics_framework.common.error import OpticsError

def test_login_flow():
    """Complete test example."""
    config = {
        "driver_sources": [
            {"appium": {"enabled": True, "url": "http://localhost:4723"}}
        ],
        "elements_sources": [
            {"appium_find_element": {"enabled": True}}
        ],
        "project_path": "/path/to/project"
    }

    optics = Optics()

    try:
        # Setup
        optics.setup(config=config)

        # Launch app
        optics.launch_app("com.example.app")

        # Add elements
        optics.add_element("username", "//input[@id='username']")
        optics.add_element("password", "//input[@id='password']")
        optics.add_element("login_button", "//button[@id='login']")

        # Perform login
        optics.enter_text("${username}", "testuser")
        optics.enter_text("${password}", "testpass")
        optics.press_element("${login_button}")

        # Verify login
        optics.validate_element("welcome_message")

        # Capture screenshot
        screenshot = optics.capture_screenshot()
        print(f"Screenshot saved: {screenshot}")

    except OpticsError as e:
        print(f"Test failed: {e.message}")
        raise
    finally:
        # Cleanup
        optics.quit()

if __name__ == "__main__":
    test_login_flow()
```

## Best Practices

### 1. Configuration Management

- Store configuration in YAML files for complex setups
- Use environment variables for sensitive data
- Keep configuration separate from test logic

### 2. Resource Cleanup

- Always call `quit()` when done
- Use context managers for automatic cleanup
- Handle exceptions to ensure cleanup

### 3. Element Management

- Store frequently used elements
- Use descriptive element names
- Leverage fallback parameters for resilience

### 4. Error Handling

- Wrap operations in try/except blocks
- Handle `OpticsError` specifically
- Log errors for debugging

### 5. Code Organization

- Separate configuration from test logic
- Create helper functions for common operations
- Organize tests into logical modules

## Related Documentation

- [Library Layer Architecture](../architecture/library_layer.md) - Detailed architecture documentation
- [Keyword Usage](keyword_usage.md) - Complete keyword reference
- [Configuration](../configuration.md) - Configuration guide
- [REST API Usage](REST_API_usage.md) - REST API endpoint reference
- [API Reference](../api_reference.md) - Python API documentation
