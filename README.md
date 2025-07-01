# Optics Framework
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=mozarkai_optics-framework&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=mozarkai_optics-framework)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![Code Smells](https://sonarcloud.io/api/project_badges/measure?project=mozarkai_optics-framework&metric=code_smells)](https://sonarcloud.io/summary/new_code?id=mozarkai_optics-framework)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=mozarkai_optics-framework&metric=coverage)](https://sonarcloud.io/summary/new_code?id=mozarkai_optics-framework)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/10842/badge)](https://www.bestpractices.dev/projects/10842)


**Optics Framework** is a powerful, extensible no code test automation framework designed for **vision powered**, **data-driven testing** and **production app synthetic monitoring**. It enables seamless integration with intrusive action & detection drivers such as Appium / WebDriver as well as non-intrusive action drivers such as BLE mouse / keyboard and detection drivers such as video capture card and external web cams.

This framework was designed primarily for the following use cases:

1. Production app monitoring where access to USB debugging / developer mode and device screenshots is prohibited
2. Resilient self-healing test automation that rely on more than one element identifier and multiple fallbacks to ensure maximum recovery
3. Enable non-coders to build test automation scripts

---

## 🚀 Features

- **Vision powered detections:** UI object detections are powered by computer vision and not just on XPath elements.
- **No code automation:** No knowledge of programming languages or access to IDE needed to build automations scripts
- **Supports non-intrusive action drivers:** Non-intrusive action drivers such as BLE mouse and keyboard are supported
- **Data-Driven Testing (DDT):** Execute test cases dynamically with multiple datasets, enabling parameterized testing and iterative execution.
- **Extensible & Scalable:** Easily add new keywords and modules without any hassle.
- **AI Integration:** Choose which AI models to use for object recognition and OCR.
- **Self-healing capability:** Configure multiple drivers, screen capture methods, and detection techniques with priority-based execution. If a primary method fails, the system automatically switches to the next available method in the defined hierarchy

---

## 📦 Installation

### Install via `pip`

```bash
pip install optics-framework
```

---

## 🚀 Quick Start

### 1 Install Optics Framework

**Note**: Ensure Appium server is running and a virtual Android device is enabled before proceeding.

```bash
mkdir ~/test-code
cd ~/test-code
python3 -m venv venv
source venv/bin/activate
pip install optics-framework
```

> **⚠️ Important:** Conda environments are not supported for `easyocr` and `optics-framework` together, due to conflicting requirements for `numpy` (version 1.x vs 2.x). Please use a standard Python virtual environment instead.

### 2 Create a New Test Project

```bash
optics setup --install Appium EasyOCR
optics init --name my_test_project --path . --template contact
```

### 📌 Dry Run Test Cases

```bash
optics dry_run my_test_project
```

### 📌 Execute Test Cases

```bash
optics execute my_test_project
```

---

## 🛠️ Usage

### Execute Tests

```bash
optics execute <project_name>
```

### Initialize a New Project

```bash
optics init --name <project_name> --path <directory> --template <contact/youtube> --force
```

### List Available Keywords

```bash
optics list
```

### Display Help

```bash
optics --help
```

### Check Version

```bash
optics version
```

---

## 🏗️ Developer Guide

### Project Structure

```bash
Optics_Framework/
├── LICENSE
├── README.md
├── dev_requirements.txt
├── samples/            # Sample test cases and configurations
|   ├── contact/
|   ├── youtube/
├── pyproject.toml
├── tox.ini
├── docs/               # Documentation using Sphinx
├── optics_framework/   # Main package
│   ├── api/            # Core API modules
│   ├── common/         # Factories, interfaces, and utilities
│   ├── engines/        # Engine implementations (drivers, vision models, screenshot tools)
│   ├── helper/         # Configuration management
├── tests/              # Unit tests and test assets
│   ├── assets/         # Sample images for testing
│   ├── units/          # Unit tests organized by module
│   ├── functional/     # Functional tests organized by module

```

### Available Keywords

The following keywords are available and organized by category. These keywords can be used directly in your test cases or extended further for custom workflows.
<details>
<summary><strong>🔹 Core Keywords</strong></summary>

<ul>
  <li>
    <code>Clear Element Text (element, event_name=None)</code><br/>
    Clears any existing text from the given input element.
  </li>
  <li>
    <code>Detect and Press (element, timeout, event_name=None)</code><br/>
    Detects if the element exists, then performs a press action on it.
  </li>
  <li>
    <code>Enter Number (element, number, event_name=None)</code><br/>
    Enters a numeric value into the specified input field.
  </li>
  <li>
    <code>Enter Text (element, text, event_name=None)</code><br/>
    Inputs the given text into the specified element.
  </li>
  <li>
    <code>Get Text (element)</code><br/>
    Retrieves the text content from the specified element.
  </li>
  <li>
    <code>Press by Coordinates (x, y, repeat=1, event_name=None)</code><br/>
    Performs a tap at the specified absolute screen coordinates.
  </li>
  <li>
    <code>Press by Percentage (percent_x, percent_y, repeat=1, event_name=None)</code><br/>
    Taps on a location based on percentage of screen width and height.
  </li>
  <li>
    <code>Press Element (element, repeat=1, offset_x=0, offset_y=0, event_name=None)</code><br/>
    Taps on a given element with optional offset and repeat parameters.
  </li>
  <li>
    <code>Press Element with Index (element, index=0, event_name=None)</code><br/>
    Presses the element found at the specified index from multiple matches.
  </li>
  <li>
    <code>Press Keycode (keycode, event_name)</code><br/>
    Simulates pressing a hardware key using a keycode.
  </li>
  <li>
    <code>Scroll (direction, event_name=None)</code><br/>
    Scrolls the screen in the specified direction.
  </li>
  <li>
    <code>Scroll from Element (element, direction, scroll_length, event_name)</code><br/>
    Scrolls starting from a specific element in the given direction.
  </li>
  <li>
    <code>Scroll Until Element Appears (element, direction, timeout, event_name=None)</code><br/>
    Continuously scrolls until the target element becomes visible or the timeout is reached.
  </li>
  <li>
    <code>Select Dropdown Option (element, option, event_name=None)</code><br/>
    Selects an option from a dropdown field by visible text.
  </li>
  <li>
    <code>Sleep (duration)</code><br/>
    Pauses execution for a specified number of seconds.
  </li>
  <li>
    <code>Swipe (x, y, direction='right', swipe_length=50, event_name=None)</code><br/>
    Swipes from a coordinate point in the given direction and length.
  </li>
  <li>
    <code>Scroll from Element (element, direction, scroll_length, event_name)</code><br/>
    Scrolls starting from the position of a given element.
  </li>
  <li>
    <code>Swipe Until Element Appears (element, direction, timeout, event_name=None)</code><br/>
    Swipes repeatedly until the element is detected or timeout is reached.
  </li>
</ul>

</details>

<details>
<summary><strong>🔹 AppManagement</strong></summary>

<ul>
  <li>
    <code>Close And Terminate App(package_name, event_name)</code><br/>
    Closes and fully terminates the specified application using its package name.
  </li>
  <li>
    <code>Force Terminate App(event_name)</code><br/>
    Forcefully terminates the currently running application.
  </li>
  <li>
    <code>Get App Version</code><br/>
    Returns the version of the currently running application.
  </li>
  <li>
    <code>Initialise Setup</code><br/>
    Prepares the environment for performing application management operations.
  </li>
  <li>
    <code>Launch App (event_name=None)</code><br/>
    Launches the default application configured in the session.
  </li>
  <li>
    <code>Start Appium Session (event_name=None)</code><br/>
    Starts a new Appium session for the current application.
  </li>
  <li>
    <code>Start Other App (package_name, event_name)</code><br/>
    Launches a different application using the provided package name.
  </li>
</ul>

</details>


<details>
<summary><strong>🔹 FlowControl</strong></summary>

<ul>
  <li>
    <code>Condition </code><br/>
    Evaluates multiple conditions and executes corresponding modules if the condition is true.
  </li>
  <li>
    <code>Evaluate (param1, param2)</code><br/>
    Evaluates a mathematical or logical expression and stores the result in a variable.
  </li>
  <li>
    <code>Read Data (input_element, file_path, index=None)</code><br/>
    Reads data from a CSV file, API URL, or list and assigns it to a variable.
  </li>
  <li>
    <code>Run Loop (target, *args)</code><br/>
    Runs a loop either by count or by iterating over variable-value pairs.
  </li>
</ul>

</details>

<details>
<summary><strong>🔹 Verifier</strong></summary>

<ul>
  <li>
    <code>Assert Equality (output, expression)</code><br/>
    Compares two values and checks if they are equal.
  </li>
  <li>
    <code>Assert Images Vision (frame, images, element_status, rule)</code><br/>
    Searches for the specified image templates within the frame using vision-based template matching.
  </li>
  <li>
    <code>Assert Presence (elements, timeout=30, rule='any', event_name=None)</code><br/>
    Verifies the presence of given elements using Appium or vision-based fallback logic.
  </li>
  <li>
    <code>Assert Texts Vision (frame, texts, element_status, rule)</code><br/>
    Searches for text in the given frame using OCR and updates element status.
  </li>
  <li>
    <code>Is Element (element, element_state, timeout, event_name)</code><br/>
    Checks if a given element exists.
  </li>
  <li>
    <code>Validate Element (element, timeout=10, rule='all', event_name=None)</code><br/>
    Validates if the given element is present on the screen using defined rule and timeout.
  </li>
  <li>
    <code>Validate Screen (elements, timeout=30, rule='any', event_name=None)</code><br/>
    Validates the presence of a set of elements on a screen using the defined rule.
  </li>
  <li>
    <code>Vision Search (elements, timeout, rule)</code><br/>
    Performs vision-based search to detect text or image elements in the screen.
  </li>
</ul>

</details>


### Setup Development Environment

```bash
git clone git@github.com:mozarkai/optics-framework.git
cd Optics_Framework
pipx install poetry
poetry install --with dev
```

### Running Tests

```bash
poetry install --with tests
poetry run pytest
```

### Build Documentation

```bash
poetry install --with docs
poetry run mkdocs serve
```

### Packaging the Project

```bash
poetry build
```

---

## 📜 Contributing

We welcome contributions! Please follow these steps:

1. Fork the repository.
2. Create a new feature branch.
3. Commit your changes.
4. Open a pull request.

Ensure your code follows **PEP8** standards and is formatted with **Black**.

---

## 🎯 Roadmap

Here are the key initiatives planned for the upcoming quarter:

1. MCP Servicer: Introduce a dedicated service to handle MCP (Model Context Protocol), improving scalability and modularity across the framework.
2. Omniparser Integration: Seamlessly integrate Omniparser to enable robust and flexible element extraction and location.
3. Playwright Integration: Add support for Playwright to enhance browser automation capabilities, enabling cross-browser testing with modern and powerful tooling.
4. Audio Support: Extend the framework to support audio inputs and outputs, enabling testing and verification of voice-based or sound-related interactions.

---

## 📄 License

This project is licensed under the **Apache 2.0 License**. See the [LICENSE](https://github.com/mozarkai/optics-framework?tab=Apache-2.0-1-ov-file) file for details.

---

## 📞 Support

For support, please open an issue on GitHub or contact us at [@malto101], [@davidamo9] or [lalit@mozark.ai] .

Happy Testing! 🚀
