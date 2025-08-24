# API Reference

The Optics Framework provides a comprehensive set of APIs for test automation across different categories. This reference covers all available APIs, their methods, and usage examples.

## Core API Modules

### ActionKeyword

The `ActionKeyword` class provides high-level functionality for performing user actions on applications, including pressing elements, scrolling, swiping, and text input.

#### Methods

##### Element Interaction

- **`press_element(element, event_name=None)`**  
  Presses/clicks the specified element using self-healing location strategies.
  - `element`: Element identifier (XPath, image template, or OCR text)
  - `event_name`: Optional event name for logging

- **`press_by_coordinates(coor_x, coor_y, repeat=1, event_name=None)`**  
  Presses at specific screen coordinates.
  - `coor_x`, `coor_y`: Screen coordinates
  - `repeat`: Number of times to repeat the action
  - `event_name`: Optional event name

- **`press_by_percentage(percent_x, percent_y, repeat=1, event_name=None)`**  
  Presses at screen position defined by percentage of screen size.
  - `percent_x`, `percent_y`: Percentage coordinates (0-100)
  - `repeat`: Number of times to repeat
  - `event_name`: Optional event name

- **`press_element_with_index(element, index=0, event_name=None)`**  
  Presses an element by its index when multiple matches are found.
  - `element`: Element identifier
  - `index`: Zero-based index of the element to press
  - `event_name`: Optional event name

- **`detect_and_press(element, timeout, event_name=None)`**  
  Waits for element to appear and then presses it.
  - `element`: Element identifier
  - `timeout`: Maximum wait time in seconds
  - `event_name`: Optional event name

##### Form Controls

- **`press_checkbox(element, event_name=None)`**  
  Interacts with checkbox elements.
  - `element`: Checkbox element identifier
  - `event_name`: Optional event name

- **`press_radio_button(element, event_name=None)`**  
  Selects radio button elements.
  - `element`: Radio button element identifier
  - `event_name`: Optional event name

- **`select_dropdown_option(element, option, event_name=None)`**  
  Selects an option from a dropdown menu.
  - `element`: Dropdown element identifier
  - `option`: Option text to select
  - `event_name`: Optional event name

##### Scrolling and Swiping

- **`scroll(direction, event_name=None)`**  
  Performs scroll action in the specified direction.
  - `direction`: Scroll direction ('up', 'down', 'left', 'right')
  - `event_name`: Optional event name

- **`scroll_until_element_appears(element, direction, timeout, event_name=None)`**  
  Scrolls until the specified element becomes visible.
  - `element`: Element to find
  - `direction`: Scroll direction
  - `timeout`: Maximum time to scroll
  - `event_name`: Optional event name

- **`scroll_from_element(element, direction, scroll_length, event_name=None)`**  
  Scrolls from a specific element position.
  - `element`: Starting element
  - `direction`: Scroll direction
  - `scroll_length`: Distance to scroll
  - `event_name`: Optional event name

- **`swipe(coor_x, coor_y, direction='right', swipe_length=50, event_name=None)`**  
  Performs swipe gesture from coordinates.
  - `coor_x`, `coor_y`: Starting coordinates
  - `direction`: Swipe direction
  - `swipe_length`: Swipe distance
  - `event_name`: Optional event name

- **`swipe_until_element_appears(element, direction, timeout, event_name=None)`**  
  Swipes until element becomes visible.
  - `element`: Element to find
  - `direction`: Swipe direction
  - `timeout`: Maximum time to swipe
  - `event_name`: Optional event name

- **`swipe_from_element(element, direction, swipe_length, event_name=None)`**  
  Swipes from a specific element position.
  - `element`: Starting element
  - `direction`: Swipe direction
  - `swipe_length`: Swipe distance
  - `event_name`: Optional event name

##### Text Input

- **`enter_text(element, text, event_name=None)`**  
  Enters text into the specified input element.
  - `element`: Input element identifier
  - `text`: Text to enter
  - `event_name`: Optional event name

- **`enter_text_direct(text, event_name=None)`**  
  Enters text directly without targeting a specific element.
  - `text`: Text to enter
  - `event_name`: Optional event name

### Verifier

The `Verifier` class provides methods to verify elements, screens, and data integrity.

#### Methods

- **`validate_element(element, timeout=10, rule="all", event_name=None)`**  
  Verifies the specified element exists.
  - `element`: Element identifier (Image template, OCR template, or XPath)
  - `timeout`: Time to wait for verification in seconds
  - `rule`: Verification rule ("all" or "any")
  - `event_name`: Optional event name

- **`is_element(element, element_state, timeout, event_name=None)`**  
  Checks if an element exists and matches the specified state.
  - `element`: Element identifier
  - `element_state`: Expected state of the element
  - `timeout`: Maximum wait time
  - `event_name`: Optional event name

- **`assert_presence(element, timeout, rule, event_name=None)`**  
  Asserts that elements are present using specified rules.
  - `element`: Element or list of elements
  - `timeout`: Time to wait
  - `rule`: Rule for assertion ("all" or "any")
  - `event_name`: Optional event name

### FlowControl

The `FlowControl` class manages control flow operations including loops, conditions, and data handling.

#### Methods

- **`condition(*args)`**  
  Evaluates multiple conditions and executes corresponding modules if conditions are true.
  - `*args`: Condition parameters

- **`evaluate(param1, param2)`**  
  Evaluates mathematical or logical expressions and stores results in variables.
  - `param1`: First parameter for evaluation
  - `param2`: Second parameter for evaluation

- **`read_data(input_element, file_path, index=None)`**  
  Reads data from CSV files, API URLs, or lists and assigns to variables.
  - `input_element`: Variable to store the data
  - `file_path`: Path to data source
  - `index`: Optional index for specific data item

- **`run_loop(target, *args)`**  
  Runs loops either by count or by iterating over variable-value pairs.
  - `target`: Loop target (module or count)
  - `*args`: Loop parameters

### AppManagement

The `AppManagement` class provides functionality for launching, terminating, and modifying app settings.

#### Methods

- **`launch_app(event_name=None)`**  
  Launches the specified application.
  - `event_name`: Optional event name

- **`start_appium_session(event_name=None)`**  
  Starts an Appium session.
  - `event_name`: Optional event name

- **`start_other_app(package_name, event_name=None)`**  
  Starts a different application by package name.
  - `package_name`: Application package identifier
  - `event_name`: Optional event name

- **`close_app(event_name=None)`**  
  Closes the current application.
  - `event_name`: Optional event name

- **`terminate_app(event_name=None)`**  
  Terminates the application forcefully.
  - `event_name`: Optional event name

## REST API Endpoints

The Optics Framework also provides a REST API for remote automation control through the FastAPI server.

### Session Management

#### POST `/v1/sessions/start`
Creates a new automation session.

**Request Body:**
```json
{
  "driver_sources": ["appium"],
  "elements_sources": ["appium_find_element"],
  "text_detection": ["easyocr"],
  "image_detection": ["templatematch"],
  "project_path": "/path/to/project",
  "appium_url": "http://localhost:4723",
  "appium_config": {
    "platformName": "Android",
    "deviceName": "emulator-5554"
  }
}
```

**Response:**
```json
{
  "session_id": "uuid-string",
  "status": "created"
}
```

#### DELETE `/v1/sessions/{session_id}/stop`
Terminates an automation session.

**Response:**
```json
{
  "status": "terminated"
}
```

### Action Execution

#### POST `/v1/sessions/{session_id}/action`
Executes a keyword action within a session.

**Request Body:**
```json
{
  "mode": "keyword",
  "keyword": "press_element",
  "params": ["element_identifier"]
}
```

**Response:**
```json
{
  "execution_id": "uuid-string",
  "status": "SUCCESS",
  "data": {"result": "action_result"}
}
```

### Data Retrieval

#### GET `/session/{session_id}/screenshot`
Captures a screenshot of the current screen.

#### GET `/session/{session_id}/elements`
Retrieves interactive elements from the current screen.

#### GET `/session/{session_id}/source`
Gets the page source of the current application state.

#### GET `/session/{session_id}/screen_elements`
Captures screenshot and gets all screen elements in one call.

### Event Streaming

#### GET `/v1/sessions/{session_id}/events`
Streams real-time events from the automation session using Server-Sent Events (SSE).

## Usage Examples

### Basic Element Interaction
```python
from optics_framework.api.action_keyword import ActionKeyword

# Initialize action keyword instance
action = ActionKeyword(builder)

# Press an element
action.press_element("login_button")

# Enter text in a field
action.enter_text("username_field", "my_username")

# Scroll until element appears
action.scroll_until_element_appears("submit_button", "down", 30)
```

### Verification
```python
from optics_framework.api.verifier import Verifier

# Initialize verifier
verifier = Verifier(builder)

# Validate element presence
verifier.validate_element("welcome_message", timeout=15)

# Check element state
verifier.is_element("checkbox", "checked", 10)
```

### Flow Control
```python
from optics_framework.api.flow_control import FlowControl

# Initialize flow control
flow = FlowControl(runner, modules)

# Read data from CSV
flow.read_data("user_data", "test_data.csv", 0)

# Run conditional logic
flow.condition("${user_data.active}", "equals", "true", "login_module")
```

### App Management
```python
from optics_framework.api.app_management import AppManagement

# Initialize app management
app_mgmt = AppManagement(builder)

# Launch application
app_mgmt.launch_app("test_start")

# Close application
app_mgmt.close_app("test_end")
```

## Self-Healing Features

The ActionKeyword class includes self-healing capabilities through the `@with_self_healing` decorator. This feature:

- Automatically tries multiple location strategies for elements
- Captures screenshots for debugging when actions fail
- Provides fallback mechanisms when primary element location fails
- Logs detailed error information for troubleshooting

## Error Handling

All API methods include comprehensive error handling and logging:

- Invalid element identifiers raise `ValueError` exceptions
- Timeout scenarios are logged with appropriate error messages
- Failed actions trigger fallback strategies when available
- All operations are logged for debugging and audit purposes

## Integration Notes

- The framework supports multiple driver sources (Appium, screenshot-based)
- Element location strategies include XPath, image templates, and OCR text
- All methods support optional event naming for tracking and analytics
- The API is designed for both programmatic use and CSV-based test definitions