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
| **Natural-language agent** | `nl_agent` | `vision: auto`, `max_steps: 15` |

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

    List of test case names to include in execution. Only the specified test cases will be executed; all others will be skipped. Setup and teardown test cases always run.

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

    Names in `include` and `exclude` are matched case-insensitively, ignoring extra whitespace, and accept `*` and `?` wildcards. The two lists can be combined: `include` narrows the set and `exclude` then removes from it. A name that matches no test case stops the run with a suggestion instead of silently running nothing. The `--include` / `--exclude` flags of `optics execute` and `optics dry_run` replace both lists for a single run (see [Selecting test cases](usage/CLI_usage.md#selecting-test-cases)).

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

=== "Natural-Language Agent"

    ### `nl_agent`

    **Type:** `object` | **Default:** `{vision: auto, max_steps: 15, escalation_model: ""}`

    Settings of the natural-language mode of `optics live` (`Ctrl-N`). They have an effect
    only when an entry under `llm_models` is enabled.

    | Key | Meaning |
    |-----|---------|
    | `vision` | When the agent sends the screenshot. `auto` sends it on the first turn, after a step that code did not confirm, when the screen has few or unlabelled controls, and when no element list is available. `always` sends it on every turn. `never` sends no image, for text-only models. |
    | `max_steps` | The most actions the agent takes for one instruction. |
    | `escalation_model` | A model ID for the same engine as the first enabled `llm_models` entry. The agent sends hard turns to it: a second try after a bad reply, a turn after two failures in a row, and a turn with a guard note. Empty means one model for all turns. |

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

    !!! info "Idle wait before each command"
        Before each command, UiAutomator2 (Android) and XCUITest (iOS) wait for the app to
        stop changing. Their own limit is 10 seconds, so on a screen that never stops
        changing (a running timer, a focused input) every step takes 10 seconds or more.
        Optics sets the limit to 1 second. Your `appium:settings` merge with this default.
        To change the limit, set `waitForIdleTimeout`: Android takes milliseconds, iOS
        takes seconds, and `0` disables the wait on both:

        ```yaml
        capabilities:
          appium:settings:
            waitForIdleTimeout: 0
        ```

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
    | `channel` | `"chrome"`, `"chrome-beta"`, `"msedge"` | Launch that installed browser instead of Playwright's Chromium (local browser only) |
    | `profile` | The directory (`"Default"`, `"Profile 1"`), name or account email of a local Chrome or Edge profile | Start signed in with that profile's sign-ins. See "Start signed in with a local profile" below |
    | `user_data_dir` | A folder | Launch with this folder as the browser's user data directory, so sign-ins persist from run to run. Not with `profile` |
    | `viewport` | `{width, height}` | Browser viewport dimensions |
    | `device` | A Playwright device name, e.g. `"Pixel 7"`, `"iPhone 15"` | Emulate that phone or tablet: its screen, user agent and touch input. Pair an iPhone with `browser: webkit` for the Safari engine. Not with `context: existing` |
    | `storage_state` | Path to a file saved by Playwright, relative to the project folder | Start signed in, with the file's cookies and local storage. Works with `device` and `viewport`. Not with `context: existing` |
    | `headers` | `{name: value}` | HTTP headers sent when connecting to a remote browser |
    | `protocol` | `"cdp"`, `"playwright"` | How to connect to the remote browser in `url` (default `"cdp"`) |
    | `context` | `"new"`, `"existing"` | Open a fresh context in the remote browser, or use the one it already has (default `"new"`) |

    A local Chromium starts without the automation flags, so `navigator.webdriver` is `false`, as in a browser a person opens. Sites with bot protection then load as they do for a user. The Selenium driver starts Chrome the same way.

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

    **Start signed in with a local profile:** set `profile` to a Chrome or Edge profile on the machine that runs Optics. Optics copies the profile's cookies, local storage and preferences into a new folder, launches the installed browser (`channel`, or the profile's own browser) with the copy, and deletes the copy when the session ends. The profile itself is not changed, and the browser can stay open. `optics mcp` lists the profiles in the `optics://browser-profiles` resource, and a `profile` that matches none fails with `E0501` and lists them. Most site sign-ins carry over, but Google accounts are signed out in a copy. Not with a remote `url` or `storage_state`. A session started over an API takes `profile` only from a stdio `optics mcp` server and never takes `user_data_dir`.

    ```yaml
    driver_sources:
      - playwright:
          enabled: true
          capabilities:
            profile: "me@example.com"
    ```

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

    `${NAME}` references are read only from a project's `config.yaml`. `optics serve` and `optics mcp` reject them in a session request, so a caller cannot make the server send one of its environment variables to an endpoint the caller picked. A caller sends the values themselves in `url` and `capabilities.headers`.

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
    | **Samsung Tizen** | D-pad focus on the app's DOM; screenshots from a camera |
    | **LG webOS** | D-pad focus on the app's DOM |

    A Tizen or webOS app is a web page, so `appium_find_element` finds `text=` (visible text, `aria-label`, `title` or `alt`) and `css=` locators in its DOM. A TV has no touch, so the element keywords move the remote's focus instead:

    - `Press Element` presses arrow keys until the element has focus, then presses ENTER. Focus is read from the page after each key press, so you do not count presses.
    - `Enter Text` types into a real `<input>` directly. For a field the app draws itself, it walks the app's on-screen keyboard key by key, switches its layers (`123`, `abc`) when a character is not shown, and reads the field after. If the field does not show the text, the step fails with `E0405` and no fallback types it again. A letter case that the app picks itself (a capital first letter) gives only a warning.
    - `Clear Element Text` presses the keyboard's backspace key until the field stops changing.
    - `Get Text` reads the element's text or a field's value.
    - `Enter Text` with `<space>`, `<backspace>` or `<enter>` presses that key of the on-screen keyboard; `<escape>` and the arrows press remote keys.
    - Use `Assert Presence` to wait for a screen in place of `Sleep`.

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
      - appium_find_element:
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
      - appium_find_element:
          enabled: true
      - appium_screenshot:
          enabled: true
    ```

    **Limitations:**

    - Keywords that raise `E0105` on TVs: coordinate taps, swipes, scrolls, `Select Dropdown Option`, the checked state in `Is Element`, typing without an element, and `Go Back` (use `Press Keycode` with `BACK`). `Press Keycode` sends remote keys; an unknown key name fails with `E0104`.
    - Each focus move costs one remote key press (about 1–2 s on a TV), so typing a long text takes time.
    - A native screenshot hangs a Tizen TV, so `appium_screenshot` refuses it there: use `camera_screenshot`. DRM/video content appears black, so judge playback from UI elements.
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

    ### `appium_web`

    **Purpose:** Finds `css=` and DOM XPath locators in mobile browsers and WebViews on
    Android and iOS. See [Mobile Web](usage/mobile_web.md).

    ```yaml
    elements_sources:
      - appium_web:
          enabled: true
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
          capabilities:
            url: http://127.0.0.1:8080/screenshot   # or "ip:port" for raw TCP
            token: <bearer-token>                   # HTTP only
    ```

    | Capability | Description |
    |---|---|
    | `url` | `ip:port` (raw TCP, unchanged) or an `http://` / `https://` URL fetched with `GET`. |
    | `token` | Sent as `Authorization: Bearer <token>` to an HTTP url. Masked in logs; redirects are not followed. |
    | `camera_index` | Local webcam index, used instead of `url`. |

    !!! note "Environment fallback"
        When neither `url` nor `camera_index` is configured, the source reads
        `WEBCAM_SCREENSHOT_URL` and `WEBCAM_SCREENSHOT_TOKEN` (HTTP). If no URL is set but
        `WEBCAM_SCREENSHOT_TCP_PORT` is, it connects over raw TCP to `127.0.0.1:<port>`.
        `token` falls back to `WEBCAM_SCREENSHOT_TOKEN`. Explicit config always wins over env.

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

    `credentials_path` is a service account key file. Without it, the engine uses Application Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS` or `gcloud auth application-default login`). If the file is missing or is not a service account key, the run stops with `E0501` before the first test.

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

Every engine takes `capabilities.timeout_s` (default `60`): the seconds one request may take.
A request that takes longer fails like any other LLM error, so a stalled connection cannot hang
a run. The `openai` and `anthropic` SDKs retry a timed-out request twice before it fails.

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
            # credentials_path: ./credentials/vertex.json  # optional Vertex service account key
    ```

    `credentials_path` selects Vertex AI and uses the key's `project_id` as the project, unless `use_vertexai` or `project` say otherwise. The location defaults to `global`. If the file is missing or is not a service account key, the run stops with `E0501`.

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

### Environment variables and relative paths

Any string value in `config.yaml` can refer to an environment variable or to a file in the project. Use this for values that must not go into the file, such as an Appium hub URL that contains a token, and to keep a project that you copy or unzip somewhere else runnable:

```yaml
driver_sources:
  - appium:
      enabled: true
      url: $APPIUM_URL
      capabilities:
        udid: ${DEVICE_UDID:-emulator-5554}
        app: ./apps/app.apk
event_attributes_json: ./creds/event_attributes.json
```

```bash
export APPIUM_URL="https://hub.example.com/wd/hub?token=..."
optics execute my_project
```

- `$NAME` and `${NAME}` are replaced with the value of `NAME`. If `NAME` is not set or is empty, the run stops and tells you which variable it needs.
- `${NAME:-default}` uses `default` when `NAME` is not set or is empty.
- A reference can be part of a longer string, for example `https://${HUB_HOST}/wd/hub`. Write `$$` for a literal `$`.
- A value that starts with `./` or `../` is a path from the project folder (the folder you give to `optics execute`), not from the folder you run the command in. If the file is not there, the run stops and shows the full path it looked for. Output paths (`execution_output_path`, `log_path`, `json_path`, `project_path`) are resolved the same way but need not exist yet.
- An environment variable can hold a relative path: `$CREDS_FILE` set to `./creds/key.json` is resolved like the literal value.
- The result is always a string. Optics fields such as `enabled: ${USE_APPIUM}` accept `true` and `false`, but a capability that needs a number gets the number as text.

To take a whole map from one environment variable, put `$from` in the map and set the variable to a JSON object. The keys next to `$from` are added to that object and replace the keys it already has:

```yaml
driver_sources:
  - appium:
      enabled: true
      url: $APPIUM_URL
      capabilities:
        $from: $APPIUM_CAPABILITIES
        appium:appPackage: com.android.settings
        appium:appActivity: com.android.settings.Settings
```

```bash
export APPIUM_CAPABILITIES='{"platformName": "Android", "appium:automationName": "UiAutomator2", "appium:udid": "emulator-5554"}'
```

Values from the JSON object are used as they are: Optics does not expand references or resolve paths in them. If the value is not a JSON object, the run stops and names the variable, but does not show its value. Do not use `$from` in the Playwright `capabilities.headers`: the driver expands that map when it connects, so give each header on its own line.

Optics writes `****` in place of secrets in the console, the log files, `logs.json`, `junit_output.xml` and error messages. A text value in the config file is a secret when the environment variable or the key that holds it has a word such as `token`, `password`, `pwd`, `pin`, `secret`, `key`, `auth`, `bearer` or `credential` in its name (`HUB_TOKEN`, `appium:keystorePassword`). A key that ends in `_env`, such as `api_key_env`, holds the name of a variable, not a secret. The Python SDK and the `optics serve` and `optics mcp` sessions read no config file, so Optics masks these values where it logs their config. Passwords in URLs (`https://user:****@host`) and query parameters with such names (`?token=****`) are masked wherever they appear.

`optics execute`, `optics dry_run`, `optics live` and `optics doctor` resolve these values when they read the file. The Playwright driver's `url` and `capabilities.headers` are the exception: the driver expands them, with the same rules, when it connects, so it can mask the values in its error messages. An unset variable in them still stops the command when it reads the file. `optics configure --edit` keeps them as written when it saves the file. The Python SDK and the `optics serve` and `optics mcp` session APIs take the config as data, not as a file, and do not expand references.

---

## Best Practices

1. **Enable Only What You Need**: Disabled dependencies reduce overhead and improve performance
2. **Use Appropriate OCR**: Choose EasyOCR for accuracy, Pytesseract for speed
3. **Set Log Level Appropriately**: Use DEBUG during development, INFO or WARNING in production
4. **Configure Execution Paths**: Set `project_path` and `execution_output_path` for organized output
5. **Keep Each Project's `config.yaml` Self-Contained**: It is the only config the runner reads
6. **Test Configuration Changes**: Verify configurations work correctly before running large test suites
