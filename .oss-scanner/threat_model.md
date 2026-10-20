# Threat model

## What this project does and where untrusted input enters

`optics-framework` is a Python test-automation framework. Test suites are CSV/YAML files
(`test_cases/`, `modules/`, `test_data/`, `elements.csv`) that drive real devices and browsers
through Appium, Selenium, Playwright and BLE drivers, plus OCR (EasyOCR/Tesseract) and image
template matching. It also ships two network entry points:

- a local FastAPI server (`optics serve`, `optics_framework/common/expose_api.py`), which accepts
  session creation, keyword execution, template-image uploads and screenshots as base64/JSON;
- an MCP stdio/HTTP server (`optics_framework/helper/mcp_server.py`), which exposes every keyword as
  a tool to an agent.

**Trusted by design:** a test suite, `config.yaml`, and the project directory are written by the
operator. A suite is *meant* to tap coordinates, type text, launch apps, read files inside the
project, call APIs and run CLI helpers (`adb`, `idevice_id`, `git`). "The suite can do X to the
device" is the product, not a vulnerability.

**Untrusted:** anything the framework parses or is handed that the operator does not author:
page source (HTML/XML) and screenshots returned by a driver, API/JSON responses consumed by
`invoke_api`, uploaded template images and base64 payloads, OCR output, MCP tool arguments from an
agent, and HTTP requests when the server is reachable by anything other than the operator. Treat
the content of those artifacts as adversarial; the framework must not let them escape a parser,
a path, or an argv element.

## Components that matter most / least

Most important:

- `optics_framework/common/` — config loading, session manager, execution engine, workspace and
  output-path handling, `expose_api.py`.
- `optics_framework/helper/execute.py`, `config_manager.py`, `generate.py`, `mcp_server.py`,
  `cli.py` — project discovery, YAML/CSV parsing, path resolution, server startup.
- `optics_framework/api/flow_control.py` — the `eval`-based condition evaluator, `invoke_api`,
  date/expression handling.
- `optics_framework/engines/` — drivers, OCR and image matching, where device data is parsed.

Lower priority: `demo/`, `docs/`, `site/`, `scripts/`, and the reproducible example suites in
`optics_framework/samples/` (useful for exercising code, not attack surface of their own).

## How to exercise it

- `tests/units/` is hermetic (mocked collaborators, in-process HTTP mock server) and is the best
  place for a reproducer. Run `pytest -c pytest.toml --no-cov tests/units`.
- `tests/integration/test_playwright_journey.py` drives headless Chromium against a local fixture
  site (`tests/integration/web_fixture/`), so it works offline. `tests/integration/test_appium_app_to_browser.py`
  skips itself without an emulator.
- The API server can be started with `optics serve` and the MCP server with `optics mcp`
  (HTTP transport); both bind `127.0.0.1` by default.
- `optics init demo --template playwright` scaffolds a runnable project, but its element text comes
  from an external site. For offline work copy `tests/integration/web_fixture/` instead.

## How you rate severity

- **Critical:** code execution, or arbitrary file write/read outside the project and output
  directories, reachable from an HTTP/MCP request or from a parsed artifact (page source,
  screenshot, template image, API response, OCR text) without a malicious local config.
- **High:** the same impact when it needs local access or a third party to supply the crafted
  artifact; credential/secret disclosure (API keys, device credentials, session tokens) through
  logs, reports or execution output.
- **Medium:** SSRF or cross-origin reach from a browser into the local API/MCP server; denial of
  service from malformed input that crashes or wedges a run; symlink or path tricks that stay
  inside the project directory.
- **Low:** crashes that only affect a single keyword, log noise, or issues that need the operator to
  edit their own config maliciously.

An inflated rating is worse than a missing one: say which entry point and trust boundary you used,
and lower the severity when the precondition is "the operator wrote a hostile test suite".

## Anything to leave alone

- Test suites, modules, `config.yaml` and CLI flags doing what they say (running commands,
  launching apps, tapping, reading project files). Do not report these as injection or RCE without
  a boundary crossing.
- Third-party dependency bugs (Appium client, Selenium, Playwright, OpenCV, EasyOCR, FastAPI,
  fastmcp). Report those upstream; only report them here if optics-framework uses them in a way
  that turns a benign dependency into an escape.
- Emulator/device exploits, TLS or authentication of a server deliberately bound to `127.0.0.1`,
  and missing hardening in `demo/`, `docs/` or `scripts/`.
- DoS from an operator running a 10 GB screenshot on their own machine, unless it also corrupts
  state or escapes the output directory.

## Reports and patches

- Reproducer: a minimal failing `pytest` test (hermetic, offline) plus the exact keyword or HTTP/MCP
  call that reaches it. Name the entry point, the file it crosses, and the data it touches.
- Patch: the smallest change in the repo's existing style, with a regression test in `tests/units/`.
  Do not restructure a module or rename public keywords.
- Deduplicate by root cause: one report per underlying defect, listing the keywords and endpoints
  that reach it, rather than one report per caller.
