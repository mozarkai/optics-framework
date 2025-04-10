# CLI Guide

This section describes the available commands for the `optics-framework` CLI.

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

Run test cases with the following command:

```bash
optics execute <project_path> --test-cases <test_case_name> --runner <runner_name>
```

**Options:**

- `<project_path>`: Path to the project directory.
- `--test-cases <test_case_name>`: Path to the test cases file.
- `--runner <runner_name>`: Specify the test runner to use. [Current support: `test_runner` (default), `pytest`]

## Initializing a New Project

Use the following command to initialize a new project:

```bash
optics init --name <project_name> --path <directory> --template <sample_name> --git-init
```

**Options:**

- `--name <project_name>`: Name of the project.
- `--path <directory>`: Directory to create the project in.
- `--force`: Overwrite existing files if necessary.
- `--template <sample_name>`: Choose a predefined example.
- `--git-init`: Initialize a Git repository.

## Generating Code

**TODO**

Generate test automation code from an input CSV file:

```bash
optics generate <input_csv> --output <output_generated_code>
```

**Options:**

- `<input_csv>`: Path to the input CSV file.
- `--output <output_generated_code>`: Specify the output file.

## Listing Available Keywords

Display a list of all available keywords:

```bash
optics list
```

## Executing Dry Run

Execute a dry run of all test cases:

```bash
optics dry_run <project_path>
```

Execute a dry run of a specific test case:

```bash
optics dry_run <project_path> --test-case "<test-case-name>"
```

**Options:**

- `<project_path>`: Path to the project directory.
- `--test-case "<test-case-name>"`: Specify the test case to execute.

## Showing Help Information

Get help for the CLI:

```bash
optics-framework --help
```

## Managing Configuration

Set, reset, or list configuration values:

```bash
optics-framework config --set <key> <value> --reset --list
```

**Options:**

- `--set <key> <value>`: Set a configuration key-value pair.
- `--reset`: Reset all configurations to default.
- `--list`: Display current configuration values.

## Checking Version

Check the installed version of `optics-framework`:

```bash
optics-framework --version
```

## Additional Information

!!! info "Command Usage"
    All commands assume `optics-framework` is installed and accessible in your terminal. Use `pip install optics-framework` if not already installed.

!!! tip "Optional Parameters"
    Options like `--runner`, `--force`, and `--git-init` are optional. Omit them to use defaults (e.g., `test_runner` for `--runner`).

!!! warning "TODO Section"
    The `Generating Code` section is marked as **TODO**, indicating it’s not yet fully implemented or documented. Functionality may be limited.

!!! note "Driver Installation"
    When using `optics setup --install`, ensure `<driver_name1> <driver_name2> ...` matches available drivers listed by `optics setup --list`.

!!! tip "Dry Run Specificity"
    Use `--test-case "<test-case-name>"` with `optics dry_run` to test a single case without affecting others, ideal for debugging.

!!! info "Configuration Persistence"
    Changes made with `optics-framework config --set` persist across sessions unless reset with `--reset`.
