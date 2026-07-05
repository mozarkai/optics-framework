# :material-api: REST API Usage

The Optics Framework provides a RESTful API for programmatic interaction with the framework. This API allows you to create sessions, execute keywords, capture screenshots, and manage test execution.

## :material-link: Base URL

The API is served via FastAPI and typically runs on `http://localhost:8000` by default.

## :material-shield-lock: Authentication

Currently, the API does not require authentication. CORS is enabled for all origins.

## :material-code-json: Response Format

All endpoints return JSON responses. Error responses follow this format:

```json
{
  "detail": "Error message"
}
```

## :material-database: Models

### SessionConfig

Configuration for starting a new Optics session.

```json
{
  "driver_sources": ["appium", "selenium"],
  "elements_sources": ["appium"],
  "text_detection": ["easyocr"],
  "image_detection": ["templatematch"],
  "project_path": "/path/to/project",
}
```

**Fields:**

- `driver_sources` (List[Union[str, Dict]]): List of driver sources. Can be strings like `["appium"]` or detailed dicts like `[{"appium": {"enabled": true, "url": "...", "capabilities": {...}}}]`
- `elements_sources` (List[Union[str, Dict]]): List of element detection sources
- `text_detection` (List[Union[str, Dict]]): List of text detection engines
- `image_detection` (List[Union[str, Dict]]): List of image detection engines
- `project_path` (Optional[str]): Path to the project directory
- `api_data` (Optional[Dict]): Inline API definitions for use with the **Invoke API** keyword. A JSON object with the same shape as the API YAML content (top-level `"api"` key with `collections`, or the API content at root). File path is not supported in the REST API; use inline object only. Omit if you will add definitions later via **Add API definitions**.
- `appium_url` (Optional[str]): **Deprecated** - Use driver_sources instead
- `appium_config` (Optional[Dict]): **Deprecated** - Use driver_sources instead

### ExecuteRequest

Request model for executing a keyword or test case.

```json
{
  "mode": "keyword",
  "test_case": null,
  "keyword": "press_element",
  "params": ["button_login"]
}
```

**Fields:**

- `mode` (str): Execution mode. Currently only `"keyword"` is supported
- `test_case` (Optional[str]): Test case name (not currently used)
- `keyword` (Optional[str]): Keyword name to execute
- `params` (Union[List, Dict]): Parameters for the keyword. Can be:
  - Positional: `["param1", "param2"]`
  - Named: `{"element": "button_login", "timeout": "30"}`
  - Fallback values: `["value1", "value2"]` or `[["value1a", "value1b"], "value2"]`

### SessionResponse

Response model for session creation.

```json
{
  "session_id": "uuid-string",
  "driver_id": "driver-session-id",
  "status": "created"
}
```

### ExecutionResponse

Response model for execution results.

```json
{
  "execution_id": "uuid-string",
  "status": "SUCCESS",
  "data": {
    "result": "execution result"
  }
}
```

### ExecutionEvent

Event model for execution status updates (used in SSE streams).

```json
{
  "execution_id": "uuid-string",
  "status": "RUNNING",
  "message": "Starting keyword: press_element"
}
```

### KeywordInfo

Information about an available keyword.

```json
{
  "keyword": "Press Element",
  "keyword_slug": "press_element",
  "description": "Taps on a given element with optional offset and repeat parameters.",
  "parameters": [
    {
      "name": "element",
      "type": "str",
      "default": null
    },
    {
      "name": "repeat",
      "type": "int",
      "default": 1
    }
  ]
}
```

## :material-routes: Endpoints

### Health Check

**GET** `/`

Check if the API is running.

**Response:**
```json
{
  "status": "Optics Framework API is running",
  "version": "1.7.14"
}
```

### Create Session

**POST** `/v1/sessions/start`

Create a new Optics session with the provided configuration.

**Request Body:** `SessionConfig`

**Response:** `SessionResponse`

**Example:**
```bash
curl -X POST "http://localhost:8000/v1/sessions/start" \
  -H "Content-Type: application/json" \
  -d '{
    "driver_sources": [{"appium": {"enabled": true, "url": "http://127.0.0.1:4723/wd/hub", "capabilities": {"automationName": "UiAutomator2", "deviceName": "emulator-5554", "platformName": "Android"}}}],
    "elements_sources": ["appium"],
    "text_detection": ["easyocr"],
    "image_detection": ["opencv"]
  }'
```

!!! info "Notes"
    - Automatically executes `launch_app` keyword after session creation
    - Returns both `session_id` and `driver_id` (the underlying driver session ID)
    - If `appium_url` or `appium_config` are provided, a deprecation warning is logged

### Execute Keyword

**POST** `/v1/sessions/{session_id}/action`

Execute a keyword in the specified session. Supports both positional and named parameters with fallback support.

**Path Parameters:**
- `session_id` (str): The session ID

**Request Body:** `ExecuteRequest`

- `mode` (str): Must be `"keyword"`.
- `keyword` (str): Keyword name (e.g. `"Press Element"`).
- `params`: Positional list or named dict of string parameters.
- `template_images` (optional): Map of logical name → base64-encoded image. Use these names in `params` (e.g. `element`) for vision-based keywords. Values can be raw base64 or a data URL (`data:image/png;base64,...`). Inline images apply only to this request.

**Response:** `ExecutionResponse`

**Example with positional parameters:**
```bash
curl -X POST "http://localhost:8000/v1/sessions/{session_id}/action" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "keyword",
    "keyword": "Press Element",
    "params": ["button_login"]
  }'
```

**Example with named parameters:**
```bash
curl -X POST "http://localhost:8000/v1/sessions/{session_id}/action" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "keyword",
    "keyword": "Press Element",
    "params": {
      "element": "button_login",
      "repeat": "2"
    }
  }'
```

**Example with fallback values:**
```bash
curl -X POST "http://localhost:8000/v1/sessions/{session_id}/action" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "keyword",
    "keyword": "Press Element",
    "params": [["button_login", "login_btn"], "1"]
  }'
```

!!! tip "Parameter Handling"
    - The API will try all combinations of fallback values until one succeeds
    - If all combinations fail, an error is returned with details about each attempt
    - Named parameters are converted to positional based on the method signature

**Example with inline template images (vision-based Press Element):**
```bash
curl -X POST "http://localhost:8000/v1/sessions/{session_id}/action" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "keyword",
    "keyword": "Press Element",
    "params": {"element": "my_button"},
    "template_images": {"my_button": "<base64-encoded-png>"}
  }'
```
Use the same name in `params` (e.g. `element`) as in `template_images`. You can also register templates per session via **Upload Template** and reuse names across execute calls.

### Upload Template

**POST** `/v1/sessions/{session_id}/templates`

Upload a template image for the session. The given name can be used in execute `params` (e.g. `element`) for vision-based keywords. Overwrites if the name already exists. Files are removed when the session is terminated.

**Path Parameters:**
- `session_id` (str): The session ID

**Request Body:** `{"name": "<logical-name>", "image_base64": "<base64-or-data-url>"}`

**Response:** `{"name": "<logical-name>", "status": "ok"}`

**Example:**
```bash
curl -X POST "http://localhost:8000/v1/sessions/{session_id}/templates" \
  -H "Content-Type: application/json" \
  -d '{"name": "my_button", "image_base64": "<base64-encoded-png>"}'
```

Then call execute with `"params": {"element": "my_button"}` (no need to send the image again).

### Add API definitions (for Invoke API)

**POST** `/v1/sessions/{session_id}/api`

Add or replace API definitions for the session. Use this when you need to call the **Invoke API** keyword and did not supply `api_data` at session creation. The request body **replaces** the session's API data (same semantics as the Add API keyword).

**Path Parameters:**
- `session_id` (str): The session ID

**Request Body:** A JSON object representing API data. Either a top-level `"api"` key with `collections` (and optional `global_defaults`), or the API content at root. Structure matches the [API definition YAML format](../usage/keyword_usage.md) (e.g. `collections` → each collection has `name`, `base_url`, `global_headers`, `apis` → each API has `endpoint`, `request`, `expected_result`, etc.).

**Response:** 204 No Content on success.

**Example:**
```bash
curl -X POST "http://localhost:8000/v1/sessions/{session_id}/api" \
  -H "Content-Type: application/json" \
  -d '{
    "api": {
      "collections": {
        "auth": {
          "name": "Auth APIs",
          "base_url": "https://api.example.com",
          "apis": {
            "token": {
              "name": "Get Token",
              "endpoint": "/token",
              "request": { "method": "POST", "body": {"user": "x"} },
              "expected_result": { "expected_status": 200, "extract": {"access_token": "access_token"} }
            }
          }
        }
      }
    }
  }'
```

### Using Invoke API

To run the **Invoke API** keyword via the REST API, the session must have API definitions. You can either:

1. **At session creation:** include `api_data` in the SessionConfig as an inline JSON object, or
2. **After creation:** call **Add API definitions** above with your API payload.

Then execute the keyword:

```bash
curl -X POST "http://localhost:8000/v1/sessions/{session_id}/action" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "keyword",
    "keyword": "Invoke API",
    "params": ["collection_name.api_name"]
  }'
```

Use the identifier format `collection_name.api_name` (e.g. `auth.token`). Extracted values from the API response are stored in the session and can be used by subsequent keywords or reads.

### Capture Screenshot

**GET** `/v1/sessions/{session_id}/screenshot`

Capture a screenshot in the specified session.

**Path Parameters:**
- `session_id` (str): The session ID

**Response:** `ExecutionResponse` with screenshot data in base64 format

**Example:**
```bash
curl "http://localhost:8000/v1/sessions/{session_id}/screenshot"
```

### Get Driver Session ID

**GET** `/v1/sessions/{session_id}/driver-id`

Get the underlying Driver session ID for this Optics session.

**Path Parameters:**
- `session_id` (str): The session ID

**Response:** `ExecutionResponse` with driver session ID in `data.result`

**Example:**
```bash
curl "http://localhost:8000/v1/sessions/{session_id}/driver-id"
```

### Get Elements

**GET** `/v1/sessions/{session_id}/elements`

Get interactive elements from the current session screen.

**Path Parameters:**
- `session_id` (str): The session ID

**Query Parameters:**
- `filter_config` (Optional[List[str]]): Filter types. Valid values:
  - `"all"`: Show all elements (default when None or empty)
  - `"interactive"`: Only interactive elements
  - `"buttons"`: Only button elements
  - `"inputs"`: Only input/text field elements
  - `"images"`: Only image elements
  - `"text"`: Only text elements
  - Can be combined: `?filter_config=buttons&filter_config=inputs`

**Response:** `ExecutionResponse` with elements array in `data.result`

**Example:**
```bash
curl "http://localhost:8000/v1/sessions/{session_id}/elements?filter_config=buttons&filter_config=inputs"
```

### Get Page Source

**GET** `/v1/sessions/{session_id}/source`

Capture the page source from the current session.

**Path Parameters:**
- `session_id` (str): The session ID

**Response:** `ExecutionResponse` with page source in `data.result`

**Example:**
```bash
curl "http://localhost:8000/v1/sessions/{session_id}/source"
```

### Get Screen Elements

**GET** `/v1/sessions/{session_id}/screen_elements`

Capture and get screen elements from the current session.

**Path Parameters:**
- `session_id` (str): The session ID

**Response:** `ExecutionResponse` with screen elements in `data.result`

**Example:**
```bash
curl "http://localhost:8000/v1/sessions/{session_id}/screen_elements"
```

### Stream Events

**GET** `/v1/sessions/{session_id}/events`

Stream execution events for the specified session using Server-Sent Events (SSE).

**Path Parameters:**
- `session_id` (str): The session ID

**Response:** Server-Sent Events stream

**Event Format:**
Each event is a JSON object:
```json
{
  "execution_id": "uuid-string",
  "status": "RUNNING|SUCCESS|FAIL|HEARTBEAT",
  "message": "Event message"
}
```

**Example:**
```bash
curl -N "http://localhost:8000/v1/sessions/{session_id}/events"
```

!!! info "Event Streaming"
    - Sends heartbeat events every 15 seconds if no execution events occur
    - Connection remains open until the session is terminated or client disconnects

### Stream Workspace

**GET** `/v1/sessions/{session_id}/workspace/stream`

Stream workspace data (screenshot, elements, optionally source) for the specified session using Server-Sent Events (SSE). Only emits updates when workspace data actually changes.

**Path Parameters:**
- `session_id` (str): The session ID

**Query Parameters:**
- `interval_ms` (int, default: 2000): Polling interval in milliseconds (minimum 500ms)
- `include_source` (bool, default: false): Include page source in workspace data
- `filter_config` (Optional[List[str]]): Filter types for elements (same as `/elements` endpoint)

**Response:** Server-Sent Events stream

**Event Format:**
Each event is a JSON object:
```json
{
  "screenshot": "base64-encoded-image",
  "elements": [...],
  "screenshotFailed": false,
  "source": "..." // only if include_source=true
}
```

Or heartbeat:
```json
{
  "type": "heartbeat",
  "timestamp": 1234567890.123
}
```

**Example:**
```bash
curl -N "http://localhost:8000/v1/sessions/{session_id}/workspace/stream?interval_ms=2000&include_source=true"
```

!!! tip "Performance"
    - Only emits when workspace data actually changes (detected via hash comparison)
    - Sends heartbeat events every 15 seconds if no changes occur
    - Screenshot and elements are gathered in parallel for better performance

### List Keywords

**GET** `/v1/keywords`

List all available keywords and their parameters.

**Response:** `List[KeywordInfo]`

**Example:**
```bash
curl "http://localhost:8000/v1/keywords"
```

**Response Example:**
```json
[
  {
    "keyword": "Press Element",
    "keyword_slug": "press_element",
    "description": "Taps on a given element with optional offset and repeat parameters.",
    "parameters": [
      {
        "name": "element",
        "type": "str",
        "default": null
      },
      {
        "name": "repeat",
        "type": "int",
        "default": 1
      }
    ]
  }
]
```

### Dry Run a Suite

Validate a whole test suite **without touching a device**: every keyword is
checked against the registry and every `${variable}` is resolved, producing a
per-test-case / per-module / per-keyword PASS/FAIL report. The dry run executes
on an ephemeral, **device-less** session (no `driver_sources` required — any
driver configuration you send is ignored), which is always torn down afterwards.

There are two ways to submit a suite.

#### Inline JSON

**POST** `/v1/dry_run`

**Request Body:** `DryRunRequest`

| Field | Type | Notes |
|-------|------|-------|
| `test_cases` | `{name: [module, ...]}` | required |
| `modules` | `{name: [[keyword, [param, ...]], ...]}` | required |
| `elements` | `{name: [value, ...]}` | optional |
| `api` | `ApiData`-shaped object | optional |
| `include` / `exclude` | `[name, ...]` | optional filter (setup/teardown always kept) |

**Example:**
```bash
curl -X POST "http://localhost:8000/v1/dry_run" \
  -H "Content-Type: application/json" \
  -d '{
        "test_cases": {"Smoke": ["Open and Search"]},
        "modules": {"Open and Search": [["Launch App"], ["Press Element", ["${search_box}"]]]},
        "elements": {"search_box": ["xpath=//input", "text=Search"]}
      }'
```

#### File / Archive Upload

**POST** `/v1/dry_run/upload` (`multipart/form-data`)

Upload your CSV/YAML suite files directly, or a single `.zip` of the project.
Optional `include` / `exclude` form fields are comma-separated.

**Example (zip):**
```bash
curl -X POST "http://localhost:8000/v1/dry_run/upload" \
  -F "files=@my_suite.zip"
```

**Example (individual files):**
```bash
curl -X POST "http://localhost:8000/v1/dry_run/upload" \
  -F "files=@test_cases/cases.csv" \
  -F "files=@modules/mods.csv" \
  -F "files=@test_data/elements.csv" \
  -F "include=Smoke"
```

**Response (both endpoints):** `DryRunResponse`
```json
{
  "execution_id": "1f0b5e32-...",
  "status": "FAIL",
  "test_cases": [
    {
      "id": "6aa0f938-...",
      "name": "Smoke",
      "status": "FAIL",
      "elapsed": "0.00s",
      "modules": [
        {
          "name": "Open and Search",
          "status": "FAIL",
          "elapsed": "0.00s",
          "keywords": [
            {"name": "Launch App", "status": "PASS", "reason": ""},
            {"name": "Bad Keyword", "status": "FAIL", "reason": ""}
          ]
        }
      ]
    }
  ]
}
```

!!! note "Status codes"
    - A suite with an unknown keyword or unresolved variable returns **200** with
      `status: "FAIL"` and the offending step marked `FAIL` — that is the dry
      run's job, not a server error.
    - An empty / no-test-case suite returns **400**.
    - An unsafe archive (path traversal) returns **400**; an oversized body or
      archive returns **413**.

!!! warning "Limits & security"
    - Inline JSON body is capped at 5 MiB; total upload at 10 MiB; total
      decompressed archive at 50 MiB (zip-slip and zip-bomb protected).
    - As with every endpoint, the dry-run endpoints are **unauthenticated** — do
      not expose the server to untrusted networks without an auth layer in front.

### Terminate Session

**DELETE** `/v1/sessions/{session_id}/stop`

Terminate the specified session and clean up resources.

**Path Parameters:**
- `session_id` (str): The session ID

**Response:** `TerminationResponse`

**Example:**
```bash
curl -X DELETE "http://localhost:8000/v1/sessions/{session_id}/stop"
```

!!! warning "Termination"
    - Automatically executes `close_and_terminate_app` keyword before termination
    - Cleans up all session resources

## :material-alert-circle: Error Handling

The API uses standard HTTP status codes:

- `200 OK`: Request succeeded
- `400 Bad Request`: Invalid request parameters
- `404 Not Found`: Session not found
- `500 Internal Server Error`: Server error

Error responses include a `detail` field with the error message:

```json
{
  "detail": "Session not found"
}
```

For Optics-specific errors, the response may include additional fields:

```json
{
  "detail": {
    "code": "E0402",
    "message": "Keyword not found",
    "status": 400
  }
}
```

## :material-routes: Common Workflows

### Basic Session Workflow

1. **Create a session:**
   ```bash
   POST /v1/sessions/start
   ```

2. **Execute keywords:**
   ```bash
   POST /v1/sessions/{session_id}/action
   ```

3. **Monitor execution (optional):**
   ```bash
   GET /v1/sessions/{session_id}/events
   ```

4. **Terminate session:**
   ```bash
   DELETE /v1/sessions/{session_id}/stop
   ```

### Real-time Workspace Monitoring

1. **Create a session:**
   ```bash
   POST /v1/sessions/start
   ```

2. **Stream workspace updates:**
   ```bash
   GET /v1/sessions/{session_id}/workspace/stream?interval_ms=1000
   ```

3. **Terminate when done:**
   ```bash
   DELETE /v1/sessions/{session_id}/stop
   ```

## :material-information: Notes

- All endpoints that accept `session_id` will return `404` if the session doesn't exist
- The API supports CORS for all origins
- Session creation automatically launches the app configured in the session
- Fallback parameter support allows trying multiple values until one succeeds
- Named parameters are automatically converted to positional parameters based on method signatures
- Workspace streaming only emits when data changes, reducing load on the driver
