"""Friendly startup-abort rendering shared by cli.py and execute.py."""

import shlex
import sys
from typing import NoReturn

from optics_framework.helper.environment import (EnvKind, detect,
                                                 project_add_command)


def reinstall_guidance() -> tuple[str, ...]:
    """Reinstall advice for the environment actually in use.

    A broken install is precisely the case where a hardcoded ``pip install`` is
    most likely to be wrong: uv and pipx environments have no pip to run, and a
    manager-owned one would discard the reinstall on its next sync."""
    env = detect()
    package = "optics-framework"
    if env.kind is EnvKind.TOOL:
        command = (f"pipx reinstall {package}" if env.manager == "pipx"
                   else f"uv tool install --reinstall {package}")
    elif env.kind is EnvKind.PROJECT:
        command = f"{project_add_command(env)} {package}"
    elif not env.has_pip and env.uv:
        command = (f"uv pip install --python {shlex.quote(env.python)} "
                   f"--reinstall {package}")
    else:
        command = f"pip install --force-reinstall {package}"
    return (
        "Reinstall the package and its dependencies, then re-run:",
        "",
        f"  {command}",
    )


def abort_with_panel(lines: list[str]) -> NoReturn:
    """Print ``lines`` in a red "Cannot start" panel on stderr, then exit 1.

    rich is imported lazily: when the install is broken enough that rich
    itself is missing, the caller must still get the guidance, not a
    second traceback.
    """
    try:
        from rich.console import Console
        from rich.panel import Panel

        Console(file=sys.stderr).print(
            Panel("\n".join(lines), title="Cannot start", border_style="red"))
    except ImportError:
        print("\n".join(lines), file=sys.stderr)
    sys.exit(1)
