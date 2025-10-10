# How to use a config.yaml

## Driver Sources
Driver sources define the automation frameworks used to control devices or browsers.
### appium
**Mobile app testing (Android or iOS)**
```bash
driver_sources:
  - appium:
      enabled: true  #Set to false to disable
      url: "https://your-appium-server.com/wd/hub"
      capabilities:
            {
            automationName: UiAutomator2
            deviceName: emulator-5554
            platformName: Android
            }
```
### Key settings to update:

- platformVersion: Your Android/iOS version
- deviceName: Your device name
- udid: Your device's unique identifier (find with adb devices)
- url: Your Appium server address (usually localhost)

### selenium
**Web application testing**
```bash
driver_sources:
  - selenium:
      enabled: false  #Set to true to enable
      url: "http://localhost:4444/wd/hub"
      capabilities:
        {
        browserName: chrome
        browserURL: <website name>
        }
```

### ble
- What it does: Enables Bluetooth Low Energy (BLE) device automation.
```bash
driver_sources:
  - ble:
      enabled: false  #Set to true when needed
      url: null
      capabilities:
        {
          "device_id": "Samsung A50",
          "port": "/dev/ttyACM0",
          "x_invert": 1,
          "y_invert": 1,
          "pixel_width": 1080,
          "pixel_height": 2336,
          "mickeys_height": 2336,
          "mickeys_width": 1080
        }
```
## Element Sources
Element sources define methods for locating and capturing UI elements.

### appium_find_element
- What it does: Locates elements using Appium's element finding strategies.
```bash
elements_sources:
  - appium_find_element:
      enabled: true
      url: null
      capabilities: {}
```

### appium_page_source
- What it does: Retrieves the entire XML page source from Appium.
```bash
elements_sources:
  - appium_page_source:
      enabled: true
      url: null
      capabilities: {}
```

### appium_screenshot
- What it does: Captures screenshots through Appium.
```bash
elements_sources:
  - appium_screenshot:
      enabled: true
      url: null
      capabilities: {}
```

### camera_screenshot
- What it does: Captures images from a physical webcam.
```bash
elements_sources:
  - camera_screenshot:
      enabled: false
      url: null
      capabilities: {}
```

### selenium_find_element
- What it does: Locates web elements using Selenium strategies.
```bash
elements_sources:
  - selenium_find_element:
      enabled: false
      capabilities: {}
```

### selenium_screenshot
- What it does: Captures browser screenshots via Selenium.
```bash
elements_sources:
  - selenium_screenshot:
      enabled: false
      capabilities: {}
```
## Text Detection
Text detection engines extract text from images or screenshots.

### easyocr
- What it does: Uses EasyOCR library for optical character recognition.
```bash
text_detection:
  - easyocr:
      enabled: true
      url: null
      capabilities: {}
```
**Download easyocr to use this**
```bash
pip install easyocr
```

### pytesseract
- What it does: Uses Tesseract OCR engine for text recognition.
```bash
text_detection:
  - pytesseract:
      enabled: false
      url: null
      capabilities: {}
```
**Download pytesseract to use this**
```bash
pip install pytesseract
```

### google_vision
- What it does: Uses Google Cloud Vision API for text detection.
```bash
text_detection:
  - google_vision:
      enabled: false
      url: null
      capabilities: {}
```

### remote_ocr
- What it does: Connects to a remote Optical Image Recognition (OIR) service for advanced visual element detection.
```bash
text_detection:
  - remote_oir:
      enabled: false
      url: "https://your-oir-service.com/api/recognize"
      capabilities:{}
```

## Image Detection
Image detection methods for visual element recognition.

### templatematch
- What it does: Finds images within screenshots using template matching (OpenCV).
```bash
image_detection:
  - templatematch:
      enabled: true
      url: null
      capabilities: {}
```

### remote_oir
- What it does: Connects to a remote Optical Character Recognition (OCR) service for text extraction.
```bash
text_detection:
  - remote_ocr:
      enabled: false
      url: "https://your-ocr-service.com/api/extract"
      capabilities: {}
```

## Logging Configuration

### log_level
- What it does: Sets the verbosity of log messages.
1. DEBUG: Troubleshooting test failures, development
2. INFO: Normal test execution
3. WARNING: Only warnings and errors
4. ERROR: Only errors
5. CRITICAL: Only critical failures

```bash
log_level: DEBUG
```

### file_log
- What it does: Enables writing logs to a file.
```bash
file_log: true
log_path: "./logs/test_execution.log"  #Optional
```

### console
- What it does: Enables/disables console log output.
```bash
console: true
```

### json_log
- What it does: Outputs logs in JSON format.
```bash
json_log: true
json_path: "./logs/test_logs.json"
```

## Execution Settings
### halt_duration
- What it does: Pause duration (in seconds) between actions.
```bash
halt_duration: 0.1  # 100ms pause between actions
```

### max_attempts
- What it does: Number of retry attempts for failing actions.
```bash
max_attempts: 3  #Retry up to 3 times
```

### project_path
- What it does: Root directory for test project files.
```bash
project_path: "./tests"
```

## Best Practices ✅
- Enable only what you need: Disabled sources reduce overhead
- Use appropriate OCR: EasyOCR for accuracy, Pytesseract for speed
- Use DEBUG logging: Only during development, not in production
- Keep in mind config is priority based
