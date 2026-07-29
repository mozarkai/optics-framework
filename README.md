<div align="center">

# Optics Framework

**Self-healing test automation for mobile, web, TV — and AI agents.**

One keyword engine. Six ways to drive it: CSV/YAML files, a Python SDK, Robot Framework, a REST API, an interactive terminal, or an MCP server your AI agent talks to.

[![PyPI](https://img.shields.io/pypi/v/optics-framework.svg)](https://pypi.org/project/optics-framework/)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=mozarkai_optics-framework&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=mozarkai_optics-framework)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=mozarkai_optics-framework&metric=coverage)](https://sonarcloud.io/summary/new_code?id=mozarkai_optics-framework)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/10842/badge)](https://www.bestpractices.dev/projects/10842)

[Documentation](https://mozarkai.github.io/optics-framework/) · [Install](https://mozarkai.github.io/optics-framework/prerequisites/) · [Quick Start](https://mozarkai.github.io/optics-framework/quickstart/) · [Keywords](https://mozarkai.github.io/optics-framework/usage/keyword_usage/) · [Architecture](https://mozarkai.github.io/optics-framework/architecture/)

</div>

---

Most frameworks assume a UI element has one true locator, and a test breaks the moment that locator changes. Optics assumes an element has **several plausible identities** — its XPath, its visible text, what it looks like on screen — and tries all of them before giving up.

That idea runs through the whole framework: locators fall back, drivers fall back, element values fall back. Tests are data (CSV or YAML), so non-coders can write them, and the same keywords are reachable from Python, Robot Framework, HTTP, and MCP.

## Why Optics

**🔁 A locator ladder, not a locator.** Every element-based keyword walks a priority-ordered chain until one strategy succeeds:

| # | Strategy | How it finds the element |
|---|----------|--------------------------|
| 1 | `XPathStrategy` | Native XPath query through the driver's accessibility tree |
| 2 | `TextElementStrategy` | Direct text / CSS / class lookup through the element source |
| 3 | `TextDetectionStrategy` | Screenshot → OCR (EasyOCR, Pytesseract, Google Vision, remote OCR) |
| 4 | `ImageDetectionStrategy` | Screenshot → template matching against a reference PNG |

Cheap strategies run first, so vision only costs you time when the tree can't help. Two more fallback axes sit alongside it: **multiple values per element name** (encode "different XPath on Android 12 vs 14" in one row) and **multiple enabled drivers or element sources**, each tried in config order. If everything fails, an optional LLM step (`ai_self_heal`) inspects the screen and makes one bounded recovery attempt.

**📄 Tests are data.** Elements, modules, and test cases live in CSV or YAML — no IDE, no programming. File *content* decides how a file is read, not its extension, and multiple files of a kind are merged.

**📺 Phones aren't the limit.** Android and iOS via Appium, web via Selenium or Playwright, Android TV, Samsung Tizen, and LG webOS via platform profiles that swap in the right options class and remote-control key mapping. Touch-only keywords raise a clear `E0105` on TV rather than failing deep inside a driver call.

**🔌 Non-intrusive automation.** A BLE mouse/keyboard driver plus camera-based capture let you monitor **production** apps where USB debugging, developer mode, and screenshots are disabled by policy — including DRM-protected content.

**🤖 Agent-ready.** `optics mcp` exposes every keyword as a typed MCP tool and device state as MCP resources, so Claude, Cursor, or any MCP client can drive a real device.

## Install

Optics needs **Python 3.12+**. The core install ships no drivers, OCR, or LLM backends — you add only what you need:

```bash
python3 -m venv venv && source venv/bin/activate
pip install "optics-framework[appium,easyocr]"
```

Extra names match the `config.yaml` source keys, so the word you install is the word you enable:

| | |
|---|---|
| **Drivers** | `appium` · `selenium` · `playwright` · `ble` |
| **OCR** | `easyocr` · `pytesseract` · `google-vision` |
| **AI** | `llm` (natural-language mode + self-heal) · `mcp` (MCP server) |
| **Bundles** | `mobile` · `web` · `vision` · `all` |

Or install them by name — `optics setup` pins to your installed Optics version, and bare `optics setup` opens a TUI picker:

```bash
optics setup --list
optics setup --install appium easyocr
```

> [!IMPORTANT]
> A driver extra installs only the **Python client**. Mobile testing also needs the Appium server, a device/emulator, and platform tooling (Node.js, Android SDK/`adb`, JDK). See the [Installation & Prerequisites guide](https://mozarkai.github.io/optics-framework/prerequisites/).

> [!WARNING]
> Conda is not supported for `easyocr` + `optics-framework` together (conflicting NumPy 1.x/2.x requirements). Use a standard `venv`.

## Quickstart

```bash
optics init --name my_test_project --template contact
# point my_test_project/config.yaml at your device/app, start the Appium server, then:
optics dry_run my_test_project    # validate keywords, elements and module refs — no device needed
optics execute my_test_project
```

`--template` scaffolds a working project from a bundled sample: `contact`, `calendar`, `youtube` (Appium/Android), `clock` (Android + image templates), `gmail_web` (Selenium), `playwright` (Playwright). Omit it for an empty scaffold with a commented starter `config.yaml`.

## Write a test as data

```text
my_test_project/
├── config.yaml
├── test_cases/test_cases.csv
├── modules/modules.csv
└── test_data/
    ├── elements.csv
    ├── error_definitions.csv      # optional
    └── input_templates/*.png      # optional, for image matching
```

**`test_data/elements.csv`** — names mapped to locators. Doubles as a general variable store; repeating a name builds a fallback list.

```csv
Element_Name,Element_ID
Add_Contact_Button,//android.widget.Button[@content-desc="Create contact"]
First_Name_element,//android.widget.EditText[@text="First name"]
Save_Button,Save
First_Name,John
```

A locator can be an XPath, `text=…`, `css=…`, a plain string, an image filename from `input_templates/`, or `TEXT_ONLY:…` to force a vision-based search.

**`modules/modules.csv`** — a reusable sequence of keywords; `${name}` resolves against `elements.csv`.

```csv
module_name,module_step,param_1,param_2
Add Contact,Press Element,${Add_Contact_Button}
Add Contact,Enter Text,${First_Name_element},${First_Name}
Add Contact,Press Element,${Save_Button}
```

**`test_cases/test_cases.csv`** — modules sequenced into scenarios. A test case whose name contains *suite* + *setup* (or *teardown*) is hoisted to run around the whole suite.

```csv
test_case,test_step
Suite Setup,Launch Contact Application
Add Contact with Contact App,Add Contact
Add Contact with Contact App,Verify Contact is Added
```

## Six ways to run the same keywords

| Surface | Command / import | Best for |
|---------|------------------|----------|
| **CLI runner** | `optics execute <project>` | CI suites written as CSV/YAML |
| **Interactive TUI** | `optics live [project]` | Building a test by doing it — every action is recorded, `/save <name>` writes it back as a module. `Ctrl-N` for natural-language mode |
| **Python SDK** | `from optics_framework import Optics` | Custom logic, embedding in existing suites |
| **Robot Framework** | `Library  optics_framework.optics.Optics` | Teams already on Robot |
| **REST API** | `optics serve` | Remote/orchestrated execution, live workspace streaming over SSE |
| **MCP server** | `optics mcp` | Letting an AI agent drive a real device |

<details>
<summary><b>Python SDK example</b></summary>

```python
from optics_framework import Optics

optics = Optics()
optics.setup(
    driver_sources=[{"appium": {"enabled": True, "url": "http://localhost:4723"}}],
    elements_sources=[{"appium_find_element": {"enabled": True}}],
)

optics.launch_app("com.example.app")
optics.enter_text("username_field", "testuser")
optics.press_element("submit_button")
optics.validate_element("welcome_message")
optics.quit()
```

</details>

<details>
<summary><b>MCP client config</b></summary>

```json
{ "mcpServers": { "optics": { "command": "optics", "args": ["mcp"] } } }
```

Then: `start_session` → observe (`screenshot`, `optics://session/{id}/source`) → act (`press_element`, `enter_text`, …) → `terminate_session`. For networked use: `optics mcp --transport http --port 8090`. Sessions are **not** shared with `optics serve` — each is its own process.

</details>

## Keywords

Every public method on the four API classes is automatically a keyword, on every surface above. CSV/YAML uses Title Case (`Press Element` → `press_element`).

| Category | Representative keywords |
|----------|-------------------------|
| **Actions** | Press Element · Press By Percentage · Press By Coordinates · Detect And Press · Select Dropdown Option · Press Checkbox · Press Radio Button · Swipe · Swipe Until Element Appears · Scroll · Scroll Until Element Appears · Scroll From Element · Enter Text · Enter Number · Clear Element Text · Press Keycode · Get Text · Sleep · Execute Script |
| **Verification** | Assert Presence · Assert Visibility · Assert Equality · Validate Element · Validate Screen · Is Element · Get Interactive Elements · Get Screen Elements · Capture Screenshot · Capture Pagesource |
| **App lifecycle** | Launch App · Launch Other App · Start Appium Session · Get Driver Session Id · Close And Terminate App · Force Terminate App · Get App Version |
| **Flow control** | Run Loop · Condition · Read Data · Evaluate · Date Evaluate · Add API · Invoke API |

Run `optics list` for the live catalogue with signatures, or read the [Keyword Usage guide](https://mozarkai.github.io/optics-framework/usage/keyword_usage/) for parameters and examples. Location keywords accept percentage-based **Area-of-Interest** bounds (`aoi_x/y/width/height`, 0–100) to scope a vision search to part of the screen.

## Configure once, in `config.yaml`

Every section is a priority-ordered list and every entry has an `enabled` flag. Enable a second driver and it becomes a fallback.

```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723"
      capabilities:
        platformName: Android
        automationName: UiAutomator2
        deviceName: emulator-5554
        appPackage: com.google.android.contacts
        appActivity: com.android.contacts.activities.PeopleActivity

elements_sources:
  - appium_find_element: { enabled: true }
  - appium_page_source:  { enabled: true }
  - appium_screenshot:   { enabled: true }

text_detection:
  - easyocr: { enabled: true }

image_detection:
  - templatematch: { enabled: false }

ai_self_heal: false     # LLM recovery when every locate strategy fails
log_level: INFO
```

| Layer | Available engines |
|-------|-------------------|
| **Drivers** | `appium` (Android, iOS, Android TV, Tizen, webOS) · `selenium` · `playwright` · `ble` |
| **Element sources** | `appium_find_element` · `appium_page_source` · `appium_screenshot` · `selenium_*` · `playwright_*` · `camera_screenshot` |
| **Text detection** | `easyocr` · `pytesseract` · `google_vision` · `remote_ocr` |
| **Image detection** | `templatematch` · `remote_oir` |
| **LLM** | `gemini` |

Full reference: [Configuration](https://mozarkai.github.io/optics-framework/configuration/). Adding your own engine is a file drop plus an interface — see [Extending the Framework](https://mozarkai.github.io/optics-framework/architecture/extending/).

## Results

An `optics execute` run writes to `<project>/execution_output/`:

- **`junit_output.xml`** — written incrementally, so CI sees progress as it happens
- **`logs.json`** — structured logs when `json_log: true`
- **screenshots** — pre/post action frames, plus strategy-annotated and AOI overlays
- **`detected_errors_<session_id>.json`** — on-screen error detection

Drop an `error_definitions.csv` into `test_data/` and Optics scans visible text for crash dialogs, `Session expired`, network errors, and the like — no assertions required. Matches also land in the JUnit XML as a synthetic failing testcase, so CI fails a build on "the app crashed mid-test" the same way it fails a normal assertion. See [Error Detection](https://mozarkai.github.io/optics-framework/usage/error_detection/).

## CLI reference

```text
optics init        Scaffold a new project (--template, --path, --force, --git-init)
optics setup       Install engine backends (--list, --install); bare command opens a TUI
optics dry_run     Validate a project without touching a device
optics execute     Run a project (--runner test_runner|pytest)
optics live        Interactive keyword session against a live target
optics generate    Emit pytest or Robot Framework code from a project
optics list        Print every discoverable keyword
optics serve       Start the REST API server (--host, --port, --workers)
optics mcp         Start the MCP server (--transport stdio|http)
optics config      Manage global configuration (interactive)
optics completion  Install shell autocompletion
optics --version   Print the installed version
```

Details in the [CLI guide](https://mozarkai.github.io/optics-framework/usage/CLI_usage/).

## Contributing

```bash
git clone git@github.com:mozarkai/optics-framework.git
cd optics-framework
pipx install poetry
poetry install --with dev,test,docs

poetry run pytest                    # tests + coverage
poetry run ruff check --fix .        # lint
poetry run pre-commit run --all-files
poetry run mkdocs serve              # docs preview
```

Commits follow [Conventional Commits](https://www.conventionalcommits.org/), enforced by commitizen in the commit-msg hook. Read the [Contributing Guidelines](docs/contribution/contributing_guidelines.md), the [Developer Guide](docs/contribution/developer_guide.md), and our [Code of Conduct](CODE_OF_CONDUCT.md) before opening a PR. Looking for a place to start? See [Help Wanted](docs/contribution/help_wanted.md).

Security issues: please follow [SECURITY.md](SECURITY.md) rather than opening a public issue.

## License & support

Apache 2.0 — see [LICENSE](LICENSE).

Questions and bugs: [GitHub Issues](https://github.com/mozarkai/optics-framework/issues). Anything else: [lalit@mozark.ai](mailto:lalit@mozark.ai).

<div align="center"><sub>Built by <a href="https://mozark.ai">Mozark AI</a>. Happy testing 🚀</sub></div>
