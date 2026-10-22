# Configuration Reference

This document provides comprehensive documentation for all configuration options available in the Optics Framework. The configuration system is managed by the `ConfigHandler` class and uses YAML files for configuration.

## Overview

Configuration is **project-specific**: the one file that matters is `config.yaml` in your project directory. It is what `optics execute <folder>` / `optics dry_run <folder>` actually read, and built-in defaults fill in any field you omit.

!!! tip "Writing a project config"
    Run `optics configure <folder>` to answer a few questions and generate the file, or `optics quickstart` for the full guided setup (project + config + environment check via `optics doctor`).

## Configuration Examples

=== "Android Mobile App"

    Complete configuration for Android app testing with Appium:

    ```yaml
    # Core Settings
    console: true
    file_log: true
    log_level: INFO
    project_path: "./my_android_project"
    halt_duration: 0.1

    # Driver Configuration
    driver_sources:
      - appium:
          enabled: true
          url: "http://localhost:4723/wd/hub"
          capabilities:
            automationName: "UiAutomator2"
            deviceName: "emulator-5554"
            platformName: "Android"
            platformVersion: "13.0"
            appPackage: "com.example.app"
            appActivity: "com.example.app.MainActivity"

    # Element Sources
    elements_sources:
      - appium_find_element:
          enabled: true
          url: null
          capabilities: {}
      - appium_screenshot:
          enabled: true
          url: null
          capabilities: {}

    # Text Detection
    text_detection:
      - easyocr:
          enabled: true
          url: null
          capabilities: {}

    # Image Detection
    image_detection:
      - templatematch:
          enabled: true
          url: null
          capabilities: {}
    ```

=== "Web Application (Playwright)"

    Configuration for web testing using Playwright:

    ```yaml
    # Core Settings
    console: true
    file_log: true
    json_log: true
    log_level: INFO
    project_path: "./web_test_project"
    halt_duration: 0.2

    # Driver Configuration
    driver_sources:
      - playwright:
          enabled: true
          url: null
          capabilities:
            browser: "chromium"
            headless: false
            viewport:
              width: 1920
              height: 1080

    # Element Sources
    elements_sources:
      - playwright_find_element:
          enabled: true
          url: null
          capabilities: {}
      - playwright_screenshot:
          enabled: true
          url: null
          capabilities: {}

    # Text Detection
    text_detection:
      - pytesseract:
          enabled: true
          url: null
          capabilities: {}

    # Image Detection
    image_detection:
      - templatematch:
          enabled: true
          url: null
          capabilities: {}
    ```

=== "Mixed Driver Configuration"

    Example with multiple drivers for fallback support:

    ```yaml
    driver_sources:
      - appium:
          enabled: true
          url: "http://localhost:4723/wd/hub"
          capabilities:
            automationName: "UiAutomator2"
            deviceName: "emulator-5554"
            platformName: "Android"
      - selenium:
          enabled: true
          url: "http://localhost:4444/wd/hub"
          capabilities:
            browserName: "chrome"

    elements_sources:
      - appium_find_element:
          enabled: true
          url: null
          capabilities: {}
      - selenium_find_element:
          enabled: true
          url: null
          capabilities: {}
    ```

=== "Full Logging Configuration"

    Example with comprehensive logging setup:

    ```yaml
    console: true
    file_log: true
    json_log: true
    log_level: DEBUG
    log_path: "./logs/execution.log"
    json_path: "./logs/execution.json"
    project_path: "./test_project"
    execution_output_path: "./outputs"
    ```

---

## Quick Reference

| Category | Key Settings | Common Values |
|----------|-------------|---------------|
| **Logging** | `console`, `file_log`, `json_log`, `log_level` | `log_level: INFO` or `DEBUG` |
| **Paths** | `project_path`, `execution_output_path` | `./my_project`, `./outputs` |
| **Execution** | `halt_duration` | `0.1` |
| **Drivers** | `appium`, `selenium`, `playwright`, `ble` | See Driver Sources tab |
| **Element Sources** | `appium_find_element`, `playwright_screenshot`, etc. | See Element Sources tab |
| **Text Detection** | `easyocr`, `pytesseract`, `google_vision` | See Text Detection tab |
| **Image Detection** | `templatematch`, `remote_oir` | See Image Detection tab |
| **LLM Models** | `gemini`, `openai`, `anthropic` | See LLM Models tab |
| **AI Self-Healing** | `ai_self_heal` | `false` (opt-in) |

## Configuration Structure

All configurations are defined in YAML format. The main configuration file (`config.yaml`) supports the following top-level sections:

- **Core settings** - Logging, paths, execution parameters
- **Driver sources** - Automation frameworks (Appium, Selenium, Playwright, BLE)
- **Element sources** - Element detection methods
- **Text detection engines** - OCR capabilities
- **Image detection engines** - Template matching
- **LLM models** - Optional LLM backend for AI self-healing and `optics live`'s natural-language mode

---

## Core Settings

=== "Logging Configuration"

    ### `console`

    **Type:** `bool` | **Default:** `true`

    Enable or disable console log output.

    ```yaml
    console: true
    ```

    ### `file_log`

    **Type:** `bool` | **Default:** `false`

    Enable writing logs to a file. When enabled, logs are written to the path specified by `log_path` or a default location.

    ```yaml
    file_log: true
    log_path: "./logs/test_execution.log"  # Optional, defaults to execution_output_path/logs.log
    ```

    ### `json_log`

    **Type:** `bool` | **Default:** `false`

    Enable JSON format logging. When enabled, logs are written in JSON format to the path specified by `json_path`.

    ```yaml
    json_log: true
    json_path: "./logs/test_logs.json"  # Optional, defaults to execution_output_path/logs.json
    ```

    ### `log_level`

    **Type:** `str` | **Default:** `"INFO"`

    Sets the verbosity of log messages. Valid values (in order of verbosity):

    - `DEBUG` - Detailed information for troubleshooting
    - `INFO` - General informational messages (default)
    - `WARNING` - Warning messages only
    - `ERROR` - Error messages only
    - `CRITICAL` - Critical failures only

    ```yaml
    log_level: DEBUG
    ```

    ### `log_path`

    **Type:** `Optional[str]` | **Default:** `null`

    Path for log file. If not specified and `file_log` is enabled, defaults to `{execution_output_path}/logs.log`.

    ```yaml
    file_log: true
    log_path: "./logs/custom_execution.log"
    ```

    ### `json_path`

    **Type:** `Optional[str]` | **Default:** `null`

    Path for JSON log file. If not specified and `json_log` is enabled, defaults to `{execution_output_path}/logs.json`.

    ```yaml
    json_log: true
    json_path: "./logs/custom_logs.json"
    ```

=== "Paths & Execution"

    ### `project_path`

    **Type:** `Optional[str]` | **Default:** `null`

    Root directory for test project files. This folder holds your test cases, modules, and elements (either the sample subdir layout — `test_cases/`, `modules/`, `test_data/` — or flat CSVs; the runner discovers them by content) plus any `input_templates/` images.

    ```yaml
    project_path: "./my_test_project"
    ```

    ### `execution_output_path`

    **Type:** `Optional[str]` | **Default:** `null` (auto-generated)

    Directory where execution outputs (logs, screenshots, etc.) are stored. If not specified, defaults to `{project_path}/execution_output` when `project_path` is set, or `{cwd}/execution_output` (current working directory) otherwise.

    ```yaml
    execution_output_path: "./outputs"
    ```

    ### `halt_duration`

    **Type:** `float` | **Default:** `0.1`

    Pause duration (in seconds) between actions. This helps ensure UI stability and prevents race conditions.

    ```yaml
    halt_duration: 0.1  # 100ms pause between actions
    ```

=== "Test Control"

    ### `include`

    **Type:** `Optional[List[str]]` | **Default:** `null`

    List of test case names to include in execution. Only the specified test cases will be executed; all others will be skipped.

    ```yaml
    include:
      - "Test Login Flow"
      - "Test Checkout Process"
    ```

    ### `exclude`

    **Type:** `Optional[List[str]]` | **Default:** `null`

    List of test case names to exclude from execution. All other test cases will be executed.

    ```yaml
    exclude:
      - "Test Legacy Feature"
      - "Test Deprecated Flow"
    ```

    ### `event_attributes_json`

    **Type:** `Optional[str]` | **Default:** `null`

    Path to a JSON file containing event attributes for the Event SDK. This file defines custom attributes to be included in event tracking.

    ```yaml
    event_attributes_json: "./config/event_attributes.json"
    ```

=== "AI Self-Healing"

    ### `ai_self_heal`

    **Type:** `bool` | **Default:** `false`

    Last-resort recovery for element location. When every locator strategy (XPath, text,
    OCR, image) fails for a self-healing-capable keyword (e.g. `Press Element`, `Enter Text`),
    a bounded LLM-driven loop reads the screen and attempts to recover before the keyword
    fails outright.

    Opt-in and inert unless an entry under `llm_models` is also enabled — a missing or
    misconfigured LLM degrades this to a no-op rather than a failure:

    ```yaml
    ai_self_heal: true

    llm_models:
      - gemini:
          enabled: true
    ```

    See [LLM Models](#llm-models) below for backend configuration.

---

## Driver Sources

Driver sources define the automation frameworks used to control devices or browsers.

=== "Appium"

    **Purpose:** Mobile app automation for Android and iOS devices.

    **Configuration:**

    ```yaml
    driver_sources:
      - appium:
          enabled: true
          url: "http://localhost:4723/wd/hub"
          capabilities:
            automationName: "UiAutomator2"  # or "XCUITest" for iOS
            deviceName: "emulator-5554"
            platformName: "Android"  # or "iOS"
            platformVersion: "13.0"
            appPackage: "com.example.app"
            appActivity: "com.example.app.MainActivity"
            udid: "device_unique_id"  # Optional, for specific device
    ```

    **Common Capabilities:**

    | Capability | Android | iOS | Description |
    |------------|---------|-----|-------------|
    | `automationName` | `"UiAutomator2"` | `"XCUITest"` | Automation framework |
    | `platformName` | `"Android"` | `"iOS"` | Platform identifier |
    | `platformVersion` | `"13.0"` | `"16.0"` | OS version |
    | `deviceName` | Device identifier | Device identifier | Device name |
    | `appPackage` | Package name | - | Android app package |
    | `appActivity` | Activity name | - | Android activity |
    | `udid` | Device UDID | Device UDID | Unique device ID |

    !!! warning "Android: elements off-screen may be missing from Capture Pagesource"
        On Android (UiAutomator2), elements not currently rendered on screen (e.g. scrolled
        out of view) are omitted entirely from `Capture Pagesource` and the page
        source-backed element sources by default -- so `Assert Visibility` can't distinguish
        "off-screen" from "doesn't exist" for them, since they never show up in the tree at
        all. Set `allowInvisibleElements: true` under `appium:settings` to include them:

        ```yaml
        capabilities:
          appium:settings:
            allowInvisibleElements: true
        ```

        iOS (XCUITest) includes such elements by default (with a `visible="false"`
        attribute) -- no equivalent setting needed.

=== "Selenium"

    **Purpose:** Web browser automation.

    **Configuration:**

    ```yaml
    driver_sources:
      - selenium:
          enabled: true
          url: "http://localhost:4444/wd/hub"  # Selenium Grid or standalone
          capabilities:
            browserName: "chrome"  # or "firefox", "safari", "edge"
            browserVersion: "latest"
            platformName: "Windows"
    ```

    **Common Capabilities:**

    | Capability | Values | Description |
    |------------|--------|-------------|
    | `browserName` | `"chrome"`, `"firefox"`, `"safari"`, `"edge"` | Browser type |
    | `browserVersion` | Version string | Browser version |
    | `platformName` | `"Windows"`, `"Linux"`, `"macOS"` | Operating system |

=== "Playwright"

    **Purpose:** Modern web automation with better reliability and performance.

    **Configuration:**

    ```yaml
    driver_sources:
      - playwright:
          enabled: true
          url: null  # null launches a local browser; see "Remote browser" below
          capabilities:
            browser: "chromium"  # or "firefox", "webkit"
            headless: false
            viewport:
              width: 1920
              height: 1080
    ```

    **Common Capabilities:**

    | Capability | Values | Description |
    |------------|--------|-------------|
    | `browser` | `"chromium"`, `"firefox"`, `"webkit"` | Browser engine |
    | `headless` | `true`, `false` | Run browser in headless mode (local browser only) |
    | `viewport` | `{width, height}` | Browser viewport dimensions |
    | `device` | A Playwright device name, e.g. `"Pixel 7"`, `"iPhone 15"` | Emulate that phone or tablet: its screen, user agent and touch input. Pair an iPhone with `browser: webkit` for the Safari engine. Not with `context: existing` |
    | `storage_state` | Path to a file saved by Playwright, relative to the project folder | Start signed in, with the file's cookies and local storage. Works with `device` and `viewport`. Not with `context: existing` |
    | `headers` | `{name: value}` | HTTP headers sent when connecting to a remote browser |
    | `protocol` | `"cdp"`, `"playwright"` | How to connect to the remote browser in `url` (default `"cdp"`) |
    | `context` | `"new"`, `"existing"` | Open a fresh context in the remote browser, or use the one it already has (default `"new"`) |

    **Remote browser:** set `url` to a remote browser's endpoint and Optics connects to that browser instead of launching one. It works with any browser grid or service that speaks one of Playwright's two remote protocols:

    | `protocol` | Playwright call | Speaks to |
    |------------|-----------------|-----------|
    | `cdp` (default) | `connect_over_cdp` | Any Chrome DevTools Protocol endpoint (`ws://`, `wss://`, or `http://` for `/json/version` discovery). Chromium-based browsers only (`browser: chromium`) |
    | `playwright` | `connect` | A Playwright server (`playwright run-server`, or a grid built on one). Any `browser`; the server's Playwright version must match the client's |

    Use the protocol the provider documents for Playwright's `connect_over_cdp` or `connect`; a `/playwright` path in a url does not by itself mean the Playwright protocol. A mismatch shows up as a connect timeout. Whatever a provider needs, such as browser version, OS or session options, goes in the url's query string as that provider documents it, often as URL-encoded JSON. Credentials go in `headers` or in the url, written as `${NAME}` references:

    ```yaml
    driver_sources:
      - playwright:
          enabled: true
          url: "wss://grid.example.com/session?options=%7B%22browserVersion%22%3A%22latest%22%7D"
          capabilities:
            protocol: cdp
            headers:
              Authorization: Bearer ${GRID_TOKEN}
            connect_timeout_ms: 90000
    ```

    The same keys cover the common shapes. Check each provider's docs for its current endpoint and options:

    | Remote browser | `url` | `protocol` | Credentials |
    |----------------|-------|------------|-------------|
    | Chrome started with `--remote-debugging-port=9222 --user-data-dir=<dir>` | `http://127.0.0.1:9222` | `cdp` | none, or the profile's own sign-ins with `context: existing` |
    | A CDP grid that takes a token in the query, such as Browserless | `wss://<host>?token=${GRID_TOKEN}` | `cdp` | in the url |
    | A CDP grid that takes headers | `wss://<host>/<path>` | `cdp` | `headers` |
    | A Playwright server or grid, such as `npx playwright run-server` | `ws://<host>:<port>/` | `playwright` | `headers`, if any |
    | A Playwright grid with capabilities in the query, such as BrowserStack (`caps=`) or LambdaTest (`capabilities=`) | `wss://<host>/playwright?caps=<URL-encoded JSON with ${USER} and ${ACCESS_KEY}>` | `playwright` | inside the capabilities JSON |

    `${NAME}` in `url` or in a header value is replaced with that environment variable when the browser starts, so credentials stay out of the suite. The value is substituted as is, so inside a URL-encoded query write the reference itself unencoded. An unset or empty variable fails the launch with an error that names it. Only the url's host is logged, and secret values and the url's path and query are masked in connect errors. `Close And Terminate App`, or the end of the run, closes the connection, which is what ends the session on the provider's side. No local browser download (`playwright install`) is needed.

    By default Optics opens a fresh context, with the configured `viewport`, in the remote browser. Set `context: existing` to use the browser's own context and its open tab instead, when a provider documents `browser.contexts[0]` because its session settings or recording live there, when a browser cannot create new contexts, or to reuse a signed-in profile. The page then keeps the browser's own window size, `viewport` is not applied, and ending the run disconnects without closing that context's tabs.

    To be signed in and still emulate a `device` or `viewport`, save the sign-ins to a file once and set `storage_state` to it. Every run then opens a fresh context with them, locally or in a remote browser. Sign in by hand in a browser that saves its state when you close it:

    ```bash
    python -m playwright open --save-storage=auth.json https://example.com
    ```

    or save the state of a Chrome profile you are already signed in with, started with `--remote-debugging-port=9222 --user-data-dir=<dir>`:

    ```python
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        p.chromium.connect_over_cdp("http://127.0.0.1:9222").contexts[0].storage_state(path="auth.json")
    ```

    The file holds live session cookies, so keep it out of version control. Cookies that belong to a browser extension are left out, because Chromium refuses to set them in a new context. Some sites end a session when the user agent changes, so a `device` can sign you out of them. Through `optics serve` or `optics mcp`, send the state's JSON object as `storage_state` instead of a path.

    To run every session on a remote browser without editing a suite, for example in CI or for an `optics mcp` server, set `OPTICS_PLAYWRIGHT_REMOTE` to a JSON object with `url` and, optionally, `headers`, `protocol` and `context`. It takes precedence over the driver config; timeouts still come from `capabilities`:

    ```bash
    export OPTICS_PLAYWRIGHT_REMOTE='{"url": "wss://grid.example.com/session", "headers": {"Authorization": "Bearer ${GRID_TOKEN}"}}'
    ```

    `${NAME}` references are read only from a project's `config.yaml` and from `OPTICS_PLAYWRIGHT_REMOTE`. `optics serve` and `optics mcp` reject them in a session request, so a caller cannot make the server send one of its environment variables to an endpoint the caller picked. A caller sends the values themselves, or the server's operator sets `OPTICS_PLAYWRIGHT_REMOTE`.

    **Timeouts** (all in milliseconds, all under `capabilities`):

    | Capability | Default | Bounds |
    |------------|---------|--------|
    | `connect_timeout_ms` | `90000` | Connecting to a remote browser, including the time the provider takes to start it |
    | `navigation_timeout_ms` | `60000` | `Launch App` and `Go Back` page loads |
    | `navigation_wait_until` | `"domcontentloaded"` | Not a timeout: the load event a navigation waits for (`load`, `domcontentloaded`, `networkidle`, `commit`) |
    | `element_timeout_ms` | `10000` | Waiting for an element to appear before acting on it, per locator |
    | `settle_timeout_ms` | `3000` | Waiting for the page to stop changing before reading page source or elements; `0` turns it off |
    | `action_timeout_ms` | `15000` | Any single browser call that has no timeout of its own (click at coordinates, keys, scroll, screenshot, script). It is also the margin added on top of the other budgets |

=== "BLE"

    **Purpose:** Bluetooth Low Energy (BLE) device automation for non-intrusive mouse/keyboard control.

    **Configuration:**

    ```yaml
    driver_sources:
      - ble:
          enabled: true
          url: null
          capabilities:
            device_id: "Samsung A50"
            port: "/dev/ttyACM0"
            x_invert: 1
            y_invert: 1
            pixel_width: 1080
            pixel_height: 2336
            mickeys_height: 2336
            mickeys_width: 1080
    ```

    **Common Capabilities:**

    | Capability | Description |
    |------------|-------------|
    | `device_id` | Device identifier |
    | `port` | Serial port for BLE communication |
    | `x_invert`, `y_invert` | Coordinate inversion flags |
    | `pixel_width`, `pixel_height` | Screen pixel dimensions |
    | `mickeys_width`, `mickeys_height` | Mouse coordinate dimensions |

=== "Smart TV (Appium)"

    **Purpose:** Smart TV automation via platform-specific Appium profiles. **Android TV is supported via the Android profile** (same as phones). LG webOS and Samsung Tizen need separate setup, described below.

    | Platform | Notes |
    |----------|-------|
    | **Android TV** | Supported via Android profile — same as phone automation |
    | **Samsung Tizen** | D-pad navigation + DOM access (limited) |
    | **LG webOS** | D-pad navigation only (blind remote control) |

    Both Tizen and webOS use D-pad buttons (UP/DOWN/ENTER/BACK) for navigation, not touch. Touch-based methods (`click_element`, `swipe`, `enter_text`) raise an error on TVs.

    **Samsung Tizen prerequisites:**
    - **Tizen Studio** installed; `TIZEN_HOME` set
    - TV in Developer Mode (Settings → enter 12345 via 123 button → Client IP = your Appium host)
    - `sdb connect <tv-ip>` and verified with `sdb devices`
    - **Appium 2.x** with driver: `appium driver install --source=npm appium-tizen-tv-driver`
    - RC token: `appium driver run tizentv pair-remote --host <tv-ip>` (approve on-screen prompt)
    - Debug-signed `.wgt` app binary
    - Matching chromedriver version for the TV's Chromium

    ```yaml
    driver_sources:
      - appium:
          enabled: true
          url: "http://localhost:4723"
          capabilities:
            platformName: TizenTV
            appium:automationName: TizenTV
            appium:deviceAddress: 192.168.1.50
            appium:rcToken: "14184553"
            appium:appPackage: com.example.app
            appium:app: /path/to/app.wgt
            appium:chromedriverExecutableDir: /path/to/chromedrivers

    elements_sources:
      - appium_page_source:
          enabled: true
      - appium_screenshot:
          enabled: true
    ```

    **LG webOS prerequisites:**
    - **webOS SDK** installed (`ares-cli` on PATH)
    - TV in Developer Mode (Dev Mode app → signed in → ON; expires every ~50 hours)
    - `ares-setup-device -n <name> -i <ip>` to register the TV
    - **Appium 2.x** with driver: `appium driver install --source=npm appium-lg-webos-driver`
    - RC pairing happens automatically on first session (approve on-screen)
    - Matching chromedriver version

    ```yaml
    driver_sources:
      - appium:
          enabled: true
          url: "http://localhost:4723"
          capabilities:
            platformName: LGTV
            appium:automationName: webOS
            appium:deviceHost: 192.168.1.50
            appium:appId: com.example.app
            appium:rcMode: rc
            appium:useSecureWebsocket: true
            appium:newCommandTimeout: 600

    elements_sources:
      - appium_screenshot:
          enabled: true
    ```

    **Limitations:**

    - Methods that raise an error on TVs — Touch: `click_element`, `press_element`, `press_coordinates`, `tap_at_coordinates`, `swipe*`; Keyboard: `enter_text`, `clear_text`; Other: `scroll`, `get_text_element`, `press_xpath_using_coordinates`.
    - Methods that work everywhere: `launch_app`, `press_keycode`, `execute_script`, `terminate`.
    - Text input isn't supported — use a sequence of `Press Keycode` steps for D-pad navigation through on-screen keyboards.
    - Native screenshots can hang on Tizen; Optics renders the DOM to canvas instead. DRM/video content appears black — judge playback from UI elements.
    - Chromedriver version must match the TV's Chromium exactly (e.g. webOS 6 → Chromium 79 → chromedriver 79.x). Mismatches cause "cannot connect to chrome" errors.
    - webOS and Tizen drivers require Appium 2.x (no Appium 3 support yet).

    **References:**
    - [LG webOS developer site](https://webostv.developer.lge.com/)
    - [Samsung Developer — Tizen TV](https://developer.samsung.com/smarttv)
    - [Appium webOS driver](https://github.com/headspinio/appium-lg-webos-driver)
    - [Appium Tizen driver](https://github.com/headspinio/appium-tizen-tv-driver)

---

## Element Sources

Element sources define methods for locating and capturing UI elements.

=== "Appium Sources"

    ### `appium_find_element`

    **Purpose:** Locates elements using Appium's native element finding strategies (XPath, ID, etc.).

    ```yaml
    elements_sources:
      - appium_find_element:
          enabled: true
          url: null
          capabilities: {}
    ```

    ### `appium_page_source`

    **Purpose:** Retrieves the entire XML page source from Appium for element location.

    ```yaml
    elements_sources:
      - appium_page_source:
          enabled: true
          url: null
          capabilities: {}
    ```

    ### `appium_screenshot`

    **Purpose:** Captures screenshots through Appium for visual element detection.

    ```yaml
    elements_sources:
      - appium_screenshot:
          enabled: true
          url: null
          capabilities: {}
    ```

=== "Selenium Sources"

    ### `selenium_find_element`

    **Purpose:** Locates elements using Selenium's element finding strategies (CSS selectors, XPath, etc.).

    ```yaml
    elements_sources:
      - selenium_find_element:
          enabled: true
          url: null
          capabilities: {}
    ```

    ### `selenium_screenshot`

    **Purpose:** Captures screenshots from Selenium browser sessions.

    ```yaml
    elements_sources:
      - selenium_screenshot:
          enabled: true
          url: null
          capabilities: {}
    ```

=== "Playwright Sources"

    ### `playwright_find_element`

    **Purpose:** Locates elements using Playwright's modern locator API (CSS, text, XPath).

    ```yaml
    elements_sources:
      - playwright_find_element:
          enabled: true
          url: null
          capabilities: {}
    ```

    ### `playwright_page_source`

    **Purpose:** Retrieves DOM HTML from Playwright for element location.

    ```yaml
    elements_sources:
      - playwright_page_source:
          enabled: true
          url: null
          capabilities: {}
    ```

    ### `playwright_screenshot`

    **Purpose:** Captures high-quality screenshots from Playwright sessions.

    ```yaml
    elements_sources:
      - playwright_screenshot:
          enabled: true
          url: null
          capabilities: {}
    ```

=== "Camera Source"

    ### `camera_screenshot`

    **Purpose:** Captures screenshots from external cameras or capture cards (useful for production monitoring).

    ```yaml
    elements_sources:
      - camera_screenshot:
          enabled: true
          url: null
          capabilities: {}
    ```

---

## Text Detection

Text detection engines provide OCR (Optical Character Recognition) capabilities for locating text on screen.

=== "EasyOCR"

    **Purpose:** EasyOCR library for text recognition. Provides good accuracy but may be slower.

    ```yaml
    text_detection:
      - easyocr:
          enabled: true
          url: null
          capabilities: {}
    ```

    !!! tip "Performance"
        EasyOCR provides excellent accuracy but can be slower than Pytesseract. Consider using it when accuracy is more important than speed.

=== "Pytesseract"

    **Purpose:** Tesseract OCR engine via Python wrapper. Generally faster than EasyOCR.

    ```yaml
    text_detection:
      - pytesseract:
          enabled: true
          url: null
          capabilities: {}
    ```

    !!! tip "Speed vs Accuracy"
        Pytesseract is generally faster than EasyOCR but may have lower accuracy for complex text or non-standard fonts.

=== "Google Vision"

    **Purpose:** Google Cloud Vision API for text recognition. Requires API credentials.

    ```yaml
    text_detection:
      - google_vision:
          enabled: true
          url: null
          capabilities:
            credentials_path: "./config/google_credentials.json"
    ```

    !!! warning "API Credentials Required"
        You must provide valid Google Cloud credentials in the `credentials_path` capability.

=== "Remote OCR"

    **Purpose:** Remote OCR service for text extraction. Useful for distributed or cloud-based OCR.

    ```yaml
    text_detection:
      - remote_ocr:
          enabled: true
          url: "https://your-ocr-service.com/api/extract"
          capabilities: {}
    ```

---

## Image Detection

Image detection engines provide template matching capabilities for locating UI elements by image.

=== "Template Match"

    **Purpose:** OpenCV-based template matching for image recognition.

    ```yaml
    image_detection:
      - templatematch:
          enabled: true
          url: null
          capabilities:
            sift_nfeatures: 2000
    ```

    !!! note "Local Processing"
        Template matching runs locally using OpenCV and does not require external services.

    Matching is two-stage: a `cv2.matchTemplate` fast path handles exact
    same-scale matches, and a SIFT + FLANN + RANSAC fallback covers scaled or
    rotated instances. `capabilities.sift_nfeatures` (default `2000`) caps the
    SIFT features extracted per frame in that fallback, bounding per-call
    memory; raise it only if scaled matches fail on feature-poor screens.

=== "Remote OIR"

    **Purpose:** Remote Object Image Recognition (OIR) service for image-based element detection.

    ```yaml
    image_detection:
      - remote_oir:
          enabled: true
          url: "https://your-oir-service.com/api/match"
          capabilities: {}
    ```

---

## LLM Models

LLM backends power two opt-in features: the [`ai_self_heal`](#ai_self_heal) recovery path
above, and `optics live`'s natural-language mode (`Ctrl-N`). Both stay inert until an entry
below is enabled — the backend SDK is only imported when its engine is enabled, so leaving
this section out has no cost for projects that don't use either feature.

Each engine has an extra of the same name (`pip install 'optics-framework[openai]'`); the
`llm` extra installs all three. Keys are read from environment variables only. If you enable
more than one entry, a failed call falls through to the next one in list order.

| You have | Engine |
|----------|--------|
| A Gemini API key, or Vertex AI | `gemini` |
| An OpenAI key, OpenRouter, Azure OpenAI, or a local server (Ollama, vLLM, LM Studio) | `openai` |
| A Claude key, or Claude on Amazon Bedrock / Google Vertex AI | `anthropic` |

=== "Gemini"

    **Purpose:** Google Gemini via the `google-genai` SDK, supporting both the Gemini
    Developer API and Vertex AI. Install: `pip install 'optics-framework[gemini]'`.

    ```yaml
    llm_models:
      - gemini:
          enabled: true
          capabilities:
            model: gemini-3.8-flash    # optional; framework default is gemini-2.5-flash
            # temperature: 0.0         # optional; unset uses the model default (Gemini 3 recommends 1.0)
            # use_vertexai: true       # optional; else uses GOOGLE_GENAI_USE_VERTEXAI
            # project: your-project    # optional Vertex override; else GOOGLE_CLOUD_PROJECT
            # location: us-east4       # optional Vertex override; else GOOGLE_CLOUD_LOCATION
    ```

    With no `capabilities` set, the SDK auto-detects everything from environment variables:

    | Variable | Purpose |
    |----------|---------|
    | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini Developer API key |
    | `GOOGLE_GENAI_USE_VERTEXAI` | Switch to Vertex AI |
    | `GOOGLE_CLOUD_PROJECT` | Vertex AI project |
    | `GOOGLE_CLOUD_LOCATION` | Vertex AI location |
    | `GOOGLE_APPLICATION_CREDENTIALS` | Vertex AI service account credentials |

    !!! warning "Don't hardcode credentials"
        `capabilities.api_key` / `capabilities.gemini_api_key` are also accepted but log a
        warning when used — prefer the environment variables above so keys never end up in
        a committed `config.yaml`.

=== "OpenAI-compatible"

    **Purpose:** any server that speaks the OpenAI Chat Completions API, via the `openai`
    SDK. Without `url` it calls OpenAI; with one it calls that server instead.
    Install: `pip install 'optics-framework[openai]'`.

    ```yaml
    llm_models:
      - openai:
          enabled: true
          url: https://openrouter.ai/api/v1   # omit for OpenAI; or OPENAI_BASE_URL
          capabilities:
            model: anthropic/claude-sonnet-5-5  # required; the name the server uses
            api_key_env: OPENROUTER_API_KEY     # optional; default OPENAI_API_KEY
            # response_format: json_object     # for servers without JSON-schema output
            # max_tokens: 4096                  # optional cap per reply; unset = server default
            # temperature: 0.0
    ```

    | Server | `url` | Key |
    |--------|-------|-----|
    | OpenAI | (omit) | `OPENAI_API_KEY` |
    | OpenRouter | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` via `api_key_env` |
    | Azure OpenAI | `https://<resource>.openai.azure.com/openai/v1/` | your Azure key via `api_key_env` |
    | Ollama | `http://localhost:11434/v1` | none |
    | vLLM / LM Studio | the server's `/v1` URL | none |

    Natural-language mode and self-heal send screenshots, so pick a model that accepts
    images. Responses use strict JSON-schema output by default; if the server rejects it,
    set `response_format: json_object` and the schema is sent in the prompt instead.

=== "Anthropic"

    **Purpose:** Claude via the `anthropic` SDK, on the Claude API, Amazon Bedrock, or
    Google Vertex AI. Install: `pip install 'optics-framework[anthropic]'` (for Bedrock
    or Vertex, also `pip install 'anthropic[bedrock]'` or `'anthropic[vertex]'`).

    ```yaml
    llm_models:
      - anthropic:
          enabled: true
          capabilities:
            model: claude-sonnet-5-5   # required; Bedrock/Vertex use their own model IDs
            # platform: bedrock        # anthropic (default) | bedrock | vertex
            # region: us-east-1        # Bedrock or Vertex region
            # project: your-project    # Vertex project
            # max_tokens: 4096
            # temperature: 0.0
    ```

    | Platform | Credentials |
    |----------|-------------|
    | `anthropic` | `ANTHROPIC_API_KEY`, or the variable named by `api_key_env` |
    | `bedrock` | the standard AWS credential chain (`AWS_PROFILE`, `AWS_REGION`, …) |
    | `vertex` | Google application default credentials |

---

## Dependency Configuration Structure

All dependency types (driver sources, element sources, text detection, image detection) use the same `DependencyConfig` structure:

### `enabled`

**Type:** `bool` | **Required:** `true`

Whether this dependency is enabled. Only enabled dependencies are used by the framework.

### `url`

**Type:** `Optional[str]` | **Default:** `null`

Service URL for remote dependencies (e.g., Appium server, remote OCR service). Set to `null` for local dependencies.

### `capabilities`

**Type:** `Dict[str, Any]` | **Default:** `{}`

Dependency-specific configuration options. The structure varies by dependency type.

### Example Dependency Configuration

```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723/wd/hub"
      capabilities:
        automationName: "UiAutomator2"
        deviceName: "emulator-5554"
        platformName: "Android"
```

---

## How configuration is loaded

When you run `optics execute` or `optics dry_run`, the framework reads the target folder's own `config.yaml`. Any field you leave out falls back to the built-in defaults (for example, every source defaults to `enabled: false`). A project's `config.yaml` is the single source of truth for that run — there is no global config layer to merge.

---

## Best Practices

1. **Enable Only What You Need**: Disabled dependencies reduce overhead and improve performance
2. **Use Appropriate OCR**: Choose EasyOCR for accuracy, Pytesseract for speed
3. **Set Log Level Appropriately**: Use DEBUG during development, INFO or WARNING in production
4. **Configure Execution Paths**: Set `project_path` and `execution_output_path` for organized output
5. **Keep Each Project's `config.yaml` Self-Contained**: It is the only config the runner reads
6. **Test Configuration Changes**: Verify configurations work correctly before running large test suites
