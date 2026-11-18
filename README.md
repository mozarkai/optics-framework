<div align="center">

<a href="https://optics-framework.org/?ref=readme"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/mozarkai/optics-framework/docs/readme-redesign/.github/assets/readme/hero-dark.webp">
  <img alt="Optics. Create tests your way. Run them forever. Write, record, say, delegate." src="https://raw.githubusercontent.com/mozarkai/optics-framework/docs/readme-redesign/.github/assets/readme/hero-light.webp" width="100%">
</picture></a>

[![PyPI](https://img.shields.io/pypi/v/optics-framework.svg)](https://pypi.org/project/optics-framework/)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](https://github.com/mozarkai/optics-framework/blob/main/LICENSE)
[![PyPI Downloads](https://img.shields.io/pypi/dm/optics-framework.svg)](https://pypi.org/project/optics-framework/)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=mozarkai_optics-framework&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=mozarkai_optics-framework)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=mozarkai_optics-framework&metric=coverage)](https://sonarcloud.io/summary/new_code?id=mozarkai_optics-framework)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/10842/badge)](https://www.bestpractices.dev/projects/10842)
[![Listed on mcpservers.org](https://mcpservers.org/badge.svg)](https://mcpservers.org/servers/mozarkai/optics-framework)

**[Docs](https://optics-framework.org/docs/?ref=readme)** · **[Install](https://optics-framework.org/docs/get-started/install?ref=readme)** · **[Quickstart](https://optics-framework.org/docs/get-started/quickstart?ref=readme)** · **[Playground](https://optics-framework.org/playground?ref=readme)** · **[Keywords](https://optics-framework.org/docs/reference/keywords/?ref=readme)**

</div>

Optics is open-source test automation for Android, iOS, web, smart TV and desktop apps. You write a test as data, in CSV or YAML. Each element can have more than one way to find it, and Optics tries them in order: XPath, text, OCR, image, then an optional AI recovery. When the app changes and one way breaks, the next way finds the element and the test continues.

## Try it in your browser

These four pages on optics-framework.org need no install. Click an image to open the page.

### Real Optics, in the browser

<a href="https://optics-framework.org/playground?ref=readme"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/mozarkai/optics-framework/docs/readme-redesign/.github/assets/readme/playground-dark.webp">
  <img alt="The playground runs the optics-framework wheel on Pyodide and executes a suite against the Birch shop app." src="https://raw.githubusercontent.com/mozarkai/optics-framework/docs/readme-redesign/.github/assets/readme/playground-light.webp" width="100%">
</picture></a>

[/playground](https://optics-framework.org/playground?ref=readme) loads the `optics-framework` wheel from PyPI on Pyodide. Edit the suite, break a locator, then run `dry_run` or `execute` on Birch, a demo shop app. The output and `junit_output.xml` come from Optics itself.

### `optics live`, next to a phone

<a href="https://optics-framework.org/?ref=readme#playground"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/mozarkai/optics-framework/docs/readme-redesign/.github/assets/readme/live-dark.webp">
  <img alt="A simulation of optics live: typed steps run on a phone, then /save writes them into the suite." src="https://raw.githubusercontent.com/mozarkai/optics-framework/docs/readme-redesign/.github/assets/readme/live-light.webp" width="100%">
</picture></a>

[The homepage playground](https://optics-framework.org/?ref=readme#playground) is a simulation of `optics live`. Type a step or tap the phone. Each step runs at once and shows how Optics found the element. `/save` writes the steps that passed into `modules.csv` and `elements.csv`. Under the phone, each step also shows as CSV, Python, Robot, an HTTP call and an MCP call.

### A real run, frame by frame

<a href="https://optics-framework.org/run?ref=readme"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/mozarkai/optics-framework/docs/readme-redesign/.github/assets/readme/run-dark.webp">
  <img alt="A replay of a real Optics run against two releases of Birch, with the screenshots and logs the run wrote." src="https://raw.githubusercontent.com/mozarkai/optics-framework/docs/readme-redesign/.github/assets/readme/run-light.webp" width="100%">
</picture></a>

[/run](https://optics-framework.org/run?ref=readme) replays a real run of the Birch suite against two releases of the app. Each frame is a file that the run wrote. Release 2.2 passes every step and fails the run, because the last screen shows "Session expired".

### Explain a suite

<a href="https://optics-framework.org/explain?ref=readme"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/mozarkai/optics-framework/docs/readme-redesign/.github/assets/readme/explain-dark.webp">
  <img alt="The suite explainer reads a suite row by row and shows what optics dry_run reports." src="https://raw.githubusercontent.com/mozarkai/optics-framework/docs/readme-redesign/.github/assets/readme/explain-light.webp" width="100%">
</picture></a>

[/explain](https://optics-framework.org/explain?ref=readme) reads a suite row by row. It shows what each step does, how each value resolves and what `optics dry_run` reports for it.

## Install

One line installs the `optics` command into its own environment under `~/.optics`:

```bash
curl -fsSL https://optics-framework.org/install | sh      # macOS, Linux
```

```powershell
irm https://optics-framework.org/install.ps1 | iex        # Windows
```

Then run `optics quickstart`. It asks what you want to test, installs the engine for it, makes a project, checks your setup and runs the first test. Each prompt has a default.

To use pip, you need Python 3.12 or newer. The core package has no drivers, OCR or AI engines. Add the extras that you need:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install "optics-framework[appium,easyocr]"
```

| Kind | Extras |
|---|---|
| **Drivers** | `appium` · `selenium` · `playwright` · `ble` |
| **OCR** | `apple-vision` · `rapidocr` · `easyocr` · `pytesseract` · `google-vision` |
| **AI** | `gemini` · `openai` · `anthropic` |
| **MCP server** | `mcp` |
| **Bundles** | `mobile` · `web` · `vision` · `llm` · `all` |

`optics setup --list` shows the same engines, and `optics setup --install appium easyocr` installs them for the Optics version you have.

> [!IMPORTANT]
> A driver extra installs only the Python client. A phone test also needs the Appium server, a device or emulator, and the platform tools. `pytesseract` needs the Tesseract program, and `playwright` needs its browsers (`playwright install`). See the [install guide](https://optics-framework.org/docs/get-started/install?ref=readme).

> [!WARNING]
> Do not use Conda with `easyocr`. Its NumPy version conflicts with the one that Optics needs. Use `venv`.

## Four ways to create a test

1. **Write it.** Elements, modules and test cases are plain CSV or YAML files. The same keywords are also a Python library and a Robot Framework library.
2. **Record it.** Run keywords one at a time in `optics live` next to your device. Each step that works is recorded, and `/save` writes the recording into your suite as a module.
3. **Describe it.** Press <kbd>Ctrl</kbd>+<kbd>N</kbd> in `optics live` and describe the flow. An AI model (Gemini, Claude, OpenAI or a local model) drives the device one step at a time, then keeps only the steps that mattered.
4. **Delegate it.** Run `optics mcp install` to register the MCP server with Claude Code, or add `optics mcp` to any MCP client. Your agent looks, taps, types and checks through the tools. When it is done, it gets the steps it ran as a suite.

## A test is data

A suite is three kinds of file. Optics finds each file by its CSV header or YAML keys, not by its folder.

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

**`elements.csv`** gives a name to each element. Give a name more than one row and Optics tries each value in order.

```csv
Element_Name,Element_ID
email_field,//android.widget.EditText[@resource-id="email"]
email_field,Email
sign_in,Sign in
sign_in,sign_in_button.png
```

A value can be an XPath, `text=…`, `css=…`, plain text, an image file from `input_templates/`, or `TEXT_ONLY:…` to search with OCR only.

**`modules.csv`** holds the steps, one keyword for each row. `${name}` reads a value from `elements.csv`.

```csv
module_name,module_step,param_1,param_2
Sign In,Enter Text,${email_field},ava@birch.shop
Sign In,Press Element,${sign_in}
```

**`test_cases.csv`** sets the modules that each test case runs, in order. A test case named `Suite Setup` or `Suite Teardown` runs before or after all the others.

```csv
test_case,test_step
Suite Setup,Launch Birch
Sign in and buy,Sign In
Sign in and buy,Add To Cart
```

## How Optics finds an element

Each keyword that acts on an element tries these ways in order and stops at the first one that works:

| # | Way | What it does |
|---|---|---|
| 1 | XPath | Queries the accessibility tree through the driver |
| 2 | Text | Finds the visible text, CSS or class in the page source |
| 3 | OCR | Reads the screenshot with Apple Vision, RapidOCR, EasyOCR, Tesseract, Google Vision or a remote OCR server |
| 4 | Image | Matches a reference picture of the element on the screenshot |
| 5 | AI self-heal (optional) | An LLM reads the screen and recovers with the same keywords |

The fast ways run first, so OCR and image matching cost time only when the tree cannot help. Two more fallbacks run next to this one: the values of one element name, and the drivers or element sources that you enable, in `config.yaml` order.

## Run a suite

```bash
optics dry_run my_test_project    # check keywords, parameters, elements and modules without a device
optics execute my_test_project    # run the suite
```

Each run writes to `my_test_project/execution_output/`:

- **`junit_output.xml`**, written once when the run ends, also when you stop the run.
- **Screenshots** of each step that finds an element, with a box around what Optics found.
- **Detected errors.** Put an `error_definitions.csv` in the project and Optics reads the last screen for crash dialogs, `Session expired` and other messages. A match adds a failure to the JUnit report, with no assertion. See [error detection](https://optics-framework.org/docs/run/error-detection?ref=readme).

`optics execute` exits with `1` when a test fails, so CI marks the build as failed. See [Run in CI](https://optics-framework.org/docs/run/ci?ref=readme) for GitHub Actions and GitLab.

## One suite, many targets

| Target | Driver |
|---|---|
| Android, iOS | `appium` with UiAutomator2 or XCUITest |
| Web | `playwright` or `selenium` |
| Android TV, Samsung Tizen, LG webOS | `appium` |
| macOS, Linux and Windows apps (Windows is experimental) | `appium` with appium-xa11y-driver |
| Production devices that block debugging and screenshots | `ble`: a Bluetooth dongle taps and types, and `camera_screenshot` reads the screen |

`config.yaml` sets the driver and how to find elements. Each section is a list in order of priority, and each entry has an `enabled` flag. Enable a second driver and it becomes a fallback.

```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723"
      capabilities:
        platformName: Android
        automationName: UiAutomator2
        deviceName: emulator-5554

elements_sources:
  - appium_find_element: { enabled: true }
  - appium_page_source:  { enabled: true }
  - appium_screenshot:   { enabled: true }

text_detection:
  - easyocr: { enabled: true }
```

`optics configure <folder>` writes this file from a few questions. See the [configuration reference](https://optics-framework.org/docs/reference/config?ref=readme) for every key, the AI engines and self-heal.

## Use the keywords from code

The same keywords work from each of these:

| Surface | Start it with |
|---|---|
| CLI runner | `optics execute <project>` |
| Interactive session | `optics live [project]` |
| Python | `from optics_framework import Optics` |
| Robot Framework | `Library    optics_framework.optics.Optics` |
| HTTP API | `optics serve` ([OpenAPI](https://optics-framework.org/openapi.json)) |
| MCP server | `optics mcp`, or `optics mcp install` for Claude Code |

<details>
<summary><b>Python example</b></summary>

```python
from optics_framework import Optics

optics = Optics()
optics.setup(
    driver_sources=[{"appium": {"enabled": True, "url": "http://localhost:4723"}}],
    elements_sources=[{"appium_find_element": {"enabled": True}}],
)

optics.launch_app(app_identifier="com.example.app")
optics.enter_text("username_field", "testuser")
optics.press_element("submit_button")
optics.validate_element("welcome_message")
optics.quit()
```

</details>

<details>
<summary><b>MCP client configuration</b></summary>

```json
{ "mcpServers": { "optics": { "command": "optics", "args": ["mcp"] } } }
```

The server gives the agent 11 tools: `start_session`, `observe`, `tap`, `type_text`, `scroll`, `press_key`, `launch_app`, `batch`, `check`, `run_keyword` and `terminate_session`. Each action replies with what changed on the screen. For a network client, run `optics mcp --transport http --port 8090`. See the [MCP reference](https://optics-framework.org/docs/reference/mcp?ref=readme).

</details>

## Keywords

In CSV and YAML, keywords are in Title Case (`Press Element`). In Python they are in snake case (`press_element`).

| Kind | Keywords |
|---|---|
| **Actions** | Press Element · Press By Percentage · Press By Coordinates · Detect And Press · Select Dropdown Option · Swipe · Swipe By Percentage · Swipe From Element · Swipe Until Element Appears · Scroll · Scroll From Element · Scroll Until Element Appears · Enter Text · Enter Text Direct · Enter Text Using Keyboard · Enter Number · Clear Element Text · Press Keycode · Go Back · Get Text · Sleep · Execute Script |
| **Checks** | Assert Presence · Assert Visibility · Assert Text · Assert Equality · Validate Element · Validate Screen · Is Element · Get Interactive Elements · Get Screen Elements · Capture Screenshot · Capture Pagesource |
| **App** | Launch App · Launch Other App · Start Appium Session · Initialise Setup · Get Driver Session Id · Close And Terminate App · Force Terminate App · Get App Version |
| **Flow** | Execute Module · Run Loop · Condition · Read Data · Evaluate · Date Evaluate · Invoke API |

`optics list` prints every keyword, and `optics list press_element` prints the parameters of one. The [keyword reference](https://optics-framework.org/docs/reference/keywords/?ref=readme) has an example for each.

## CLI

```text
optics quickstart   Make a first project, step by step
optics init         Make a project (--template for a sample)
optics configure    Write or edit config.yaml
optics doctor       Check engines, tools and project config (--check for CI)
optics setup        Install engines (--list, --install)
optics list         Print the keywords, or the parameters of one
optics dry_run      Check a project without a device
optics execute      Run a project (--include, --exclude)
optics live         Run keywords one at a time on a device
optics generate     Write pytest or Robot Framework code from a project
optics serve        Start the HTTP API
optics mcp          Start the MCP server (install registers it with Claude Code)
optics completion   Install shell completion
```

See the [CLI reference](https://optics-framework.org/docs/reference/cli?ref=readme) for every option.

## Contribute

Bug reports, docs and code are welcome. Larger tasks have the [`help wanted`](https://github.com/mozarkai/optics-framework/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) label. To report a bug or ask for a keyword, [open an issue](https://github.com/mozarkai/optics-framework/issues/new/choose).

```bash
git clone git@github.com:mozarkai/optics-framework.git
cd optics-framework
pipx install poetry
poetry install --with dev,test,docs
poetry run pytest
poetry run pre-commit run --all-files
```

The [contribution guide](https://github.com/mozarkai/optics-framework/blob/main/CONTRIBUTING.md) has the development setup, how to add a keyword or an engine, and the commit format. Report security issues as [SECURITY.md](https://github.com/mozarkai/optics-framework/blob/main/SECURITY.md) tells you, not in a public issue.

## License

Apache 2.0. See [LICENSE](https://github.com/mozarkai/optics-framework/blob/main/LICENSE).
