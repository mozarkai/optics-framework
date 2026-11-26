from collections.abc import Iterator
from contextlib import contextmanager
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from optics_framework.common.logging_config import internal_logger

T = TypeVar("T")


class InstanceFallback(BaseModel, Generic[T]):
    """Manages fallback instances for dynamic method invocation."""
    instances: List[T] = Field(default_factory=list)
    current_instance: Optional[T] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, instances: List[T], **data):
        super().__init__(instances=instances, **data)
        self.current_instance = instances[0] if instances else None

    @property
    def active_instance(self) -> Optional[T]:
        """The concrete instance currently in play.

        Prefers ``current_instance`` (the one the last fallback call selected), falling
        back to the first configured instance, or ``None`` when there are none. Callers
        that need a real attribute off the wrapped source (e.g. ``REQUIRED_DRIVER_TYPE``
        or ``driver``) must go through this -- reading such an attribute off the wrapper
        directly would be intercepted by ``__getattr__`` and returned as a callable.
        """
        return self.current_instance or (self.instances[0] if self.instances else None)

    @contextmanager
    def routed_to(self, instance: T | None) -> Iterator[None]:
        """Send the calls made inside the block to ``instance`` alone, when it is one of these."""
        configured = self.instances
        if any(candidate is instance for candidate in configured):
            self.instances = [instance]
        try:
            yield
        finally:
            self.instances = configured

    def __getattr__(self, attr):
        def fallback_method(*args, **kwargs):
            if not self.instances:
                internal_logger.warning(
                    f"Attempted to call '{attr}' but no valid instances exist.")
                return None
            last_exception = None
            for instance in self.instances:
                self.current_instance = instance
                try:
                    method = getattr(instance, attr)
                    return method(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    internal_logger.error(
                        f"Error calling '{attr}' on {instance}: {e}")
            self.current_instance = None
            if last_exception:
                raise last_exception
            raise AttributeError(
                f"Attribute '{attr}' not found in fallback instances.")
        return fallback_method
