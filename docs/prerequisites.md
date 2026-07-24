# Installation & Prerequisites

This page takes you from a clean machine to a verified Optics install. Do the
[Core install](#1-core-install) first; then add only the
[engine backend](#3-engine-backends) and
[platform tooling](#4-platform-tooling) for the kind of app you are testing.

---

## 1. Core install

Optics requires **Python 3.12 or newer**.

```bash
python3 --version            # must be 3.12+
python3 -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate
pip install optics-framework
```

!!! warning "Use a standard virtualenv, not Conda"
    `easyocr` and `optics-framework` have conflicting `numpy` requirements
    (1.x vs 2.x) under Conda. Use a plain `venv`.

---

## 2. Verify the core install

```bash
optics --version             # prints the installed version
optics list                  # prints the available keywords (reflection)
```

If both commands work, the CLI is installed correctly. The engines below are
what let it actually drive a device or browser.

---

## 3. Engine backends

The core install has **no drivers, OCR, or LLM backends** — they are optional
extras. Add them either as pip extras or with `optics setup`.

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

Two equivalent ways to install them:

```bash
# As pip extras (names match the config.yaml source keys)
pip install "optics-framework[appium,easyocr]"

# ...or interactively / by name
optics setup                       # TUI picker
optics setup --list                # list installable engines
optics setup --install appium easyocr
```

`optics setup` pins to your installed Optics version, so it never upgrades the
CLI out from under you. Convenience bundles also exist: `mobile`, `web`,
`vision`, `all`.

---

## 4. Platform tooling

A driver extra installs only the **Python client**. Each platform needs its own
external tooling.

### Android (Appium)

1. **Node.js** (LTS) — required by the Appium server.
2. **Appium server + UiAutomator2 driver:**
   ```bash
   npm install -g appium
   appium driver install uiautomator2
   appium                       # starts the server on http://localhost:4723
   ```
3. **Android SDK platform-tools** (provides `adb`) and a **JDK** (17+).
   Verify a device/emulator is visible:
   ```bash
   adb devices                  # your emulator/device should be listed
   ```
4. Point your project `config.yaml` at the device — see
   [Configuration](configuration.md). Match `deviceName`/`platformName` to
   `adb devices` and set `appPackage`/`appActivity` for your app.

### iOS (Appium)

Requires **macOS + Xcode** and the XCUITest driver
(`appium driver install xcuitest`). Follow the
[Appium XCUITest setup](https://appium.github.io/appium-xcuitest-driver/) for
signing and simulator/device provisioning.

### Web — Playwright

```bash
pip install "optics-framework[playwright]"
# optics setup --install playwright also runs the browser download for you:
playwright install --with-deps chromium
```

### Web — Selenium

Run a Selenium/WebDriver server (e.g. Selenium Grid or a standalone driver) and
set its URL in `config.yaml` (`http://localhost:4444/wd/hub` by default).

### OCR notes

- `pytesseract` needs a **system Tesseract binary** (`brew install tesseract`,
  `apt install tesseract-ocr`, etc.).
- `google-vision` needs Google Cloud credentials
  (`GOOGLE_APPLICATION_CREDENTIALS`).

### LLM notes (optional)

Natural-language `optics live` and AI self-heal use the `llm` extra
(`google-genai`). Credentials are read from the environment
(`GEMINI_API_KEY` / `GOOGLE_API_KEY`, or Vertex AI env vars). Never commit keys.

---

## 5. Next steps

You now have a working install. Continue with the
[Quick Start](quickstart.md) to create and run your first project.
