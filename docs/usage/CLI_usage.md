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
optics setup --install appium==6.1.2 "easyocr>=1.7,<2.0"
```

The version applies to the engine's main package and is intersected with the extra's supported range, so an out-of-range pin fails loudly instead of silently downgrading. A malformed specifier (e.g. `appium=6.1.2` with a single `=`), two conflicting versions for the same engine, or a specifier on a bundle (e.g. `all==1.0`) are all rejected up front with a clear error.

After a successful install, Optics prints the recommended next steps (`optics init` → `optics configure` → `optics doctor`).

## Executing Test Cases

Run test cases from a project folder. The runner discovers test cases (and modules, elements, config) from that folder:

```bash
optics execute <folder_path> [--runner <runner_name>] [--use-printer | --no-use-printer]
               [--include <test_case> ...] [--exclude <test_case> ...]
```

**Options:**

- `<folder_path>`: Path to the project directory. The runner discovers test cases, modules, elements, and `config.yaml` by content, so either the sample subdir layout (`test_cases/`, `modules/`, `test_data/`) or flat CSVs work.
- `--runner <runner_name>`: Test runner to use. Supported: `test_runner` (default), `pytest`.
- `--use-printer` (default): Enable live result printer.
- `--no-use-printer`: Disable live result printer.
- `--include <test_case> ...`: Run only the named test cases. See [Selecting test cases](#selecting-test-cases).
- `--exclude <test_case> ...`: Skip the named test cases. See [Selecting test cases](#selecting-test-cases).

**Preflight check:** before running anything, `optics execute` verifies the enabled driver's backend is actually reachable — the Appium server (plus at least one attached Android device via `adb`) for Appium projects, or the remote WebDriver URL for Selenium projects. If something is missing it stops with the exact fix (e.g. "Start one in another terminal: `appium`") and a non-zero exit code instead of running tests that cannot reach a target. Set `OPTICS_SKIP_PREFLIGHT=1` to bypass this check.

**Failure details:** when any step fails, a *Failure details* panel is printed below the summary with each failing step and its reason; unknown steps include a `Did you mean '…'?` suggestion when a close keyword match exists.

### Selecting test cases

Run a subset of a project's test cases without editing any file:

```bash
# Only these test cases
optics execute ./my_project --include "Login Test" "Checkout Test"

# Everything except these
optics execute ./my_project --exclude "Legacy Flow"

# Wildcards, and both flags together: every login test except the slow one
optics execute ./my_project --include "login*" --exclude "Login Slow"
```

- **Names are matched case-insensitively** against the `test_case` column, and runs of whitespace count as a single space, so `"login   TEST"` selects `Login Test`. A name must match the whole test case name; it is not a substring search.
- **Wildcards:** `*` matches any run of characters and `?` matches exactly one. Every other character, including `[`, `]` and `.`, is literal.
- **Several names:** give several names after one flag (`--include A B`), repeat the flag (`--include A --include B`), or both. Put the project folder **before** the flags (or end the flag's list with `--`), otherwise the folder is read as another test case name.
- **Combining:** `--include` narrows the set first, then `--exclude` removes test cases from it.
- **Setup and teardown always run.** Suite setup/teardown and per-test setup/teardown test cases are kept for whichever tests are selected, and they cannot be selected or excluded by name.
- **Overrides config.yaml:** when either flag is given, the `include`/`exclude` lists in `config.yaml` are ignored for that run. Without flags, `config.yaml` applies as before.
- **Typos fail fast:** a name that matches no test case stops the run before any device or browser session starts, with a `Did you mean '…'?` suggestion and the list of available test cases. A selection that leaves nothing to run is rejected the same way.

The same flags work with [`optics dry_run`](#executing-dry-run), so you can check a selection before running it.

### Exit codes

`optics execute` and `optics dry_run` exit with:

| Code | Meaning |
|------|---------|
| `0` | Every test case passed. |
| `1` | A test case failed, or the run could not start: a config or environment variable error, an unknown `--include`/`--exclude` name, missing test files, an unreachable driver, or a credentials file that is not valid. |
| `2` | The command line is not valid, for example an unknown flag or no project folder. |
| `3` | Any other value that is not valid (a `ValueError`). |
| `130` | Stopped with Ctrl-C (SIGINT). |
| `143` | Stopped with SIGTERM. |

When the run cannot start, stderr shows a *Cannot start* panel titled with the error code, for example `Cannot start: E0501`. Other errors print `Code.<code>: <message>`. See [Error Handling](../architecture/error_handling.md) for the codes.

### Results

A run writes its results to `execution_output/` in the project folder, or to `execution_output_path` when the config sets it:

- `junit_output.xml`: the JUnit report, written once when the run ends, also when it is stopped with Ctrl-C or SIGTERM. A run has one `<testsuite>` with one `<testcase>` per test case it ran, including suite setup and teardown. Each per-test setup and teardown is a test case of its own, named after the test it wraps, such as `Setup (Login Test)`. The suite `name` and every `classname` are the project folder's name, so unpack a project into a folder named after the suite. The suite `id` is the session id.
- `junit_output_<session_id>.xml` instead, only when two sessions in one process share the output folder (the Python SDK or `optics serve`). `optics execute` and `optics dry_run` always write `junit_output.xml`.
- Screenshots, `failed_steps_elements.json`, `interactable_elements.json`, and `logs.json` when `json_log: true`.

| Outcome | Exit | `junit_output.xml` |
|---------|------|--------------------|
| All test cases passed | `0` | Every `<testcase>` has `status="PASS"`. |
| A test case failed | `1` | That `<testcase>` has `status="FAIL"` and a `<failure>` with the reason. |
| A per-test setup failed | `1` | `Setup (<test>)` has a `<failure>`. The test still runs and has its own result. |
| Config, environment variable or selection error | `1` | Not written: the run stops before a session starts. |
| Error after the session starts, such as a credentials file that is not valid | `1` | `<testsuites/>` with no test cases. |
| Stopped with Ctrl-C or SIGTERM | `130` / `143` | Test cases that finished keep their results. The one that was running has `status="ERROR"` and an `<error>`. Later test cases and the suite teardown do not run. |

A run where one test fails and the next is stopped with SIGTERM gives (keyword details cut):

```xml
<testsuites>
  <testsuite name="checkout_suite" id="2cadb1eb-a58e-429e-8685-7136c46e9db7" tests="7" failures="1" errors="1" skipped="0" time="4.30">
    <testcase name="Suite Setup" id="5e111a2d-..." classname="checkout_suite" time="1.47" status="PASS">...</testcase>
    <testcase name="Setup (TC1 Heading Shown)" id="2225c4bd-..." classname="checkout_suite" time="0.07" status="PASS">...</testcase>
    <testcase name="TC1 Heading Shown" id="0813321d-..." classname="checkout_suite" time="0.04" status="PASS">...</testcase>
    <testcase name="Setup (TC2 Missing Text)" id="234e032f-..." classname="checkout_suite" time="0.04" status="PASS">...</testcase>
    <testcase name="TC2 Missing Text" id="a4197485-..." classname="checkout_suite" time="2.64" status="FAIL">
      ...
      <failure message="Keyword 'Assert Presence' failed after fallback attempts; last error: Code.E0201: ['text=This text is not on the page'] not found based on rule 'any'. ..." type="Failure">...</failure>
    </testcase>
    <testcase name="Setup (TC3 Slow)" id="2c38f9a0-..." classname="checkout_suite" time="0.04" status="PASS">...</testcase>
    <testcase name="TC3 Slow" id="0ede90da-..." classname="checkout_suite" time="0" status="ERROR">
      ...
      <error message="The run stopped before this test case finished." type="Error">The run stopped before this test case finished.</error>
    </testcase>
  </testsuite>
</testsuites>
```

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
- `mobile_web` — Wikipedia search in Chrome on an Android device (Appium)

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
               [--include <test_case> ...] [--exclude <test_case> ...]
```

`optics dry-run` is the same command.

**Options:**

- `<folder_path>`: Path to the project directory.
- `--runner <runner_name>`: Test runner to use (default: `test_runner`).
- `--use-printer` (default): Enable live result printer.
- `--no-use-printer`: Disable live result printer.
- `--include <test_case> ...` / `--exclude <test_case> ...`: Validate only a subset of test cases. The rules are the same as for `optics execute` (see [Selecting test cases](#selecting-test-cases)).

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

Requires the `mcp` extra, which the installer always includes. With pip: `pip install "optics-framework[mcp]"`.

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
