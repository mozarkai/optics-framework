# Introduction

**Optics Framework** is a powerful, extensible no-code test automation framework designed for **vision-powered**, **data-driven testing** and **production app synthetic monitoring**. It enables seamless integration with intrusive action & detection drivers such as Appium / WebDriver as well as non-intrusive action drivers such as BLE mouse / keyboard and detection drivers such as video capture card and external web cams.

## Primary Use Cases

This framework was designed primarily for the following use cases:

1. **Production App Monitoring**
   Where access to USB debugging / developer mode and device screenshots is prohibited

2. **Resilient Self-Healing Test Automation**
   That rely on more than one element identifier and multiple fallbacks to ensure maximum recovery

3. **Enable Non-Coders to Build Test Automation Scripts**
   No programming knowledge required to create and execute tests

## Supported Platforms

- **Android** - Native Android app testing via Appium
- **iOS** - Native iOS app testing via Appium
- **Browsers** - Web application testing via Selenium or Playwright
- **Smart TVs** - Android TV (Android profile), Samsung Tizen, and LG webOS via Appium platform profiles
- **Non-intrusive targets** - Production devices driven as a Bluetooth HID mouse/keyboard, with capture via an external camera. Coordinate-driven only: the BLE driver exposes no page source, so element location relies on the OCR and image strategies

Each Appium platform carries its own options class, app-identifier capability names, and keycode-delivery strategy. TVs are navigated with D-pad remote keys rather than touch, so touch- and keyboard-based keywords raise `E0105` naming the unsupported platform instead of failing deep inside a driver call. See [Configuration](configuration.md) for per-platform setup.

## Key Features

### Vision Powered Detections

UI object detections are powered by computer vision and not just on XPath elements. This makes tests more resilient to UI changes.

### No Code Automation

No knowledge of programming languages or access to IDE needed to build automation scripts. Define tests as plain CSV or YAML data files.

### Non-Intrusive Action Drivers

Non-intrusive action drivers such as BLE mouse and keyboard are supported, enabling testing of production apps without developer mode.

### Data-Driven Testing (DDT)

Execute test cases dynamically with multiple datasets, enabling parameterized testing and iterative execution.

### Extensible & Scalable

Easily add new keywords and modules without any hassle. The modular architecture allows for easy extension.

### AI Integration

Choose which AI models to use for object recognition and OCR. Support for multiple vision models and OCR engines.

### Self-Healing Capability

Configure multiple drivers, screen capture methods, and detection techniques with priority-based execution. If a primary method fails, the system automatically switches to the next available method in the defined hierarchy.

### Interactive Sessions

`optics live` opens a terminal session that runs keywords one at a time against a live device or browser. Recording is always on, so an exploratory session can be saved straight back into `modules.csv` and `test_cases.csv` as a reusable module. See [Live Usage](usage/live_usage.md).

### Natural Language and AI Self-Heal

In `optics live`, `Ctrl-N` lets you describe a goal in plain English and have an LLM drive the keywords step by step. Separately, `ai_self_heal` acts as a last-resort backstop: when every location strategy fails for a keyword, an LLM reads the screen and attempts a bounded recovery. Both are opt-in and require an enabled `llm_models` entry.

### Agent and Service Interfaces

`optics mcp` exposes every keyword as a [Model Context Protocol](https://modelcontextprotocol.io) tool and device state as MCP resources, so an AI client can drive a real target. `optics serve` exposes the same engine over REST. See [MCP Usage](usage/mcp_usage.md) and [REST API Usage](usage/REST_API_usage.md).

### On-Screen Error Detection

Define crash dialogs, session timeouts, and network errors in an `error_definitions.csv` and Optics scans visible text for them without a single assertion. Matches are reported in the JUnit XML so CI fails the build. See [Error Detection](usage/error_detection.md).

## Architecture

Optics Framework offers a modular architecture paired with a command-line interface (CLI) that enables testers and developers to:

- Define test cases using CSV files
- Manage test data efficiently
- Execute tests with ease
- Extend functionality through plugins

## Who Can Use It?

Whether you're:

- A **beginner** looking to automate your first test
- An **experienced developer** contributing new features
- A **QA engineer** building comprehensive test suites
- A **DevOps engineer** setting up CI/CD pipelines

The Optics Framework is designed to empower you.

## License

The Optics Framework is licensed under the **Apache License 2.0**, which can be found [here](https://www.apache.org/licenses/LICENSE-2.0). This permissive license allows you to use, modify, and distribute the software freely, as long as you comply with its terms.

!!! info "License Key Points"

    - Redistributions of the code must include a copy of the license and any relevant notices
    - If you modify the code, you should also document your changes
    - The software is provided "as is" without any warranties
    - You can use, modify, distribute, and even sublicense the software with minimal restrictions

## Next Steps

Ready to get started? Run **`optics quickstart`** for a guided setup, or check out our [Getting Started Guide](getting-started.md) to create your first test in minutes!
