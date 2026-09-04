import argparse
import os
import sys
from typing import Literal, Optional
from pydantic import BaseModel
from optics_framework.helper.list_keyword import main as list_main
from optics_framework.helper.initialize import create_project, available_templates
from optics_framework.helper.version import VERSION
from optics_framework.helper.execute import execute_main, dryrun_main
from optics_framework.helper.live import live_main
from optics_framework.helper.generate import generate_test_file as generate_framework_code
from optics_framework.helper.setup import EngineInstallerApp, SetupError, list_engines, install_extras, resolve_engines, print_install_next_steps
from optics_framework.helper.serve import run_uvicorn_server
from optics_framework.helper.autocompletion import update_shell_rc
from optics_framework.helper.config_manager import configure as configure_project
from optics_framework.helper.onboarding import welcome, is_first_run, mark_onboarded
from optics_framework.helper.doctor import run_doctor
from optics_framework.helper.quickstart import run_quickstart


class Command:
    """
    Abstract base class for CLI commands.

    This abstract class defines the interface for CLI commands.
    Subclasses must implement the ``register`` and ``execute`` methods.

    :ivar logger: Optional logger instance.
    """

    def register(self, subparsers: argparse._SubParsersAction):
        """
        Register the command with the given subparsers.

        :param subparsers: The argparse subparsers object.
        :type subparsers: argparse._SubParsersAction
        :raises NotImplementedError: If the subclass does not implement this method.
        """
        raise NotImplementedError(
            "Subclasses must implement the `register` method.")

    def execute(self, args):
        """
        Execute the command using the provided arguments.

        :param args: The parsed command-line arguments (Pydantic model or argparse.Namespace).
        """
        raise NotImplementedError(
            "Subclasses must implement the `execute` method.")


class ListCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "list", help="List all available methods in the API"
        )
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        list_main()

class AutocompletionCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "completion",
            help="Enable shell autocompletion for optics CLI",
            description=(
                "Enable Tab-completion of optics commands and arguments in bash or zsh.\n"
                "\n"
                "Writes the completion scripts to ~/.optics/optics_completion.sh (bash) and\n"
                "~/.optics/optics_completion.zsh (zsh), then asks before appending a\n"
                '"# Optics CLI autocompletion" comment and one "source" line to the rc file\n'
                "for your $SHELL: ~/.bashrc for bash, ~/.zshrc for zsh. Declining the prompt,\n"
                "or running without an interactive terminal, leaves the rc file untouched and\n"
                "prints the line to add by hand instead.\n"
                "\n"
                "There is no uninstall flag: to undo, delete those two lines from the rc file\n"
                "and, if you want, remove ~/.optics/optics_completion.*."
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        update_shell_rc()

class GenerateArgs(BaseModel):
    """Arguments for the generate command."""
    project_path: str
    framework: str = "pytest"
    output_file: str|None = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.output_file is None:
            if self.framework == "robot":
                self.output_file = "test_generated.robot"
            else:
                self.output_file = "test_generated.py"


class GenerateCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "generate", help="Generate test framework code")
        parser.add_argument("project_path", help="Project name (required)")
        parser.add_argument(
            "--output",
            help="Path to the output file where the code will be generated",
            nargs="?",
        )
        parser.add_argument(
            "--framework",
            choices=["pytest", "robot"],
            default="pytest",
            help="Test framework to use for code generation (default: pytest)",
        )
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        generate_args = GenerateArgs(
            project_path=args.project_path, output_file=args.output, framework=args.framework)
        generate_framework_code(
            generate_args.project_path,
            generate_args.framework,
            generate_args.output_file,
        )

class ServerArgs(BaseModel):
    """Arguments for the server command."""
    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 1

class ServerCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "serve", help="Run the Optics Framework API server"
        )
        parser.add_argument(
            "--host", default="127.0.0.1", help="Host to bind the server (default: 127.0.0.1)"
        )
        parser.add_argument(
            "--port", type=int, default=8000, help="Port to bind the server (default: 8000)"
        )
        parser.add_argument(
            "--workers", type=int, default=1, help="Number of worker processes (default: 1)"
        )
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        server_args = ServerArgs(
            host=args.host,
            port=args.port,
            workers=args.workers
        )
        run_uvicorn_server(
            host=server_args.host,
            port=server_args.port,
            workers=server_args.workers
        )


class MCPArgs(BaseModel):
    """Arguments for the mcp command."""
    transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8090


class MCPCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "mcp", help="Run the Optics Framework MCP server (requires the 'mcp' extra)"
        )
        parser.add_argument(
            "--transport", choices=["stdio", "http"], default="stdio",
            help="MCP transport: stdio (default, local clients) or http"
        )
        parser.add_argument(
            "--host", default="127.0.0.1", help="Host to bind for http transport (default: 127.0.0.1)"
        )
        parser.add_argument(
            "--port", type=int, default=8090, help="Port to bind for http transport (default: 8090)"
        )
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        mcp_args = MCPArgs(
            transport=args.transport,
            host=args.host,
            port=args.port
        )
        # Lazy import so the optional 'mcp' extra (fastmcp) is only required
        # when this command actually runs.
        from optics_framework.helper.mcp_server import run_mcp_server
        run_mcp_server(
            transport=mcp_args.transport,
            host=mcp_args.host,
            port=mcp_args.port
        )

class ConfigureCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "configure", help="Edit a project's config.yaml (beginner prompts or --edit TUI)"
        )
        parser.add_argument(
            "folder", nargs="?", default=None,
            help="Project folder whose config.yaml to edit (default: current directory)",
        )
        parser.add_argument(
            "--edit", action="store_true",
            help="Launch the full-field Textual editor instead of the guided prompts",
        )
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        configure_project(folder=args.folder, edit=args.edit)


class ConfigCommand(Command):
    """Deprecated alias for ``optics configure``.

    ``optics config`` used to edit a global file the runner never read. It now
    forwards to the project-scoped guided configuration in the current directory
    with a deprecation notice, so existing muscle memory keeps working."""

    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "config", help="Manage configuration (deprecated; use `configure`)"
        )
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        print(
            "`optics config` is deprecated; use `optics configure [folder]` — "
            "answering a few questions to configure the project in the current directory."
        )
        configure_project(folder=os.getcwd(), edit=False)


class DoctorCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "doctor", help="Diagnose a project's setup (engines, config, tooling)"
        )
        parser.add_argument(
            "folder", nargs="?", default=None,
            help="Project folder to diagnose (default: current directory)",
        )
        parser.add_argument(
            "--check", action="store_true",
            help="Non-interactive: exit non-zero if any check fails",
        )
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        raise SystemExit(run_doctor(folder=args.folder, check=args.check))


class QuickstartCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "quickstart", help="Guided walkthrough to create and configure a first project"
        )
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        run_quickstart()


class InitArgs(BaseModel):
    """Arguments for the init command."""
    name: str
    path: Optional[str] = None
    force: bool = False
    template: Optional[str] = None
    git_init: bool = False


class InitCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser("init", help="Initialize a new project")
        parser.add_argument(
            "name_positional", nargs="?", default=None,
            help="Project name (positional, preferred)",
        )
        parser.add_argument("--name", dest="name_flag", default=None,
                            help="Project name (deprecated; pass it as a positional instead)")
        parser.add_argument(
            "--path", help="Directory where the project will be created"
        )
        parser.add_argument(
            "--force", action="store_true", help="Override if the project exists"
        )
        parser.add_argument(
            "--template",
            help=(
                "Start from a sample template: "
                + ", ".join(available_templates())
                + ". Omit to choose one from an interactive picker; with no "
                "interactive terminal, a blank project is created instead."
            ),
        )
        parser.add_argument(
            "--git-init",
            action="store_true",
            help="Initialize a git repository for the project",
        )
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        positional = args.name_positional
        flag = args.name_flag
        if positional is not None and flag is not None and positional != flag:
            raise ValueError(
                f"Conflicting project names: '{positional}' (positional) vs "
                f"'{flag}' (--name). Pass the name once."
            )
        if positional is not None:
            name = positional
        elif flag is not None:
            # Soft deprecation: keep --name working, nudge toward the positional.
            print(
                "Note: `--name` is deprecated; use a positional, e.g. "
                "`optics init myproject`."
            )
            name = flag
        elif sys.stdin.isatty():
            name = input("Project name: ").strip()
            if not name:
                raise ValueError("A project name is required.")
        else:
            name = sys.stdin.readline().strip()
            if not name:
                raise ValueError("A project name is required (pass it as a positional).")

        init_args = InitArgs(
            name=name,
            path=args.path,
            force=args.force,
            template=args.template,
            git_init=args.git_init
        )
        create_project(init_args)


class DryRunArgs(BaseModel):
    """Arguments for the dry_run command."""
    folder_path: str
    runner: str = "test_runner"
    use_printer: bool = True


class DryRunCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "dry_run", help="Validate a project's test cases without running them"
        )
        parser.add_argument(
            "folder_path", type=str, help="Path to the folder containing CSV files"
        )
        parser.add_argument(
            "--runner",
            type=str,
            default="test_runner",
            help="Test runner to use (default: test_runner)"
        )
        printer_group = parser.add_mutually_exclusive_group()
        printer_group.add_argument(
            "--use-printer",
            dest="use_printer",
            action="store_true",
            help="Enable live result printer (default)"
        )
        printer_group.add_argument(
            "--no-use-printer",
            dest="use_printer",
            action="store_false",
            help="Disable live result printer"
        )
        parser.set_defaults(func=self.execute, use_printer=True)

    def execute(self, args):
        dry_run_args = DryRunArgs(
            folder_path=args.folder_path,
            runner=args.runner,
            use_printer=args.use_printer
        )
        dryrun_main(
            dry_run_args.folder_path,
            dry_run_args.runner,
            use_printer=dry_run_args.use_printer
        )


class ExecuteArgs(BaseModel):
    """Arguments for the execute command."""
    folder_path: str
    runner: str = "test_runner"
    use_printer: bool = True


class ExecuteCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "execute", help="Execute test cases from CSV files"
        )
        parser.add_argument(
            "folder_path", type=str, help="Path to the folder containing CSV files"
        )
        parser.add_argument(
            "--runner",
            type=str,
            default="test_runner",
            help="Test runner to use (default: test_runner)"
        )
        printer_group = parser.add_mutually_exclusive_group()
        printer_group.add_argument(
            "--use-printer",
            dest="use_printer",
            action="store_true",
            help="Enable live result printer (default)"
        )
        printer_group.add_argument(
            "--no-use-printer",
            dest="use_printer",
            action="store_false",
            help="Disable live result printer"
        )
        parser.set_defaults(func=self.execute, use_printer=True)

    def execute(self, args):
        execute_args = ExecuteArgs(
            folder_path=args.folder_path,
            runner=args.runner,
            use_printer=args.use_printer
        )
        # Pass only required arguments for backward compatibility
        execute_main(
            execute_args.folder_path,
            execute_args.runner,
            use_printer=execute_args.use_printer
        )


class LiveArgs(BaseModel):
    """Arguments for the live command."""
    project_folder: Optional[str] = None


class LiveCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "live", help="Open an interactive session to run keywords against a live target"
        )
        parser.add_argument(
            "project_folder",
            type=str,
            nargs="?",
            default=None,
            help=(
                "Path to a project folder containing a config.yaml (with exactly one enabled "
                "driver_sources entry — appium/selenium/playwright — and at least one enabled "
                "elements_sources) plus elements. Defaults to the current directory."
            ),
        )
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        live_args = LiveArgs(project_folder=args.project_folder)
        live_main(live_args.project_folder)


class EngineInstaller(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "setup", help="Install optional engine backends (drivers/OCR/LLM)")
        parser.add_argument(
            "--install", nargs="+", metavar="NAME",
            help="Install the given engines, e.g. --install appium easyocr. "
                 "Append a version to pin it, e.g. --install appium==5.0.0")
        parser.add_argument("--list", action="store_true",
                        help="List all available engines")
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        if args.list:
            list_engines()
        elif args.install:
            try:
                engines, invalid = resolve_engines(args.install)
            except SetupError as exc:
                print(f"Error: {exc}")
                sys.exit(1)
            if invalid:
                print(f"Error: Invalid engine(s): {', '.join(invalid)}")
                print("Use `optics setup --list` to see available engines")
                sys.exit(1)
            success, message = install_extras(engines)
            print(message)
            if not success:
                sys.exit(1)
            print_install_next_steps()
        else:
            EngineInstallerApp().run()


def main():
    """
    Main entry point for the Optics Framework CLI.

    This function sets up the argument parser, registers all commands, parses the
    command-line arguments, and dispatches the appropriate command function.
    """
    parser = argparse.ArgumentParser(prog="optics", description="Optics Framework CLI")

    # Handle the --version argument directly on the main parser
    parser.add_argument(
        "--version",
        action="version",
        version=f"Optics Framework {VERSION}",
        help="Print the current version",
    )

    subparsers = parser.add_subparsers(dest="command")

    # Register all commands.
    commands = [
        ListCommand(),
        ConfigureCommand(),
        ConfigCommand(),
        DoctorCommand(),
        QuickstartCommand(),
        DryRunCommand(),
        InitCommand(),
        ExecuteCommand(),
        LiveCommand(),
        GenerateCommand(),
        EngineInstaller(),
        ServerCommand(),
        MCPCommand(),
        AutocompletionCommand(),
    ]
    for cmd in commands:
        cmd.register(subparsers)

    args = parser.parse_args()
    if hasattr(args, "func"):
        try:
            args.func(args)
        except KeyboardInterrupt:
            print("Operation cancelled by user.", file=sys.stderr)
            sys.exit(130)
        except argparse.ArgumentError as e:
            print(f"Argument error: {e}", file=sys.stderr)
            sys.exit(2)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(3)
        except Exception as e:
            print(f"Unexpected error ({type(e).__name__}): {e}", file=sys.stderr)
            print(
                "Run `optics doctor` to check your setup, "
                "or `optics <command> --help` for usage.",
                file=sys.stderr,
            )
            sys.exit(1)
    elif len(sys.argv) == 1:
        # Bare `optics`: greet first-timers with the full banner, then mark
        # them onboarded. Returning users get a one-line hint instead — they
        # already know the golden path. `optics --help` is handled by argparse
        # above and is left untouched.
        if is_first_run():
            welcome(first_run=True)
            mark_onboarded()
        else:
            print("Run `optics quickstart` to start a project, or `optics --help` for everything else.")


if __name__ == "__main__":
    main()
