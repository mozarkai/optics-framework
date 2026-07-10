# Robot Framework Usage Guide

This guide demonstrates how to use the Optics Framework with Robot Framework for test automation.

## Installation

Install the Optics Framework and Robot Framework:

```bash
pip install optics-framework robotframework
```

## Quick Start

### Basic Example

```robotframework
*** Settings ***
Library    optics_framework.optics.Optics

*** Test Cases ***
Example Test
    Setup    driver_sources=[{"appium": {"enabled": True, "url": "http://localhost:4723"}}]    elements_sources=[{"appium_find_element": {"enabled": True}}]
    Launch App    com.example.app
    Press Element    submit_button
    Enter Text    username_field    testuser
    Validate Element    welcome_message
    Quit
```

## Library Import

### Basic Import

```robotframework
*** Settings ***
Library    optics_framework.optics.Optics
```

### Import with Alias

```robotframework
*** Settings ***
Library    optics_framework.optics.Optics    WITH NAME    Optics
```

## Configuration

### Configuration from Dictionary

```robotframework
*** Settings ***
Library    optics_framework.optics.Optics

*** Variables ***
${CONFIG}    {"driver_sources": [{"appium": {"enabled": true, "url": "http://localhost:4723"}}], "elements_sources": [{"appium_find_element": {"enabled": true}}]}

*** Test Cases ***
Test With Config
    Setup    config=${CONFIG}
    Launch App    com.example.app
    Quit
```

### Configuration from YAML File

```robotframework
*** Settings ***
Library    optics_framework.optics.Optics

*** Test Cases ***
Test With YAML Config
    Setup From File    config.yaml
    Launch App    com.example.app
    Quit
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

### Configuration with Parameters

```robotframework
*** Settings ***
Library    optics_framework.optics.Optics

*** Test Cases ***
Test With Parameters
    Setup    driver_sources=[{"appium": {"enabled": True, "url": "http://localhost:4723"}}]    elements_sources=[{"appium_find_element": {"enabled": True}}]    text_detection=[{"easyocr": {"enabled": True}}]
    Launch App    com.example.app
    Quit
```

## App Management

### Launching Apps

```robotframework
*** Test Cases ***
Launch App Test
    Setup    config=config.yaml
    Launch App    com.example.app
    ${version}=    Get App Version
    Log    App version: ${version}
    ${session_id}=    Get Driver Session Id
    Log    Session ID: ${session_id}
    Quit
```

### App Lifecycle

```robotframework
*** Test Cases ***
App Lifecycle Test
    Setup    config=config.yaml
    Launch App    com.example.app
    Press Element    button
    Close And Terminate App
    Quit
```

## Element Interactions

### Pressing Elements

```robotframework
*** Test Cases ***
Press Element Test
    Setup    config=config.yaml
    Launch App    com.example.app

    # Press element by ID
    Press Element    submit_button

    # Press with fallback
    Press Element    button1    button2    //button[@id='submit']

    # Press by coordinates
    Press By Coordinates    100    200

    # Press by percentage
    Press By Percentage    50    50

    # Press element with index
    Press Element With Index    button    0

    Quit
```

### Text Input

```robotframework
*** Test Cases ***
Text Input Test
    Setup    config=config.yaml
    Launch App    com.example.app

    # Enter text
    Enter Text    username_field    testuser
    Enter Text Direct    input_field    text
    Enter Text Using Keyboard    field    text

    # Enter number
    Enter Number    number_field    123

    # Clear text
    Clear Element Text    input_field

    Quit
```

### Gestures

```robotframework
*** Test Cases ***
Gesture Test
    Setup    config=config.yaml
    Launch App    com.example.app

    # Swipe
    Swipe    100    200    300    400
    # Swipe by percentage (x%, y%, direction, swipe length % — integers 0-100)
    Swipe Percentage    50    50    up    25
    Swipe Until Element Appears    target_element    direction=up
    Swipe From Element    source_element    300    400

    # Scroll
    Scroll    100    200    300    400
    Scroll Until Element Appears    target_element    direction=down
    Scroll From Element    source_element    300    400

    Quit
```

### Other Actions

```robotframework
*** Test Cases ***
Other Actions Test
    Setup    config=config.yaml
    Launch App    com.example.app

    # Press keycode
    Press Keycode    4

    # Get text
    ${text}=    Get Text    element_id
    Log    Element text: ${text}

    # Sleep
    Sleep    2

    # Execute script
    ${result}=    Execute Script    mobile: shell    command=echo hello
    Log    Script result: ${result}

    Quit
```

## Verification

### Element Validation

```robotframework
*** Test Cases ***
Validation Test
    Setup    config=config.yaml
    Launch App    com.example.app

    # Validate single element
    Validate Element    welcome_message

    # Assert presence
    Assert Presence    element1    element2    rule=all
    Assert Presence    element1    element2    rule=any

    # Validate screen
    Validate Screen    header    content    footer

    Quit
```

### Screenshots and Page Source

```robotframework
*** Test Cases ***
Capture Test
    Setup    config=config.yaml
    Launch App    com.example.app

    # Capture screenshot
    ${screenshot}=    Capture Screenshot
    Log    Screenshot: ${screenshot}

    # Capture pagesource
    ${page_source}=    Capture Pagesource
    Log    Page source: ${page_source}

    # Get interactive elements
    ${elements}=    Get Interactive Elements
    Log    Elements: ${elements}

    Quit
```

## Flow Control

### Conditional Execution

```robotframework
*** Test Cases ***
Conditional Test
    Setup    config=config.yaml
    Launch App    com.example.app

    # Condition with variable
    Set Test Variable    ${status}    ready
    Condition    ${status} == 'ready'    proceed_keyword

    # Condition with expression
    Set Test Variable    ${count}    15
    Condition    ${count} > 10    handle_large_count

    Quit
```

### Data Operations

```robotframework
*** Test Cases ***
Data Operations Test
    Setup    config=config.yaml

    # Read data from CSV
    ${data}=    Read Data    data.csv
    Log    Data: ${data}

    # Invoke API
    ${response}=    Invoke API    GET    https://api.example.com/data
    Log    API response: ${response}

    # Read data from list
    ${data}=    Read Data    item1    item2    item3
    Log    Data: ${data}

    Quit
```

### Loops

```robotframework
*** Test Cases ***
Loop Test
    Setup    config=config.yaml
    Launch App    com.example.app

    # Run loop
    Run Loop    5    keyword_name

    # Run loop with condition
    Set Test Variable    ${status}    active
    Run Loop    10    process_item    condition=${status} == 'active'

    Quit
```

### Evaluation

```robotframework
*** Test Cases ***
Evaluation Test
    Setup    config=config.yaml

    # Evaluate expression
    Set Test Variable    ${var1}    10
    Set Test Variable    ${var2}    20
    ${result}=    Evaluate    ${var1} + ${var2}
    Log    Result: ${result}

    # Date evaluation
    ${future_date}=    Date Evaluate    today + 1 day
    ${past_date}=    Date Evaluate    today - 7 days
    Log    Future date: ${future_date}
    Log    Past date: ${past_date}

    Quit
```

## Element Management

### Adding Elements

```robotframework
*** Test Cases ***
Element Management Test
    Setup    config=config.yaml
    Launch App    com.example.app

    # Add element
    Add Element    submit_button    //button[@id='submit']

    # Get element value
    ${element_value}=    Get Element Value    submit_button
    Log    Element value: ${element_value}

    # Use element
    Press Element    submit_button

    # Use element variable
    Press Element    ${submit_button}

    Quit
```

### Element Variables

```robotframework
*** Test Cases ***
Element Variables Test
    Setup    config=config.yaml
    Launch App    com.example.app

    # Add multiple elements
    Add Element    username    //input[@name='user']
    Add Element    password    //input[@name='pass']
    Add Element    submit    //button[@type='submit']

    # Use in sequence
    Enter Text    ${username}    testuser
    Enter Text    ${password}    testpass
    Press Element    ${submit}

    Quit
```

## Test Organization

### Test Suite Structure

```robotframework
*** Settings ***
Documentation    Example test suite using Optics Framework
Library    optics_framework.optics.Optics
Resource    keywords.robot

*** Variables ***
${CONFIG_FILE}    config.yaml

*** Test Cases ***
Test Case 1
    [Documentation]    First test case
    Setup    config=${CONFIG_FILE}
    Launch App    com.example.app
    Press Element    button1
    Validate Element    result1
    Quit

Test Case 2
    [Documentation]    Second test case
    Setup    config=${CONFIG_FILE}
    Launch App    com.example.app
    Press Element    button2
    Validate Element    result2
    Quit
```

### Keywords Resource File

**keywords.robot:**
```robotframework
*** Settings ***
Library    optics_framework.optics.Optics

*** Keywords ***
Login
    [Arguments]    ${username}    ${password}
    Enter Text    username_field    ${username}
    Enter Text    password_field    ${password}
    Press Element    login_button
    Validate Element    welcome_message

Logout
    Press Element    logout_button
    Validate Element    login_screen
```

### Using Resource Keywords

```robotframework
*** Settings ***
Library    optics_framework.optics.Optics
Resource    keywords.robot

*** Test Cases ***
Login Test
    Setup    config=config.yaml
    Launch App    com.example.app
    Login    testuser    testpass
    Logout
    Quit
```

## Complete Example

```robotframework
*** Settings ***
Documentation    Complete example test suite
Library    optics_framework.optics.Optics

*** Variables ***
${CONFIG_FILE}    config.yaml
${APP_PACKAGE}    com.example.app

*** Test Cases ***
Login Flow Test
    [Documentation]    Test complete login flow
    [Tags]    smoke    login

    # Setup
    Setup    config=${CONFIG_FILE}

    # Launch app
    Launch App    ${APP_PACKAGE}

    # Add elements
    Add Element    username    //input[@id='username']
    Add Element    password    //input[@id='password']
    Add Element    login_button    //button[@id='login']

    # Perform login
    Enter Text    ${username}    testuser
    Enter Text    ${password}    testpass
    Press Element    ${login_button}

    # Verify login
    Validate Element    welcome_message

    # Capture screenshot
    ${screenshot}=    Capture Screenshot
    Log    Screenshot saved: ${screenshot}

    # Cleanup
    Quit

Element Interaction Test
    [Documentation]    Test various element interactions
    [Tags]    interaction

    Setup    config=${CONFIG_FILE}
    Launch App    ${APP_PACKAGE}

    # Press elements
    Press Element    button1
    Press By Coordinates    100    200
    Press By Percentage    50    50

    # Text input
    Enter Text    input_field    test text
    Enter Number    number_field    123

    # Gestures
    Swipe    100    200    300    400
    Swipe Percentage    50    50    up    25
    Scroll    100    200    300    400

    # Verification
    Validate Element    result_element
    Assert Presence    element1    element2    rule=all

    Quit
```

## Best Practices

### 1. Configuration Management

- Store configuration in YAML files
- Use variables for reusable values
- Keep configuration separate from test logic

### 2. Test Organization

- Use resource files for reusable keywords
- Organize tests into logical test suites
- Use tags for test categorization

### 3. Element Management

- Store elements using Add Element
- Use descriptive element names
- Leverage element variables

### 4. Error Handling

- Use Robot Framework's built-in error handling
- Add proper documentation to test cases
- Use tags to mark flaky tests

### 5. Test Data

- Use variables for test data
- Store data in external files when needed
- Use data-driven testing for multiple scenarios

## Running Tests

### Basic Execution

```bash
robot tests/login_test.robot
```

### With Tags

```bash
robot --include smoke tests/
robot --exclude flaky tests/
```

### With Variables

```bash
robot -v CONFIG_FILE:config.yaml -v APP_PACKAGE:com.example.app tests/
```

### Generate Reports

```bash
robot --outputdir results tests/
```

## Related Documentation

- [Library Usage](library_usage.md) - Python library usage guide
- [Library Layer Architecture](../architecture/library_layer.md) - Detailed architecture documentation
- [Keyword Usage](keyword_usage.md) - Complete keyword reference
- [Configuration](../configuration.md) - Configuration guide
- [REST API Usage](REST_API_usage.md) - REST API endpoint reference
- [API Reference](../api_reference.md) - Python API documentation
