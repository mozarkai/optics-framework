---
hide:
  - navigation
  - toc
---

# Optics Framework

Welcome to the official documentation for the **Optics Framework**, an open-source test automation framework designed to simplify and streamline the creation and execution of automated tests across various platforms. Whether you're testing mobile apps (including DRM-enabled ones), Optics Framework provides a flexible, extensible, and user-friendly solution to meet your testing needs.

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=mozarkai_optics-framework&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=mozarkai_optics-framework)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](https://github.com/mozarkai/optics-framework/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/10842/badge)](https://www.bestpractices.dev/projects/10842)

## Key Features

<div class="grid cards" markdown>

-   :material-eye-outline: **Vision Powered**

    UI object detections powered by computer vision, not just XPath elements

-   :material-code-braces: **No Code Automation**

    Build automation scripts without programming knowledge or IDE access

-   :material-bluetooth: **Non-Intrusive Drivers**

    Support for BLE mouse and keyboard for production monitoring

-   :material-database: **Data-Driven Testing**

    Execute test cases dynamically with multiple datasets

-   :material-puzzle: **Extensible & Scalable**

    Easily add new keywords and modules without hassle

-   :material-robot: **AI Integration**

    Choose which AI models to use for object recognition and OCR

-   :material-auto-fix: **Self-Healing**

    Automatic fallback to alternative detection methods when primary fails

-   :material-play-circle: **Live Sessions**

    Drive a live target interactively and record sessions straight into modules. See [Live Usage](usage/live_usage.md).

-   :material-connection: **MCP & Agents**

    Expose every keyword as an MCP tool so AI clients drive a real target. See [MCP Usage](usage/mcp_usage.md).

</div>

## Quick Start

From zero to a runnable test project with **one guided command**:

!!! info "Requires Python 3.12 or newer"
    Check first with `python3 --version`. On an older Python, install 3.12 — see [Installation](prerequisites.md) — then continue.

```bash
pip install optics-framework
optics quickstart
```

The wizard asks what you want to automate (Android, iOS, web…), installs the matching engine, scaffolds the project, writes a platform-correct `config.yaml`, and runs an environment check before printing your next steps.

??? tip "Prefer to drive each step yourself?"
    ```bash
    pip install optics-framework
    optics setup --install appium easyocr        # install the engines you need
    optics init my_test_project --template contact
    # edit my_test_project/config.yaml for your device, start Appium + emulator, then:
    optics dry_run my_test_project               # validate without a device
    optics execute my_test_project               # run it
    ```

    The [Getting Started Guide](getting-started.md) explains every step.

[Getting Started Guide](getting-started.md){ .md-button .md-button--primary } &nbsp; [Installation](prerequisites.md){ .md-button }

## Explore Documentation

<div class="grid cards" markdown>

-   :material-information: **Introduction**

    Learn about the framework's architecture and capabilities

    [:material-arrow-right: Introduction →](introduction.md)

-   :material-speedometer: **Getting Started**

    Set up Optics and run your first test in minutes — guided or step by step

    [:material-arrow-right: Getting Started →](getting-started.md)

-   :material-office-building: **Architecture**

    Deep dive into the framework's architecture, components, and design patterns

    [:material-arrow-right: Architecture →](architecture.md)

-   :material-routes: **User Workflow**

    Understand the typical workflow for creating and running tests

    [:material-arrow-right: User Workflow →](user_workflow.md)

-   :material-toolbox: **Usage**

    Comprehensive guides for CLI and keyword usage

    [:material-arrow-right: Usage →](usage/usage.md)

-   :material-api: **API Reference**

    Python API documentation and REST API usage guides

    [:material-arrow-right: API Reference →](api_reference.md)

-   :material-code-tags: **Developer Guide**

    Learn how to extend and contribute to the framework

    [:material-arrow-right: Developer Guide →](contribution/developer_guide.md)

-   :material-hand-heart: **Contributing**

    Guidelines for contributing to the project

    [:material-arrow-right: Contributing →](contribution/contributing_guidelines.md)

-   :material-help-circle: **Help Wanted**

    Areas where we need your help to improve the framework

    [:material-arrow-right: Help Wanted →](contribution/help_wanted.md)

-   :material-shield-account: **Code of Conduct**

    Our community standards and expectations

    [:material-arrow-right: Code of Conduct →](contribution/code_of_conduct.md)

</div>

## Need Help?

-   :material-github: [GitHub Issues](https://github.com/mozarkai/optics-framework/issues) - Report bugs or request features
-   :material-email: [Contact](mailto:lalit@mozark.ai) - Reach out for support

---

<div align="center">


</div>
