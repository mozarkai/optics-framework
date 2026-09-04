"""Friendly startup-abort rendering shared by cli.py and execute.py."""

import sys
from dataclasses import dataclass
from typing import NoReturn

_LIBGL_MARKERS = ("libGL", "libgl")

_GRAPHICS_LIBRARY_GUIDANCE = (
    "OpenCV needs a system graphics library that minimal and headless",
    "Linux images do not ship. Install it, then re-run the command:",
    "",
    "  Debian/Ubuntu:  apt-get install -y libgl1",
    "  RHEL/Fedora:    dnf install -y mesa-libGL",
    "  Alpine:         apk add mesa-gl",
)

_REINSTALL_GUIDANCE = (
    "Reinstall the package and its dependencies, then re-run:",
    "",
    "  pip install --force-reinstall optics-framework",
)


@dataclass(frozen=True)
class ImportFailure:
    """A startup import error paired with the guidance that explains it."""

    error: ImportError
    guidance: tuple[str, ...]


def classify_import_error(exc: ImportError) -> ImportFailure:
    """Pair an import error with its fix guidance.

    OpenCV reports a missing system graphics library as a message substring
    rather than a structured attribute, so the markers are the only signal
    that survives across OpenCV versions.
    """
    if any(marker in str(exc) for marker in _LIBGL_MARKERS):
        return ImportFailure(exc, _GRAPHICS_LIBRARY_GUIDANCE)
    return ImportFailure(exc, _REINSTALL_GUIDANCE)


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
