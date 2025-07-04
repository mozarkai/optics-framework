import argparse
import sys
from typing import Optional
from pydantic import BaseModel
from optics_framework.helper.list_keyword import main as list_main
from optics_framework.helper.config_manager import main as config_main
from optics_framework.helper.initialize import create_project
from optics_framework.helper.version import VERSION
from optics_framework.helper.execute import execute_main, dryrun_main
from optics_framework.helper.generate import generate_test_file as generate_framework_code
from optics_framework.helper.setup  import DriverInstallerApp, list_drivers, install_packages, ALL_DRIVERS
from optics_framework.helper.serve import run_uvicorn_server
from optics_framework.helper.autocompletion import update_shell_rc

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
            "completion", help="Enable shell autocompletion for optics CLI"
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
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        server_args = ServerArgs(
            host=args.host,
            port=args.port
        )
        run_uvicorn_server(
            host=server_args.host,
            port=server_args.port
        )

class ConfigCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser("config", help="Manage configuration")
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        config_main()


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
        parser.add_argument("--name", required=True,
                            help="Project name (required)")
        parser.add_argument(
            "--path", help="Directory where the project will be created"
        )
        parser.add_argument(
            "--force", action="store_true", help="Override if the project exists"
        )
        parser.add_argument(
            "--template", help="Select a project template (e.g., 'sample1')"
        )
        parser.add_argument(
            "--git-init",
            action="store_true",
            help="Initialize a git repository for the project",
        )
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        init_args = InitArgs(
            name=args.name,
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
            "dry_run", help="Execute test cases from CSV files"
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


class VersionCommand(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "--version", help="Print the current version")
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        print(f"Optics Framework {VERSION}")

class DriverInstaller(Command):
    def register(self, subparsers: argparse._SubParsersAction):
        parser = subparsers.add_parser(
            "setup", help="Install driver for the project")
        parser.add_argument("--install", nargs="+",
                            help="Install specified drivers")
        parser.add_argument("--list", action="store_true",
                        help="List all available drivers")
        parser.set_defaults(func=self.execute)

    def execute(self, args):
        if args.list:
            list_drivers()
        elif args.install:
            driver_to_install = args.install
            invalid_drivers = [
                d for d in driver_to_install if d not in ALL_DRIVERS]
            if invalid_drivers:
                print(f"Error: Invalid driver(s): {', '.join(invalid_drivers)}")
                print("Use --list to see available drivers")
                return
            requirements = []
            for driver in driver_to_install:
                requirements.extend(ALL_DRIVERS[driver].packages)
            install_packages(requirements)
        else:
            DriverInstallerApp().run()


def main():
    """
    Main entry point for the Optics Framework CLI.

    This function sets up the argument parser, registers all commands, parses the
    command-line arguments, and dispatches the appropriate command function.
    """
    parser = argparse.ArgumentParser(
        prog="optics", description="Optics Framework CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Register all commands.
    commands = [
        ListCommand(),
        ConfigCommand(),
        DryRunCommand(),
        InitCommand(),
        ExecuteCommand(),
        VersionCommand(),
        GenerateCommand(),
        DriverInstaller(),
        ServerCommand(),
        AutocompletionCommand(),
    ]
    for cmd in commands:
        cmd.register(subparsers)

    args = parser.parse_args()

    try:
        args.func(args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
