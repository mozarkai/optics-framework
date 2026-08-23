# CLI Guide

This section describes the available commands for the Optics Framework CLI. The command you run is **`optics`**; the package you install is **`optics-framework`** (e.g. `pip install optics-framework`).

## First run

Run `optics` with no arguments to see a welcome screen and the golden path:

```bash
optics
```

It points you at `optics quickstart` (guided setup), `optics doctor` (environment check), and `optics --help`. The first run also writes a marker at `~/.optics/.onboarded` so the greeting introduces itself only once; delete that file to see the first-run greeting again. If `~/.optics/` isn't writable (read-only HOME, containers, CI), set `OPTICS_HOME` to a directory that is — the marker lands there instead.

## Guided Setup: `optics quickstart`

New to Optics? One command walks you from nothing to a runnable project:

```bash
optics quickstart
```

It combines the commands below into one guided flow — welcome banner, engine install (`setup`), project scaffold (`init`), config Q&A (`configure`), and a doctor verification — then prints next steps tailored to your choices. See the [Getting Started Guide](../getting-started.md) for what each stage sets up.

## Setup: install engine backends

The core install ships without drivers, OCR, or LLM backends — they are optional extras. `optics setup` installs them by name (the names match the `config.yaml` source keys, e.g. `appium`, `easyocr`, `google-vision`), pinned to your installed Optics version.

List installable engines:

```bash
optics setup --list
```

Interactive picker (TUI):

```bash
optics setup
```

Install by name:

```bash
optics setup --install appium easyocr
```

This is equivalent to `pip install "optics-framework[appium,easyocr]"`. See [Installation](../prerequisites.md) for the full extras table and the external tooling (Appium server, adb, browsers) each engine needs.

Pin a specific version by appending a specifier to any engine (handy for reproducible CI):

```bash
optics setup --install appium==5.0.0 "easyocr>=1.7,<2.0"
```

The version applies to the engine's main package and is intersected with the extra's supported range, so an out-of-range pin fails loudly instead of silently downgrading. A malformed specifier (e.g. `appium=5.0.0` with a single `=`), two conflicting versions for the same engine, or a specifier on a bundle (e.g. `all==1.0`) are all rejected up front with a clear error.

After a successful install, Optics prints the recommended next steps (`optics init` → `optics configure` → `optics doctor`).

## Executing Test Cases

Run test cases from a project folder. The runner discovers test cases (and modules, elements, config) from that folder:

```bash
optics execute <folder_path> [--runner <runner_name>] [--use-printer | --no-use-printer]
```

**Options:**

- `<folder_path>`: Path to the project directory. The runner discovers test cases, modules, elements, and `config.yaml` by content, so either the sample subdir layout (`test_cases/`, `modules/`, `test_data/`) or flat CSVs work.
- `--runner <runner_name>`: Test runner to use. Supported: `test_runner` (default), `pytest`.
- `--use-printer` (default): Enable live result printer.
- `--no-use-printer`: Disable live result printer.

**Preflight check:** before running anything, `optics execute` verifies the enabled driver's backend is actually reachable — the Appium server (plus at least one attached Android device via `adb`) for Appium projects, or the remote WebDriver URL for Selenium projects. If something is missing it stops with the exact fix (e.g. "Start one in another terminal: `appium`") and a non-zero exit code instead of running tests that cannot reach a target. Set `OPTICS_SKIP_PREFLIGHT=1` to bypass this check.

**Failure details:** when any step fails, a *Failure details* panel is printed below the summary with each failing step and its reason; unknown steps include a `Did you mean '…'?` suggestion when a close keyword match exists.

## Initializing a New Project

Use the following command to initialize a new project:

```bash
optics init <project_name> [--path <directory>] [--template <sample_name>] [--git-init] [--force]
```

**Arguments and options:**

- `<project_name>`: Name of the project (positional). `--name <project_name>` still works but is deprecated.
- `--path <directory>`: Directory to create the project in (default: current directory).
- `--template <sample_name>`: Copy files from a predefined sample. See [Templates](#templates) below. Omitted interactively, you get a picker; with no TTY, a blank project is scaffolded.
- `--force`: Overwrite an existing project directory if it exists.
- `--git-init`: Initialize a Git repository in the project.

After scaffolding, Optics prints the next steps (`optics configure`, then `optics dry_run`). Blank projects (no `--template`) also get a pointer to where the first steps go — `test_cases/test_cases.csv` — and to `optics list` for every available step.

### Templates

Use `--template <name>` to copy a sample layout and assets from `optics_framework/samples/`. Available template names include:

- `contact` — Android Contacts sample (Appium)
- `clock` — Android Clock sample (Appium)
- `calendar` — Android Calendar sample (Appium; also shows an API collection)
- `youtube` — YouTube sample (Appium; uses image templates)
- `gmail_web` — Gmail web sample (Selenium)
- `playwright` — Minimal web sample (Playwright)

Exact values depend on the directories under `optics_framework/samples/`. Use only names that exist as subdirectories there.

## Generating Code

Generate test automation code from a project's test data (test cases, modules, config):

```bash
optics generate <project_path> [--output <output_file>] [--framework pytest|robot]
```

**Options:**

- `<project_path>`: Path to the project folder (containing test case and module data).
- `--output <path>`: Output file path. Defaults to `test_generated.py` (pytest) or `test_generated.robot` (robot).
- `--framework`: `pytest` (default) or `robot`.

## Listing Available Keywords

Display all available keywords and their parameters:

```bash
optics list
```

## Executing Dry Run

Validate test cases without executing actions (keyword and parameter checks):

```bash
optics dry_run <folder_path> [--runner <runner_name>] [--use-printer | --no-use-printer]
```

**Options:**

- `<folder_path>`: Path to the project directory.
- `--runner <runner_name>`: Test runner to use (default: `test_runner`).
- `--use-printer` (default): Enable live result printer.
- `--no-use-printer`: Disable live result printer.

Unknown steps and unresolved `${variables}` are flagged per step, with reasons collected in a *Failure details* panel below the summary (`Did you mean '…'?` suggestions included for close keyword matches). `optics dry_run` never touches a device — unlike `optics execute`, no driver preflight runs.

## Serving the REST API

Start the REST API server (e.g. for programmatic or remote use):

```bash
optics serve [--host <host>] [--port <port>] [--workers <n>]
```

**Options:**

- `--host`: Host to bind (default: `127.0.0.1`).
- `--port`: Port to bind (default: `8000`).
- `--workers`: Number of worker processes (default: `1`).

For endpoint details, request/response formats, and examples, see [REST API Usage](REST_API_usage.md).

## Interactive Live Session

Open an interactive session against a live target (device, browser, or TV) and run keywords as you go:

```bash
optics live [folder]
```

With a folder, Optics loads that project's `config.yaml`; without one, session defaults are used. Natural-language instructions are also available when an LLM engine is configured.

For the TUI, `/save` recording, and device commands, see [Live Usage](live_usage.md).

## MCP Server

Expose Optics keywords as Model Context Protocol tools so an LLM client (Claude Desktop, Cursor, ...) can drive sessions:

```bash
optics mcp [--transport {stdio,http}] [--host <host>] [--port <port>]
```

Requires the optional extra: `pip install "optics-framework[mcp]"`.

For setup and client configuration, see [MCP Usage](mcp_usage.md).

## Shell autocompletion

Enable shell autocompletion for the `optics` command:

```bash
optics completion
```

The completion scripts are always written under `~/.optics/`. With your confirmation, Optics also appends one `source` line to your shell RC (e.g. `.bashrc`, `.zshrc`) so commands and arguments complete when you press Tab; decline (or run without an interactive terminal) and it prints the line for you to add by hand instead — nothing is modified without an explicit yes.

## Showing Help Information

Get help for the CLI:

```bash
optics --help
```

## Configuring a Project

Answer a few questions and Optics writes the project's `config.yaml` for you:

```bash
optics configure [folder]
```

- `[folder]`: Project folder whose `config.yaml` to write (default: current directory).

For power users, `optics configure <folder> --edit` opens an interactive TUI over every config field instead (arrow keys to move, space to edit, `s` to save, `q` to quit). Edits always go to that project's own `config.yaml` — there is no global config file anymore.

!!! note "Deprecated: `optics config`"
    The old `optics config` command edited a global file the runner never read. It now prints a deprecation notice and forwards to the project editor for the current directory; use `optics configure` instead.

## Checking Your Setup

Diagnose engines, tooling, and a project's `config.yaml`:

```bash
optics doctor [folder] [--check]
```

- `[folder]`: Project folder to diagnose (default: current directory). With one, the Appium probe targets that project's configured server URL and its `config.yaml` is validated.
- `--check`: Non-interactive mode — exit non-zero when a project-config check fails (useful in CI).

## Checking Version

Check the installed version:

```bash
optics --version
```

## Additional Information

!!! info "Command name"
    The CLI command is **`optics`**. The PyPI package is **`optics-framework`**. Install with `pip install optics-framework`; then run `optics` in your terminal.

!!! tip "Optional parameters"
    Options such as `--runner`, `--force`, and `--git-init` are optional. Omit them to use defaults (e.g. `test_runner` for `--runner`).

!!! note "Driver installation"
    When using `optics setup --install`, use engine names listed by `optics setup --list` (e.g. `appium`, `easyocr`).
