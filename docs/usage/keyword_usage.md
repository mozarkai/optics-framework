# Keyword Usage

This document outlines the available keywords for the Optics Framework, which can be used in the `test_modules.csv` file to define test steps. Keywords are derived from the framework's Python API, with method names converted to a space-separated format (e.g., `press_element` becomes `Press Element`). Each keyword corresponds to a specific action, verification, or control flow operation. Below, each keyword includes detailed parameter explanations to guide their usage.

### Keyword data sources

- **Element-based keywords** (Press Element, Assert Elements, Type Text, Validate Element, etc.) use **element data** from element files (CSV with columns such as `element_name` / `element_id`, or YAML with an `elements` key). Element names in test steps refer to these definitions.
- **Invoke API** uses **API definition data** from **API YAML files** (top-level `api` or `apis`). It does not use element files. You reference an API by the identifier `collection.api_name`.

## Action Keywords

These keywords handle interactions with the application, such as clicking, swiping, and text input.

### Press Element

Presses a specified element on the screen.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The target to press. Can be:<br/>• *Text*: A string to locate via text detection (e.g., "Home", `${Home_text}`)<br/>• *XPath*: An XPath expression for Appium/Selenium (e.g., `//android.widget.Button[@resource-id="id"]`)<br/>• *Image*: A filename of an image in `input_templates/` (e.g., "home.png", `${Home_image}`) | - |
| `repeat` | Optional | Number of times to press the element (integer) | `1` |
| `offset_x` | Optional | Horizontal offset in pixels from the element's center (integer) | `0` |
| `offset_y` | Optional | Vertical offset in pixels from the element's center (integer) | `0` |
| `index` | Optional | Index of the element if multiple matches are found (integer, 0-based) | `0` |
| `aoi_x` | Optional | X percentage of Area of Interest top-left corner (0-100, float) | `0` |
| `aoi_y` | Optional | Y percentage of Area of Interest top-left corner (0-100, float) | `0` |
| `aoi_width` | Optional | Width percentage of Area of Interest (0-100, float) | `100` |
| `aoi_height` | Optional | Height percentage of Area of Interest (0-100, float) | `100` |
| `event_name` | Optional | A string identifier for logging or triggering events (e.g., "click_home") | - |

**Example:**

```csv
Press Element,${Subscriptions_text},2,10,20,0,0,0,100,100,click_event
```

### Press By Percentage

Presses at percentage-based coordinates on the screen.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `percent_x` | Required | X-coordinate as a percentage of screen width (float, 0.0 to 1.0, e.g., 0.5 for 50%) | - |
| `percent_y` | Required | Y-coordinate as a percentage of screen height (float, 0.0 to 1.0) | - |
| `repeat` | Optional | Number of times to press (integer) | `1` |
| `event_name` | Optional | A string identifier for the press event (e.g., "center_press") | - |

**Example:**

```csv
Press By Percentage,0.5,0.5,,press_center
```

### Press By Coordinates

Presses at absolute coordinates on the screen.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `coor_x` | Required | X-coordinate in pixels (integer, e.g., 500) | - |
| `coor_y` | Required | Y-coordinate in pixels (integer, e.g., 800) | - |
| `repeat` | Optional | Number of times to press (integer) | `1` |
| `event_name` | Optional | A string identifier for the press event (e.g., "tap_event") | - |

**Example:**

```csv
Press By Coordinates,500,800,,tap_event
```

### Detect And Press

Detects a specified element and presses it if found.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The element to detect and press (Image template, OCR template, or XPath) | - |
| `timeout` | Optional | Timeout for the detection operation in seconds (integer) | `30` |
| `event_name` | Optional | A string identifier for the press event | - |

**Example:**

```csv
Detect And Press,login_button.png,30,detect_login
```

### Select Dropdown Option

Opens a dropdown and selects one of its options, scrolling through the list if needed.

Presses the dropdown element to open it. If `option` is already visible, presses it directly. Otherwise, diffs the interactive elements captured before and after opening to find the dropdown's scrollable list container, then scrolls within that container until `option` appears or the list stops changing. Raises an error (`E0201`) if `option` is never found within `timeout` seconds, preventing a silent mis-selection. Falls back to a single-screen check (no scrolling) when page source, or a list-like container, isn't available.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The dropdown element (Image template, OCR template, or XPath) | - |
| `option` | Required | The option to select (visible label text, OCR template, or XPath) | - |
| `timeout` | Optional | Seconds to keep scrolling through the dropdown list before failing | `30` |
| `event_name` | Optional | A string identifier for the selection event | - |

**Example:**

```csv
Select Dropdown Option,${country_dropdown},India,30,country_selected
```

### Swipe

Performs a swipe action in a specified direction from given coordinates.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `coor_x` | Required | X coordinate of the swipe starting point (integer) | - |
| `coor_y` | Required | Y coordinate of the swipe starting point (integer) | - |
| `direction` | Optional | The swipe direction: `up`, `down`, `left`, or `right` | `right` |
| `swipe_length` | Optional | The length of the swipe in pixels (integer) | `50` |
| `event_name` | Optional | A string identifier for the swipe event | - |

**Example:**

```csv
Swipe,500,800,down,100,swipe_down
```

### Swipe By Percentage

Performs a swipe action in a specified direction by percentage of the screen (0-100).

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `percent_x` | Required | X position of the swipe start as a percentage of screen width (integer, 0-100) | - |
| `percent_y` | Required | Y position of the swipe start as a percentage of screen height (integer, 0-100) | - |
| `direction` | Optional | The swipe direction: `up`, `down`, `left`, or `right` | `right` |
| `swipe_length` | Optional | Length of the swipe as a percentage of the screen (integer, 0-100) | `50` |
| `event_name` | Optional | A string identifier for the swipe event | - |

**Example:**

```csv
Swipe By Percentage,50,50,up,25,swipe_up
```

### Swipe Until Element Appears

Swipes in a specified direction until an element appears. Raises an error if the element does not appear within the timeout.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The target element to find (Image template, OCR template, or XPath) | - |
| `direction` | Required | The swipe direction: `up`, `down`, `left`, or `right` | - |
| `timeout` | Required | Maximum time in seconds to keep swiping before raising an error (integer) | - |
| `event_name` | Optional | A string identifier for the swipe event | - |

**Example:**

```csv
Swipe Until Element Appears,next_button.png,down,30,find_next
```

### Swipe From Element

Performs a swipe action starting from a specified element.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The element to swipe from (Image template, OCR template, or XPath) | - |
| `direction` | Required | The swipe direction: `up`, `down`, `left`, or `right` | - |
| `swipe_length` | Required | The length of the swipe in pixels (integer) | - |
| `aoi_x` | Optional | X percentage of Area of Interest top-left corner (0-100, float) | `0` |
| `aoi_y` | Optional | Y percentage of Area of Interest top-left corner (0-100, float) | `0` |
| `aoi_width` | Optional | Width percentage of Area of Interest (0-100, float) | `100` |
| `aoi_height` | Optional | Height percentage of Area of Interest (0-100, float) | `100` |
| `event_name` | Optional | A string identifier for the swipe event | - |

**Example:**

```csv
Swipe From Element,slider.png,right,50,0,0,100,100,swipe_slider
```

### Scroll

Performs a scroll action in a specified direction.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `direction` | Required | The scroll direction: `up`, `down`, `left`, or `right` | - |
| `event_name` | Optional | A string identifier for the scroll event | - |

**Example:**

```csv
Scroll,down,scroll_down
```

### Scroll Until Element Appears

Scrolls in a specified direction until an element appears. Raises an error if the element does not appear within the timeout.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The target element to find (Image template, OCR template, or XPath) | - |
| `direction` | Required | The scroll direction: `up`, `down`, `left`, or `right` | - |
| `timeout` | Required | Maximum time in seconds to keep scrolling before raising an error (integer) | - |
| `event_name` | Optional | A string identifier for the scroll event | - |

**Example:**

```csv
Scroll Until Element Appears,footer.png,down,30,find_footer
```

### Scroll From Element

Performs a scroll action starting from a specified element.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The element to scroll from (Image template, OCR template, or XPath) | - |
| `direction` | Required | The scroll direction: `up`, `down`, `left`, or `right` | - |
| `scroll_length` | Required | The length of the scroll in pixels (integer) | - |
| `aoi_x` | Optional | X percentage of Area of Interest top-left corner (0-100, float) | `0` |
| `aoi_y` | Optional | Y percentage of Area of Interest top-left corner (0-100, float) | `0` |
| `aoi_width` | Optional | Width percentage of Area of Interest (0-100, float) | `100` |
| `aoi_height` | Optional | Height percentage of Area of Interest (0-100, float) | `100` |
| `event_name` | Optional | A string identifier for the scroll event | - |

**Example:**

```csv
Scroll From Element,scrollable_area.png,down,100,0,0,100,100,scroll_area
```

### Enter Text

Enters text into a specified element.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The target element (Image template, OCR template, or XPath) | - |
| `text` | Required | The text to be entered | - |
| `aoi_x` | Optional | X percentage of Area of Interest top-left corner (0-100, float) | `0` |
| `aoi_y` | Optional | Y percentage of Area of Interest top-left corner (0-100, float) | `0` |
| `aoi_width` | Optional | Width percentage of Area of Interest (0-100, float) | `100` |
| `aoi_height` | Optional | Height percentage of Area of Interest (0-100, float) | `100` |
| `event_name` | Optional | A string identifier for the input event | - |

**Example:**

```csv
Enter Text,username_field.png,myusername,0,0,100,100,enter_username
```

### Enter Text Direct

Enters text directly using the keyboard without targeting a specific element.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `text` | Required | The text to be entered | - |
| `event_name` | Optional | A string identifier for the input event | - |

**Example:**

```csv
Enter Text Direct,Hello World,type_text
```

### Enter Text Using Keyboard

Enters text or presses a special key using the keyboard. Supports special keys like `<enter>`, `<tab>`, etc.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `text_input` | Required | The text or special key identifier to send (e.g., "hello" or "<enter>") | - |
| `event_name` | Optional | A string identifier for the keyboard event | - |

**Example:**

```csv
Enter Text Using Keyboard,<enter>,press_enter
```

### Enter Number

Enters a specified number into an element.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The target element (Image template, OCR template, or XPath) | - |
| `number` | Required | The number to be entered | - |
| `aoi_x` | Optional | X percentage of Area of Interest top-left corner (0-100, float) | `0` |
| `aoi_y` | Optional | Y percentage of Area of Interest top-left corner (0-100, float) | `0` |
| `aoi_width` | Optional | Width percentage of Area of Interest (0-100, float) | `100` |
| `aoi_height` | Optional | Height percentage of Area of Interest (0-100, float) | `100` |
| `event_name` | Optional | A string identifier for the input event | - |

**Example:**

```csv
Enter Number,phone_field.png,1234567890,0,0,100,100,enter_phone
```

### Press Keycode

Presses a specified keycode (useful for Android keycodes like BACK, HOME, etc.).

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `keycode` | Required | The keycode to be pressed (e.g., "4" for BACK, "3" for HOME) | - |
| `event_name` | Optional | A string identifier for the keycode event | - |

**Example:**

```csv
Press Keycode,4,press_back
```

### Clear Element Text

Clears text from a specified element.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The target element (Image template, OCR template, or XPath) | - |
| `aoi_x` | Optional | X percentage of Area of Interest top-left corner (0-100, float) | `0` |
| `aoi_y` | Optional | Y percentage of Area of Interest top-left corner (0-100, float) | `0` |
| `aoi_width` | Optional | Width percentage of Area of Interest (0-100, float) | `100` |
| `aoi_height` | Optional | Height percentage of Area of Interest (0-100, float) | `100` |
| `event_name` | Optional | A string identifier for the clear event | - |

**Example:**

```csv
Clear Element Text,text_field.png,0,0,100,100,clear_field
```

### Get Text

Gets the text from a specified element. Currently supports XPath and Text-based elements with Appium.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The target element (Text or XPath) | - |

**Example:**

```csv
Get Text,//android.widget.TextView[@resource-id="title"]
```

**Note:** This keyword returns text but does not store it in CSV format. Use with flow control keywords to store results.

### Sleep

Sleeps for a specified duration.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `duration` | Required | The duration of the sleep in seconds (integer) | - |

**Example:**

```csv
Sleep,5
```

### Execute Script

Executes JavaScript/script in the current context. Supports both plain script strings and JSON format with arguments.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `script_or_json` | Required | The JavaScript code/script command, or a JSON string containing `{"script": "...", "args": {...}}` or `{"script": "..."}` | - |
| `event_name` | Optional | A string identifier for the script execution event | - |

**Example:**

```csv
Execute Script,{"script": "mobile:pressKey", "args": {"keycode": 3}},execute_back
```

## Verification Keywords

These keywords handle verification and validation operations.

### Assert Equality

Compares two values for equality. Both values are converted to strings and stripped of leading/trailing whitespace before comparison. Returns `true` if equal, `false` otherwise. Either value can be a `${variable}` reference.

**Usage note:** `Evaluate` writes its result directly into `session.elements`, so `${var}` from `Evaluate` can be passed straight into `Assert Equality`.

**No expression evaluation:** `Assert Equality` only resolves `${var}` references to their stored string value, then compares the two resulting strings as-is — it does not evaluate anything as Python. Quote characters you type are compared literally, not stripped. So a stored value of `9.99` must be compared against the bare literal `9.99` (not `'9.99'`).

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `output` | Required | The actual value to check — typically a `${variable}` already populated by `Evaluate`, or a value read back from a keyword like `Get Text` | - |
| `expression` | Required | The expected value | - |
| `event_name` | Optional | A string identifier for the event | - |

**Example (direct literal comparison):**

```csv
Assert Equality,${price_text},9.99,price_verified
```

**Example (chained after `Evaluate`):**

```csv
Evaluate,${price},'9.99'
Assert Equality,${price},9.99
```

**Example (chained after `Get Text`):**

`Get Text` doesn't store its result automatically, so capture the returned text and compare it directly:

```csv
Get Text,//android.widget.TextView[@resource-id="price_label"]
Assert Equality,9.99,9.99
```

Here, the first `9.99` in `Assert Equality` is the text `Get Text` returned from the label. You paste in whatever value it gave back, not a fresh literal.

### Validate Element

Verifies the specified element is present on the screen.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The element to be verified (Image template, OCR template, or XPath) | - |
| `timeout` | Optional | The time to wait for verification in seconds (integer) | `10` |
| `rule` | Optional | The rule used for verification: `all` or `any` | `all` |
| `event_name` | Optional | The name of the event associated with the verification | - |

**Example:**

```csv
Validate Element,login_button.png,10,any,verify_login
```

### Is Element

Checks if an element is in a given state: `visible`, `invisible`, `enabled`, or `disabled`. Raises an error if the element's actual state does not match the expected state.

- **visible / invisible** — asserts presence or absence of the element on screen within the timeout.
- **enabled / disabled** — locates the element and checks its interactive state via the accessibility tree. Coordinate-based results (e.g. from OCR) are not supported for enabled/disabled checks.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `element` | Required | The element to check (Image template, OCR template, or XPath) | - |
| `element_state` | Required | Expected state: `visible`, `invisible`, `enabled`, or `disabled` | - |
| `timeout` | Required | Time to wait for the element in seconds (integer) | - |
| `event_name` | Optional | A string identifier for the event | - |

**Example:**

```csv
Is Element,login_button.png,visible,10,check_login_visible
Is Element,//android.widget.Button[@text="Submit"],enabled,5,check_submit_enabled
Is Element,loading_spinner.png,invisible,15,wait_for_load
```

### Assert Presence

Asserts the presence of elements. Can check multiple elements with pipe separator (`|`).

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `elements` | Required | Comma-separated or pipe-separated string of elements to check (e.g., "button1.png\|button2.png") | - |
| `timeout_str` | Optional | The time to wait for the elements in seconds (integer) | `30` |
| `rule` | Optional | The rule for verification: `any` (at least one) or `all` (all must be present) | `any` |
| `event_name` | Optional | The name of the event associated with the assertion | - |

**Example:**

```csv
Assert Presence,login_button.png|signup_button.png,30,any,check_buttons
```

### Assert Visibility

Asserts that elements are actually rendered/visible on screen right now — distinct from `Assert Presence`, which reports found even for elements that exist in the page/DOM but are off-screen (e.g. not yet scrolled into view). Can check multiple elements with pipe separator (`|`).

**Driver support:** Appium (Android and iOS). Selenium and Playwright do not yet implement this keyword and raise an error if used — support is pending fixes to unrelated pre-existing bugs in those drivers.

**Android note:** elements scrolled out of view are omitted entirely from the page source by default on Android, so this keyword can't tell "off-screen" from "doesn't exist" for them unless `allowInvisibleElements: true` is set under `appium:settings` — see [Appium configuration](../configuration.md#driver-sources).

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `elements` | Required | Comma-separated or pipe-separated string of elements to check (e.g., "button1.png\|button2.png") | - |
| `timeout_str` | Optional | The time to wait for the elements to become visible, in seconds (integer) | `30` |
| `rule` | Optional | The rule for verification: `any` (at least one) or `all` (all must be present) | `any` |
| `event_name` | Optional | The name of the event associated with the assertion | - |

**Example:**

```csv
Assert Visibility,login_button.png|signup_button.png,30,any,check_buttons_visible
```

### Validate Screen

Verifies the specified screen by checking element presence. Similar to `Assert Presence` but does not fail if elements are not found.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `elements` | Required | Comma-separated or pipe-separated string of elements to verify | - |
| `timeout` | Optional | The time to wait for verification in seconds (integer) | `30` |
| `rule` | Optional | The rule for verification: `any` or `all` | `any` |
| `event_name` | Optional | The name of the event associated with the verification | - |

**Example:**

```csv
Validate Screen,home_screen.png|menu.png,30,any,verify_home
```

### Capture Screenshot

Captures a screenshot of the current screen.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `event_name` | Optional | The name of the event associated with the screenshot capture | - |

**Example:**

```csv
Capture Screenshot,screenshot_before_action
```

### Capture Pagesource

Captures the page source of the current screen.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `event_name` | Optional | The name of the event associated with the page source capture | - |

**Example:**

```csv
Capture Pagesource,get_source
```

### Get Interactive Elements

Retrieves a list of interactive elements on the current screen.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `filter_config` | Optional | Optional list of filter types (e.g., "buttons,inputs") | - |

**Example:**

```csv
Get Interactive Elements,buttons
```

### Get Screen Elements

Captures a screenshot and retrieves interactive elements from the current screen in a single call. Returns a dict with a base64-encoded screenshot and a list of elements.

**Parameters:**

None.

**Example:**

```csv
Get Screen Elements
```

## App Management Keywords

These keywords handle application lifecycle operations.

### Launch App

Launches the specified application.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `app_identifier` | Optional | The app identifier (package name for Android, bundle ID for iOS) | - |
| `app_activity` | Optional | The app activity (Android only) | - |
| `event_name` | Optional | The event triggering the app launch | - |

**Example:**

```csv
Launch App,com.example.app,MainActivity,launch_main
```

### Start Appium Session

Starts an Appium session. This is typically called automatically during setup.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `event_name` | Optional | The event triggering the session start | - |

**Example:**

```csv
Start Appium Session,start_session
```

### Launch Other App

Starts another application.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `app_name` | Required | The package name or bundle ID of the application | - |
| `event_name` | Optional | The event triggering the app start | - |

**Example:**

```csv
Launch Other App,com.example.otherapp,launch_other
```

### Close And Terminate App

Tears down the active driver session and flushes all pending events. The exact
behaviour depends on the configured driver (Appium, Selenium, Playwright, BLE)
but in all cases the driver reference is released after this call.

**Parameters:**

None

**Example:**

```csv
Close And Terminate App
```

### Force Terminate App

Forcefully terminates the specified application.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `app_name` | Required | The name of the application to terminate | - |
| `event_name` | Optional | The event triggering the forced termination | - |

**Example:**

```csv
Force Terminate App,com.example.app,terminate_app
```

### Get App Version

Gets the version of the application.

**Parameters:**

None

**Example:**

```csv
Get App Version
```

### Get Driver Session Id

Returns the current driver session ID, if available.

**Parameters:**

None

**Example:**

```csv
Get Driver Session Id
```

## Flow Control Keywords

These keywords handle control flow operations like loops, conditions, and data manipulation. Module execution is performed by the runner when it runs test-case steps; the Optics API does not expose user-facing "Initialise Setup" or "Execute Module" keywords.

### Run Loop

Runs a loop over a target module, either by count or with variables.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `target` | Required | The module name to execute in the loop | - |
| `args` | Variable | Either a count (integer) or variable-iterable pairs:<br/>• Count: `["5"]` - runs 5 times<br/>• Variables: `["${var1}", "value1\|value2\|value3", "${var2}", "a\|b\|c"]` - iterates over values | - |

**Example (by count):**

```csv
Run Loop,test_module,5
```

**Example (with variables):**

```csv
Run Loop,test_module,${username},user1|user2|user3,${password},pass1|pass2|pass3
```

### Condition

Evaluates conditions and executes corresponding targets. Supports both module-based and expression-based conditions.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `args` | Variable | Condition-target pairs, optionally ending with an else target:<br/>• `["condition1", "target1", "condition2", "target2", "else_target"]`<br/>• Conditions can be module names (prefixed with `!` to invert) or expressions | - |

**Example (module condition):**

```csv
Condition,login_success,show_dashboard,login_failed,show_error
```

**Example (expression condition):**

```csv
Condition,${count} > 10,handle_large_count,handle_small_count
```

**Example (with else):**

```csv
Condition,${status} == "active",activate,deactivate,default_action
```

### Read Data

Reads tabular data from a CSV file, JSON file, environment variable, or a 2D list, applies optional filtering and column selection, and stores the result in the session's elements.

For the programmatic API and full parameter details, see [Flow Control](../api_reference/flow_control.md) in the API reference.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `input_element` | Required | The variable name where data will be stored. Prefer the form `${name}` (e.g. `${data}`). If another form is used, the value is still stored under that name (a warning may be logged). | - |
| `file_path` | Required | The data source. One of:<br/>• **File path**: Path to a `.csv` or `.json` file (relative paths are resolved from the project path). Only these extensions are supported.<br/>• **Environment variable**: Use the prefix `ENV:` followed by the variable name. Example: `ENV:APP_CONFIG` reads the env var `APP_CONFIG`. The value is interpreted as: (1) if it looks like JSON (starts with `[`, `{`, or `"`), it is parsed as JSON and objects/arrays are normalized to a table; (2) otherwise it is parsed as CSV if valid; (3) otherwise the whole value is stored as a single string.<br/>• **2D list**: Only when using the Python API: a list whose first row is headers and following rows are data (e.g. `[["col1","col2"],["a","b"]]`). Not passable as a literal in CSV test steps; see [Library Usage](library_usage.md) for programmatic use. | - |
| `query` | Optional | Semicolon-separated parts. Each part is either:<br/>• **Column selection**: `select=col1,col2,...` (comma-separated column names).<br/>• **Filter**: A pandas-style expression, e.g. `status=='active'`, `count>10`. Multiple filter parts are combined with `and`.<br/>Any `${varname}` in the query is replaced from `session.elements` before evaluation (e.g. `role=='${expected_role}'`). | `""` |

**Example (CSV file):**

```csv
Read Data,${users},users.csv,select=username,email
```

**Example (with filter):**

```csv
Read Data,${active_users},users.csv,status='active';select=username,email
```

**Example (environment variable):**

```csv
Read Data,${config},ENV:APP_CONFIG
```

**Example (query with variable):**

```csv
Read Data,${filtered},users.csv,role=='${expected_role}';select=id,name
```

**Note:** Inline list data (e.g. a 2D list) is supported only when calling the Python API; see [Library Usage](library_usage.md) for examples.

### Evaluate

Evaluates an expression and stores the result in `session.elements` under the given variable name.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `param1` | Required | The variable name where the result will be stored (e.g., `${result}`) | - |
| `param2` | Required | The expression to evaluate (can use variables like `${var1} + ${var2}`) | - |

**How `param2` is evaluated:**

- Every `${var}` in `param2` is first replaced, as plain text, with the variable's *stored string value* — before anything is evaluated as Python.
- The result of that substitution is then run through a restricted `eval()`. Only arithmetic (`+ - * / % **`), comparisons (`> < >= <= == !=`), boolean logic (`and or not`), a ternary `... if ... else ...`, and literal `list`/`tuple` values are allowed. Anything else (function calls, attribute access, imports, f-strings, etc.) is rejected with `Code.E0403 Unsafe expression detected`.
- Because the substitution is textual and unquoted, a `${var}` that holds **text** must be wrapped in quotes *inside the expression* (e.g. `'${name}'`), otherwise the substituted word is treated as a bare Python name and raises a `NameError`. A `${var}` that holds a **number** does not need quotes.
- The final result is stored via `str(result)`, so even numbers and booleans end up as strings in `session.elements` (e.g. `True` → `"True"`).
- `param1` must be in `${name}` form; if it isn't, a warning is logged and the raw string is used as the variable name as-is.

**Example (numeric arithmetic):**

```csv
Evaluate,${a},5
Evaluate,${b},3
Evaluate,${sum},${a} + ${b}
```

`${sum}` → `8`

**Example (comparison):**

```csv
Evaluate,${count},15
Evaluate,${is_valid},${count} > 10
```

`${is_valid}` → `True`

**Example (string literal):**

Wrap plain text in quotes so it's evaluated as a Python string, not as bare identifiers:

```csv
Evaluate,${greeting},'hello world'
```

`${greeting}` → `hello world`

**Example (concatenating a stored text variable):**

`${name}` holds text, so it must be quoted inside the expression once substituted:

```csv
Evaluate,${name},'Alice'
Evaluate,${message},'Hello, ' + '${name}'
```

`${message}` → `Hello, Alice`

**Example (ternary if/else):**

```csv
Evaluate,${score},72
Evaluate,${result},'Pass' if ${score} >= 50 else 'Fail'
```

`${result}` → `Pass`

**Example (list literal):**

```csv
Evaluate,${numbers},[1, 2, 3, 4]
```

`${numbers}` → `[1, 2, 3, 4]`

### Date Evaluate

Evaluates a date expression based on an input date and stores the result in session.elements.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `param1` | Required | The variable name where the evaluated date result will be stored (e.g., `${tomorrow}`) | - |
| `param2` | Required | The input date string (e.g., "04/25/2025" or "2025-04-25"). Format is auto-detected | - |
| `param3` | Required | The date expression to evaluate, such as "+1 day", "-2 days", or "today" | - |
| `param4` | Optional | The output format for the evaluated date (default is "%d %B", e.g., "26 April") | `%d %B` |

**Example:**

```csv
Date Evaluate,${tomorrow},04/25/2025,+1 day
```

**Example (with custom format):**

```csv
Date Evaluate,${next_week},2025-04-25,+7 days,%Y-%m-%d
```

### Add API

Adds or updates API definitions in the current session by loading from a file path or a dictionary. Use this when not using the runner's auto-discovery (e.g. when driving the framework programmatically). The supplied data **replaces** the session's API data.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `api_data` | Required | Either a path to an API YAML file, or a dictionary with the same structure (top-level `api` key with `collections`, etc.) | - |

**Example (file path):**

```csv
Add API,path/to/api.yaml
```

**Example (programmatic):** Pass a dict with an `api` key holding the collection/endpoint definitions. Relative paths are resolved against the project path when a string is given.

### Invoke API

Invokes an API call based on a definition from the session's API data.

**Parameters:**

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `api_identifier` | Required | The API identifier in format `collection.api_name` | - |

**Example:**

```csv
Invoke API,users.get_user
```

**Data source: API definition YAML**

Invoke API does **not** use element files. It uses **API definition YAML files** only. The format is different from element CSV/YAML: definitions live under a top-level `api` (or `apis`) key, with `collections` → each collection has `base_url`, `global_headers`, and `apis` → each API has `endpoint`, `request` (e.g. `method`, `headers`, `body`), and optionally `expected_result`, `extract`, and so on.

How API definitions reach the session:

- **CLI/runner:** YAML files under the project that contain a top-level `api` or `apis` key are auto-discovered and loaded as API data.
- **Programmatic:** The **Add API** keyword can load from a file path or a dict to set or replace session API data.
