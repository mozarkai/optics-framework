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


def abort_with_panel(lines: list[str], code: str | None = None) -> NoReturn:
    """Print ``lines`` in a red "Cannot start" panel on stderr, titled with ``code`` when given, then exit 1.

    rich is imported lazily: when the install is broken enough that rich
    itself is missing, the caller must still get the guidance, not a
    second traceback. Lines are rendered as plain text, not markup, since
    they carry test names, paths and exception messages verbatim.
    """
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text

        title = f"Cannot start: {code}" if code else "Cannot start"
        Console(file=sys.stderr).print(
            Panel(Text("\n".join(lines)), title=title, border_style="red"))
    except ImportError:
        print("\n".join([code, *lines] if code else lines), file=sys.stderr)
    sys.exit(1)
