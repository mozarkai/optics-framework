# Optics Framework

[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![Documentation](https://img.shields.io/badge/docs-Sphinx-blue)](docs/)

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
pip install --index-url https://pypi.org/simple/ --extra-index-url https://test.pypi.org/simple/ optics-framework
```

---

## 🚀 Quick Start

### 1 Create a New Test Project

**Note**: Ensure Appium server is running and a virtual Android device is enabled before proceeding.

```bash
mkdir ~/test-code
cd ~/test-code
python3 -m venv venv
source venv/bin/activate
pip install --index-url https://pypi.org/simple/ --extra-index-url https://test.pypi.org/simple/ optics-framework
```

### 2 Create a New Test Project

```bash
optics init --name my_test_project --path . --template youtube
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
optics execute <project_name> --test-cases <test_case_name>
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
  <li>clear_element_text</li>
  <li>detect_and_press</li>
  <li>enter_number</li>
  <li>enter_text</li>
  <li>get_text</li>
  <li>press_by_coordinates</li>
  <li>press_by_percentage</li>
  <li>press_element</li>
  <li>press_element_with_index</li>
  <li>press_keycode</li>
  <li>scroll</li>
  <li>scroll_from_element</li>
  <li>scroll_until_element_appears</li>
  <li>select_dropdown_option</li>
  <li>sleep</li>
  <li>swipe</li>
  <li>swipe_from_element</li>
  <li>swipe_until_element_appears</li>
</ul>
</details>
<details>
<summary><strong>🔹 AppManagement</strong></summary>

<ul>
  <li>close_and_terminate_app</li>
  <li>force_terminate_app</li>
  <li>get_app_version</li>
  <li>initialise_setup</li>
  <li>launch_app</li>
  <li>start_appium_session</li>
  <li>start_other_app</li>
</ul>

</details>

<details>
<summary><strong>🔹 FlowControl</strong></summary>

<ul>
  <li>_compute_expression</li>
  <li>_ensure_runner</li>
  <li>_evaluate_conditions</li>
  <li>_extract_element_name</li>
  <li>_extract_variable_name</li>
  <li>_is_condition_true</li>
  <li>_load_data</li>
  <li>_loop_by_count</li>
  <li>_loop_with_variables</li>
  <li>_parse_iterables</li>
  <li>_parse_single_iterable</li>
  <li>_parse_variable_iterable_pairs</li>
  <li>_parse_variable_names</li>
  <li>_resolve_condition</li>
  <li>_safe_eval</li>
  <li>_split_condition_args</li>
  <li>condition</li>
  <li>evaluate</li>
  <li>execute_module</li>
  <li>read_data</li>
  <li>run_loop</li>
</ul>

</details>

<details>
<summary><strong>🔹 Verifier</strong></summary>

<ul>
  <li>assert_equality</li>
  <li>assert_images_vision</li>
  <li>assert_presence</li>
  <li>assert_texts_vision</li>
  <li>is_element</li>
  <li>validate_element</li>
  <li>validate_screen</li>
  <li>vision_search</li>
</ul>

</details>


### Setup Development Environment

```bash
git clone <repo_url> :TODO: Add repo URL
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
poetry run sphinx-build -b html docs/ docs/_build/
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
1. MCP Servicer: Introduce a dedicated service to handle MCP (Multi-Channel Processing) tasks, improving scalability and modularity across the framework.
2. Omniparser Integration: Seamlessly integrate Omniparser to enable robust and flexible element extraction and location.
3. Playwright Integration: Add support for Playwright to enhance browser automation capabilities, enabling cross-browser testing with modern and powerful tooling.
4. Audio Support: Extend the framework to support audio inputs and outputs, enabling testing and verification of voice-based or sound-related interactions.

---

## 📄 License

This project is licensed under the **Apache 2.0 License**. See the [LICENSE](LICENSE)(:TODO: Add License Link) file for details.

---

## 📞 Support

:TODO: Add support information

Happy Testing! 🚀
