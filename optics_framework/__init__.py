"""Optics Framework — self-healing test automation for mobile, web and TV.

The public Python SDK entry point is :class:`~optics_framework.optics.Optics`.
It is re-exported here so the documented import works::

    from optics_framework import Optics

The re-export is resolved lazily (:pep:`562`). Importing it eagerly would pull
the whole API and vision stack into *every* import of any ``optics_framework``
submodule, so any dependency that failed to import crashed the ``optics``
console script before ``helper/cli.py`` could run a single line — and before
its own guard could turn that into a readable message.

``Optics`` stays in ``__all__`` so ``from optics_framework import *`` keeps
binding it. That star import resolves the name through :func:`__getattr__` and
so does import the vision stack — but only for a caller that asked for the
facade, which is exactly the ``from optics_framework import Optics`` contract.
Plain ``import optics_framework`` and every submodule import stay lazy, which
is what keeps the console script reaching ``helper/cli.py``.

The one eager import is :mod:`~optics_framework.helper.console_encoding`, which
is stdlib-only and so costs nothing the laziness above is protecting against.
It runs here rather than in ``helper/cli.py`` because every entry point — the
console script, the Python SDK, the Robot Framework library, ``optics serve``
and ``optics mcp`` — reaches the package root, and only some of them reach the
CLI.
"""

from typing import TYPE_CHECKING, Any

from optics_framework.helper.console_encoding import ensure_console_encoding

if TYPE_CHECKING:
    from optics_framework.optics import Optics  # noqa: F401 - type-checker-only re-export for the lazy facade

ensure_console_encoding()

__all__: list[str] = ["Optics"]


def __getattr__(name: str) -> Any:
    if name == "Optics":
        from optics_framework.optics import Optics

        return Optics
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*globals(), "Optics"])
