"""Beginner-facing onboarding output for the optics CLI.

Everything a newcomer sees on their first runs lives here: the welcome banner,
the first-run marker under ``~/.optics``, and the "now run:" block printed at
the end of project creation. Engineer B's CLI commands call these directly —
see ``quickstart.py`` for the guided flow that stitches them together.

The marker path is computed inside each function (not at import time) so that
tests can point ``HOME`` at a temporary directory.
"""
import json
import os

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from optics_framework.helper.version import VERSION

_console = Console()
_CMD_STYLE = "cyan bold"


def _marker_dir() -> str:
    """Directory holding the first-run marker.

    Honours ``OPTICS_HOME`` so constrained environments (read-only HOME,
    containers, CI) can point the marker at a writable location without
    touching ``~/.optics``. Falls back to ``~/.optics`` when unset."""
    override = os.environ.get("OPTICS_HOME")
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser("~"), ".optics")


def _marker_path() -> str:
    """Path of the first-run marker; resolved lazily so HOME is read at call
    time (tests monkeypatch it)."""
    return os.path.join(_marker_dir(), ".onboarded")


def is_first_run() -> bool:
    """True until a readable, version-stamped marker object exists.

    A missing marker means the user never onboarded; an unreadable, corrupt
    or shape-mismatched one (permissions changed mid-flight, truncated write,
    any parseable JSON that is not the object mark_onboarded writes) is
    treated as a first run rather than locking the user out of guidance
    forever."""
    try:
        with open(_marker_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return True
    return not isinstance(data, dict)


def mark_onboarded() -> None:
    """Write the first-run marker as ``{"version": <optics version>}``.

    Creates ``~/.optics/`` if needed. Best-effort by design: a read-only HOME
    must never break an optics command, so every filesystem error is swallowed.
    """
    try:
        os.makedirs(os.path.dirname(_marker_path()), exist_ok=True)
        with open(_marker_path(), "w", encoding="utf-8") as fh:
            json.dump({"version": VERSION}, fh)
    except OSError:
        pass


def welcome(first_run: bool = False) -> None:
    """Print the welcome banner shown by ``optics quickstart``.

    One line on what Optics is, then the golden path: ``optics quickstart``
    first, with ``optics doctor`` and ``optics --help`` as companions. When
    ``first_run`` is True a friendly greeting is prepended."""
    body = Text()
    if first_run:
        body.append("👋 First time here? We'll walk you through it.\n\n")
    body.append("Optics drives real phones and browsers with plain-English "
                "test steps — no code required.\n\n")
    body.append("Golden path:\n", style="bold")
    body.append("  optics quickstart", style=_CMD_STYLE)
    body.append("   Create a project, pick your platform, get a ready config.\n")
    body.append("  optics doctor", style=_CMD_STYLE)
    body.append("      Check your environment and project setup.\n")
    body.append("  optics --help", style=_CMD_STYLE)
    body.append("       See everything else optics can do.")
    _console.print(Panel(body, title="Optics Framework",
                         subtitle=f"v{VERSION}", border_style="cyan"))


def print_next_steps(project_path: str, *, configured: bool = False) -> None:
    """Print the "now run:" block pointing at the next CLI commands.

    With ``configured=False`` (a scaffolded project whose config.yaml still
    needs answers) the configure step comes first; once configured the user
    goes straight to validating and running their tests."""
    body = Text()
    if not configured:
        body.append("  optics configure", style=_CMD_STYLE)
        body.append(f" {project_path}\n", style="yellow")
        body.append("      Answer a few questions to generate config.yaml.\n")
    body.append("  optics dry_run", style=_CMD_STYLE)
    body.append(f" {project_path}\n", style="yellow")
    body.append("      Validate your test steps without touching a device.\n")
    if configured:
        body.append("  optics execute", style=_CMD_STYLE)
        body.append(f" {project_path}\n", style="yellow")
        body.append("      Run your tests for real.")
    else:
        body.rstrip()
    _console.print(Panel(body, title="Next steps", border_style="green"))
