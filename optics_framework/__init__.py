"""Optics Framework — self-healing test automation for mobile, web and TV.

The public Python SDK entry point is :class:`~optics_framework.optics.Optics`.
It is re-exported here so the documented import works::

    from optics_framework import Optics

The re-export is resolved lazily (:pep:`562`). Importing it eagerly would pull
the whole API and vision stack — including ``cv2`` — into *every* import of any
``optics_framework`` submodule, so a missing ``libGL.so.1`` crashed the
``optics`` console script before ``helper/cli.py`` could run a single line.

There is deliberately no star-export surface: a star import resolves every
name in ``__all__`` through :func:`__getattr__`, which would eager-import
``Optics`` and resurrect that crash on machines without the graphics library.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from optics_framework.optics import Optics  # noqa: F401 - type-checker-only re-export for the lazy facade

__all__: list[str] = []


def __getattr__(name: str) -> Any:
    if name == "Optics":
        from optics_framework.optics import Optics

        return Optics
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*globals(), "Optics"])
