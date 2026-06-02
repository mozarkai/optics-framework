# CLI Guide

This section describes the available commands for the Optics Framework CLI. The command you run is **`optics`**; the package you install is **`optics-framework`** (e.g. `pip install optics-framework`).

## Setup Optics Framework

To set up the Optics Framework, use the following command:

To list all possible drivers:

```bash
optics setup --list
```

TUI way:

```bash
optics setup
```

CLI way:

```bash
optics setup --install <driver_name1> <driver_name2> ...
```

## Executing Test Cases

Run test cases from a project folder. The runner discovers test cases (and modules, elements, config) from that folder:

```bash
optics execute <folder_path> [--runner <runner_name>] [--use-printer | --no-use-printer]
```

**Options:**

- `<folder_path>`: Path to the project directory (contains `test_cases.csv`, `test_modules.csv`, `config.yaml`, etc.).
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

- `contact` — Contact/sample layout
- `clock` — Clock app sample
- `youtube` — YouTube sample
- `gmail_web` — Gmail web sample
- `calendar` — Calendar sample

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

## Serving the MCP Server

Run an MCP (Model Context Protocol) server that exposes every Optics keyword as a tool for AI clients (Claude Desktop, Claude Code, Cursor, claude.ai web):

```bash
optics mcp [--transport {http,stdio}] [--host <host>] [--port <port>] [--cors-origin <origin>]
```

**Options:**

- `--transport`: `http` (default) for hosted clients, `stdio` for local desktop clients.
- `--host`: Bind address (HTTP only; default `127.0.0.1`).
- `--port`: Listen port (HTTP only; default `8090`).
- `--cors-origin`: Allowed CORS origin (HTTP only; repeatable; default `*`).

Examples:

```bash
# Hosted clients (claude.ai web) — HTTP at http://127.0.0.1:8090/mcp
optics mcp

# Local clients (Claude Desktop, Claude Code, Cursor) — stdin/stdout
optics mcp --transport stdio
```

For client configuration, the full tool list, and end-to-end examples, see [MCP Server Usage](MCP_usage.md).

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

Manage framework configuration:

```bash
optics config [--set <key> <value>] [--reset] [--list]
```

**Options:**

- `--set <key> <value>`: Set a configuration key-value pair.
- `--reset`: Reset all configurations to default.
- `--list`: Display current configuration values.

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
    When using `optics setup --install`, use driver names listed by `optics setup --list`.

!!! info "Configuration persistence"
    Configuration changed with `optics config --set` persists until you run `optics config --reset`.
