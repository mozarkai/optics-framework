"""Recognition of a driver session that the remote server has dropped.

An Appium/Selenium hub can terminate a session server-side -- idle reaping, a hub
restart, a device being reclaimed -- while the client keeps its cached
``session_id``. Every subsequent command then fails with
``InvalidSessionIdException``, which the element sources and ``StrategyManager``
would otherwise reinterpret as "element not found" (E0201/E0202/E0403), sending
users to debug locators that were never the problem.

The helpers here identify that exception even after a lower layer has re-wrapped
it, so the strategy layer can fail fast with ``Code.E0106`` instead of masking it.
"""

from __future__ import annotations

from optics_framework.common.error import Code, OpticsError

try:
    from selenium.common.exceptions import InvalidSessionIdException

    _DEAD_SESSION_EXCEPTIONS: tuple[type[BaseException], ...] = (InvalidSessionIdException,)
except ImportError:  # selenium/appium ship as optional extras
    _DEAD_SESSION_EXCEPTIONS = ()

DEAD_SESSION_MESSAGE = (
    "Driver session is no longer active -- the remote server has dropped it. "
    "The framework does not reconnect automatically: terminate this session and "
    "start a new one."
)


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
        if _DEAD_SESSION_EXCEPTIONS and isinstance(current, _DEAD_SESSION_EXCEPTIONS):
            return True
        if isinstance(current, OpticsError) and current.code == Code.E0106:
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
