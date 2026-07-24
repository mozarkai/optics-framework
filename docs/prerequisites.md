# Installation & Prerequisites

This page is the reference for what to install: the core CLI, the optional **engine extras**, and the **external tooling** each target platform needs. If you just want to try Optics, follow the [Quick Start](quickstart.md) — come back here when you need the full list of extras or the tooling for a specific platform.

---

## Core install

Optics requires **Python 3.12 or newer**. Install the CLI into a standard virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate
pip install optics-framework
optics --version             # confirm the CLI is on your PATH
```

!!! warning "Use a standard virtualenv, not Conda"
    `easyocr` and `optics-framework` have conflicting `numpy` requirements (1.x vs 2.x) under Conda. Use a plain `venv`.

---

## Engine backends (extras)

The core install has **no drivers, OCR, or LLM backends** — they are optional extras. Add them either as pip extras or with `optics setup` (the names match the `config.yaml` source keys):

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

Convenience bundles also exist: `mobile`, `web`, `vision`, `all`.

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

With the CLI and the extras you need installed, continue with the [Quick Start](quickstart.md) to create and run your first project.
