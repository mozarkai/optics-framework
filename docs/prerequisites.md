# Installation

This page is the reference for what to install: the core CLI, the optional **engine extras**, and the **external tooling** each target platform needs. If you just want to try Optics, run `optics quickstart` instead — it picks and installs the right engine extra and verifies your environment for you (see [Getting Started](getting-started.md)). Come back here when you need the full list of extras or the tooling for a specific platform.

---

## Core install

The installer is the shortest path — it finds a suitable Python, builds a virtual environment under `~/.optics`, installs Optics into it and puts the CLI on your PATH.

=== "macOS / Linux"

    ```bash
    curl -fsSL https://optics-framework.org/install | sh
    ```

    Options are `--version`, `--extra appium,llm`, `--dir`, `--no-modify-path`, `--dry-run` and `--uninstall`; pass them after `sh -s --`. The `-f` matters: without it, curl would pipe an HTTP error page into your shell.

=== "Windows"

    ```powershell
    irm https://optics-framework.org/install.ps1 | iex
    ```

    Piping into `iex` passes no parameters, so the same options are environment variables — set them first:

    ```powershell
    $env:OPTICS_EXTRA = "appium,llm"
    irm https://optics-framework.org/install.ps1 | iex
    ```

    `OPTICS_VERSION`, `OPTICS_INSTALL_DIR` and `OPTICS_NO_MODIFY_PATH` work the same way. Downloaded as a file, the script takes `-Version`, `-Extra`, `-Dir`, `-NoModifyPath`, `-DryRun` and `-Uninstall` directly.

### Installing it yourself

Optics requires **Python 3.12 or newer**. To manage the environment yourself — in CI, or inside an existing project:

```bash
python3 -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate
pip install optics-framework
optics --version             # confirm the CLI is on your PATH
```

!!! warning "Use a standard virtualenv, not Conda"
    `easyocr` and `optics-framework` have conflicting `numpy` requirements (1.x vs 2.x) under Conda. Use a plain `venv`.

### Where Optics can install engine extras

`optics setup` and `optics quickstart` add engine extras to the environment Optics is running in. Not every environment allows that, so Optics checks first and tells you which command to use instead. `optics doctor` reports the same thing in its `environment` row, before you hit it.

| Your environment | `optics setup --install <engine>` | Add engines with |
|---|---|---|
| `python -m venv`, `virtualenv` | installs with pip | — |
| `uv venv` | installs with uv (these venvs have no pip) | — |
| `uv tool install` / `uvx` | refuses | `uv tool install "optics-framework[<engine>]"` |
| `pipx install` | refuses | `pipx install --force "optics-framework[<engine>]"` |
| uv / Poetry / PDM / Pipenv project | refuses | `uv add`, `poetry add`, `pdm add`, `pipenv install` |
| System Python on Debian 12+, Ubuntu 23.04+, Fedora, Arch, Homebrew | refuses ([PEP 668][pep668]) | create a venv first |
| Conda | refuses | use a plain `venv` (see the warning above) |

[pep668]: https://peps.python.org/pep-0668/

The refusals are deliberate. Tool installers rebuild their environment on every upgrade, and project managers prune anything their lockfile does not list — so an install that appeared to succeed would silently vanish on the next `uv tool upgrade` or `uv sync`.

!!! note "Tool installs give you the CLI, not the library"
    `pipx install optics-framework` and `uv tool install optics-framework` put the `optics` command on your PATH in an isolated environment. That covers `optics execute`, `optics live`, `optics serve` and `optics mcp`.

    They do **not** make `optics_framework` importable from your own code, so the [Python SDK](usage/library_usage.md) and [Robot Framework](usage/robot_usage.md) library (`Library    optics_framework.optics.Optics`) will not find it. For those, install into the same virtual environment your tests run in.

---

## Engine backends (extras)

The core install has **no drivers, OCR, or LLM backends** — they are optional extras. Add them either as pip extras or with `optics setup` (the names match the `config.yaml` source keys). Not sure which you need? `optics quickstart` offers the right one based on your target platform, and plain `optics setup` opens an interactive picker:

| Extra / `optics setup` name | Installs | Use for |
|---|---|---|
| `appium` | appium-python-client | Native Android/iOS |
| `selenium` | selenium | Web via a Selenium/WebDriver server |
| `playwright` | playwright (+ Chromium) | Web via Playwright |
| `ble` | pyserial | BLE mouse/keyboard action drivers |
| `easyocr` | easyocr | On-screen text detection (OCR) |
| `pytesseract` | pytesseract, pillow | OCR via a system Tesseract |
| `google-vision` | google-cloud-vision | OCR via Google Cloud Vision |
| `llm` | google-genai | Natural-language `optics live` + AI self-heal |
| `mcp` | fastmcp | `optics mcp` server |

```bash
# As pip extras
pip install "optics-framework[appium,easyocr]"

# ...or by name (optics setup pins to your installed Optics version)
optics setup --list                # list installable engines
optics setup --install appium easyocr
```

Convenience bundles also exist: `mobile`, `web`, `vision`, `all`. Append a version specifier to pin an engine (e.g. `optics setup --install appium==5.0.0`).

---

## External tooling

A driver extra installs only the **Python client**. Each platform also needs its own external tooling, which we don't bundle — install it from the vendor's own docs, since setup differs per OS:

- **Android (Appium):** [Node.js](https://nodejs.org/en/download), the [Appium server + UiAutomator2 driver](https://appium.io/docs/en/latest/quickstart/), the [Android platform tools](https://developer.android.com/tools/releases/platform-tools) (for `adb`), and a JDK (17+). Confirm a device is visible with `adb devices`.
- **iOS (Appium):** macOS + Xcode and the [Appium XCUITest driver](https://appium.github.io/appium-xcuitest-driver/).
- **Web (Playwright):** `optics setup --install playwright` runs the [browser download](https://playwright.dev/python/docs/browsers) for you.
- **Web (Selenium):** a running [Selenium/WebDriver server](https://www.selenium.dev/documentation/grid/); set its URL in `config.yaml`.
- **OCR (`pytesseract`):** a system [Tesseract binary](https://tesseract-ocr.github.io/tessdoc/Installation.html).
- **OCR (`google-vision`) / LLM (`llm`):** cloud credentials in the environment (`GOOGLE_APPLICATION_CREDENTIALS`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`). Never commit keys.

Point your project `config.yaml` at the device/browser once the tooling is running — see [Configuration](configuration.md).

---

## Next steps

With the CLI and the extras you need installed, continue with the [Getting Started](getting-started.md) guide to create and run your first project — or let `optics quickstart` do those steps for you.
