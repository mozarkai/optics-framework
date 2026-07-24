# CLI Guide

This section describes the available commands for the Optics Framework CLI. The command you run is **`optics`**; the package you install is **`optics-framework`** (e.g. `pip install optics-framework`).

## Setup: install engine backends

The core install ships without drivers, OCR, or LLM backends — they are optional
extras. `optics setup` installs them by name (the names match the `config.yaml`
source keys, e.g. `appium`, `easyocr`, `google-vision`), pinned to your installed
Optics version.

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

This is equivalent to `pip install "optics-framework[appium,easyocr]"`. See
[Installation & Prerequisites](../prerequisites.md) for the full extras table and
the external tooling (Appium server, adb, browsers) each engine needs.

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

## Initializing a New Project

Use the following command to initialize a new project:

```bash
optics init --name <project_name> --path <directory> --template <sample_name> --git-init
```

**Options:**

- `--name <project_name>`: Name of the project (required).
- `--path <directory>`: Directory to create the project in (default: current directory).
- `--template <sample_name>`: Copy files from a predefined sample. See [Templates](#templates) below.
- `--force`: Overwrite an existing project directory if it exists.
- `--git-init`: Initialize a Git repository in the project.

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

## Shell autocompletion

Enable shell autocompletion for the `optics` command:

```bash
optics completion
```

This updates your shell RC (e.g. `.bashrc`, `.zshrc`) so that commands and arguments are completed when you press Tab.

## Showing Help Information

Get help for the CLI:

```bash
optics --help
```

## Managing Configuration

```bash
optics config
```

This opens an interactive TUI for editing the **global** config at
`~/.optics/global_config.yaml` (arrow keys to move, space to edit, `s` to save,
`q` to quit). It takes no flags.

!!! warning "Global vs per-project config"
    `optics config` edits the **global** file. `optics execute <folder>` reads
    that folder's **own** `config.yaml`, which does **not** merge the global
    file. For a specific project, edit its `config.yaml` directly — that is the
    config the runner uses.

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
