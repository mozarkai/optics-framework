# Configuration Reference

This document provides comprehensive documentation for all configuration options available in the Optics Framework. The configuration system is managed by the `ConfigHandler` class and uses YAML files for configuration.

## Overview

The Optics Framework uses a hierarchical configuration system that merges configurations in the following order (with later configurations taking priority):

1. **Default Configuration** - Built-in defaults
2. **Global Configuration** - `~/.optics/global_config.yaml` (user-wide settings)
3. **Project Configuration** - `config.yaml` in your project directory (project-specific settings)

!!! tip "Configuration Priority"
    Project configuration overrides global configuration, which overrides default configuration. This allows you to set common settings globally and override them per-project.

## Quick Reference

| Category | Key Settings | Common Values |
|----------|-------------|---------------|
| **Logging** | `console`, `file_log`, `json_log`, `log_level` | `log_level: INFO` or `DEBUG` |
| **Paths** | `project_path`, `execution_output_path` | `./my_project`, `./outputs` |
| **Execution** | `halt_duration`, `max_attempts` | `0.1`, `3` |
| **Drivers** | `appium`, `selenium`, `playwright`, `ble` | See Driver Sources tab |
| **Element Sources** | `appium_find_element`, `playwright_screenshot`, etc. | See Element Sources tab |
| **Text Detection** | `easyocr`, `pytesseract`, `google_vision` | See Text Detection tab |
| **Image Detection** | `templatematch`, `remote_oir` | See Image Detection tab |

## Configuration Structure

All configurations are defined in YAML format. The main configuration file (`config.yaml`) supports the following top-level sections:

- **Core settings** - Logging, paths, execution parameters
- **Driver sources** - Automation frameworks (Appium, Selenium, Playwright, BLE)
- **Element sources** - Element detection methods
- **Text detection engines** - OCR capabilities
- **Image detection engines** - Template matching

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

    Root directory for test project files. This path should contain your CSV files (`test_cases.csv`, `test_modules.csv`, `elements.csv`) and `input_templates/` directory.

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

    ### `max_attempts`

    **Type:** `int` | **Default:** `3`

    Maximum number of retry attempts for failing actions. The framework will retry up to this many times before reporting failure.

    ```yaml
    max_attempts: 3
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
          url: null  # Playwright runs locally
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
    | `headless` | `true`, `false` | Run browser in headless mode |
    | `viewport` | `{width, height}` | Browser viewport dimensions |

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
          capabilities: {}
    ```

    !!! note "Local Processing"
        Template matching runs locally using OpenCV and does not require external services.

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
    max_attempts: 3

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
    max_attempts: 5

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

## Configuration Priority and Merging

The Optics Framework merges configurations in the following order (later configurations override earlier ones):

1. **Default Configuration** - Built-in defaults from the `Config` class
2. **Global Configuration** - `~/.optics/global_config.yaml` (user-wide settings)
3. **Project Configuration** - `config.yaml` in your project directory

### Merging Behavior

- **Simple fields** (strings, numbers, booleans): Later values completely replace earlier values
- **Lists** (driver_sources, elements_sources, etc.): Items are merged, with later items taking precedence for duplicates
- **Dictionaries** (capabilities): Deep merged, with later values overriding earlier values

### Example Merging

**Global Config (`~/.optics/global_config.yaml`):**
```yaml
log_level: INFO
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723/wd/hub"
      capabilities:
        platformName: "Android"
```

**Project Config (`config.yaml`):**
```yaml
log_level: DEBUG
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723/wd/hub"
      capabilities:
        platformName: "Android"
        deviceName: "emulator-5554"
```

**Result:** The merged configuration will have `log_level: DEBUG` and the Appium capabilities will include both `platformName: "Android"` and `deviceName: "emulator-5554"`.

---

## Best Practices

1. **Enable Only What You Need**: Disabled dependencies reduce overhead and improve performance
2. **Use Appropriate OCR**: Choose EasyOCR for accuracy, Pytesseract for speed
3. **Set Log Level Appropriately**: Use DEBUG during development, INFO or WARNING in production
4. **Configure Execution Paths**: Set `project_path` and `execution_output_path` for organized output
5. **Use Global Config for Common Settings**: Store frequently used settings in `~/.optics/global_config.yaml`
6. **Leverage Configuration Priority**: Override global settings in project-specific configs when needed
7. **Test Configuration Changes**: Verify configurations work correctly before running large test suites
