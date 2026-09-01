"""Recognition of a driver session that the remote server has dropped.

A backend that talks to a remote server (an Appium/Selenium hub, a Playwright
browser) can have its session terminated server-side -- idle reaping, a hub
restart, a device being reclaimed -- while the client keeps a cached handle. The
next command then fails with a backend-specific exception that the element sources
and ``StrategyManager`` reinterpret as "element not found" (E0201/E0202/E0403).

Each driver owns which of its exceptions mean a dropped session
(``DriverInterface.is_dead_session_error``). The helpers here consult whichever
drivers are loaded and follow the exception chain, so the strategy layer can fail
fast with ``Code.E0106`` regardless of the backend in use.
"""

from __future__ import annotations

from optics_framework.common.error import Code, OpticsError
from optics_framework.common.logging_config import internal_logger

DEAD_SESSION_MESSAGE = (
    "Driver session is no longer active -- the remote server has dropped it. "
    "The framework does not reconnect automatically: terminate this session and "
    "start a new one."
)


def _loaded_driver_classes() -> list[type]:
    """Every currently-imported ``DriverInterface`` subclass, direct or indirect.

    Only drivers that have been imported appear, which is exactly the set that can
    have produced the exception under inspection: a session cannot fail before its
    driver is instantiated.
    """
    from optics_framework.common.driver_interface import DriverInterface

    found: list[type] = []
    seen: set[int] = set()
    stack: list[type] = list(DriverInterface.__subclasses__())
    while stack:
        cls = stack.pop()
        if id(cls) in seen:
            continue
        seen.add(id(cls))
        found.append(cls)
        stack.extend(cls.__subclasses__())
    return found


def _any_driver_recognizes(exc: BaseException) -> bool:
    """Whether any loaded driver treats ``exc`` as its own dropped-session signal."""
    for cls in _loaded_driver_classes():
        try:
            if cls.is_dead_session_error(exc):
                return True
        except Exception as predicate_error:  # noqa: BLE001 - a predicate must never mask the real error
            internal_logger.debug(
                "%s.is_dead_session_error raised while inspecting %r: %s",
                cls.__name__,
                exc,
                predicate_error,
            )
    return False


def is_dead_session_error(exc: BaseException | None) -> bool:
    """Whether ``exc``, or any exception it wraps, reports a terminated session.

    Lower layers re-raise the original failure in both styles -- ``raise ... from e``
    (sets ``__cause__``) and a bare ``raise`` inside an ``except`` block (sets only
    ``__context__``) -- so both links are followed.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OpticsError) and current.code == Code.E0106:
            return True
        if _any_driver_recognizes(current):
            return True
        current = current.__cause__ or current.__context__
    return False


def raise_if_session_dead(exc: BaseException) -> None:
    """Re-raise ``exc`` as ``Code.E0106`` when it means the driver session has died.

    Called from the broad ``except`` blocks that would otherwise degrade a dead
    session into an element-not-found or pagesource-missing result. ``E0106`` sits
    outside the ``E02*``/``X0201`` family on purpose: retrying other locators or
    element candidates can never revive a session, so it must fail fast.
    """
    if isinstance(exc, OpticsError) and exc.code == Code.E0106:
        raise exc
    if is_dead_session_error(exc):
        raise OpticsError(Code.E0106, message=DEAD_SESSION_MESSAGE, cause=exc) from exc


__all__ = ["DEAD_SESSION_MESSAGE", "is_dead_session_error", "raise_if_session_dead"]
