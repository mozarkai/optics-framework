from __future__ import annotations

from optics_framework.common.error import Code, OpticsError
from optics_framework.common.logging_config import internal_logger

DEAD_SESSION_MESSAGE = (
    "Driver session is no longer active -- the remote server has dropped it. "
    "The framework does not reconnect automatically: terminate this session and "
    "start a new one."
)


def _loaded_driver_classes() -> list[type]:
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
    for cls in _loaded_driver_classes():
        try:
            if cls.is_dead_session_error(exc):
                return True
        except Exception as predicate_error:
            internal_logger.debug(
                "%s.is_dead_session_error raised while inspecting %r: %s",
                cls.__name__,
                exc,
                predicate_error,
            )
    return False


def is_dead_session_error(exc: BaseException | None) -> bool:
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
    if isinstance(exc, OpticsError) and exc.code == Code.E0106:
        raise exc
    if is_dead_session_error(exc):
        raise OpticsError(Code.E0106, message=DEAD_SESSION_MESSAGE, cause=exc) from exc


__all__ = ["DEAD_SESSION_MESSAGE", "is_dead_session_error", "raise_if_session_dead"]
