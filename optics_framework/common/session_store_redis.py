"""Redis-backed :class:`SessionStore` — Layer 2 of the stateless API design.

The Layer-1 seams (``SessionStore`` ABC with lease methods, ``DriverBinding``,
``get_or_rehydrate``) were built so this store drops in with zero call-site
churn: selecting it is a config switch (``OPTICS_SESSION_STORE=redis``), the
``SessionManager`` already calls ``acquire``/``renew``/``release`` on every
lookup.

Layout in Redis:

- ``{prefix}:session:{sid}`` — the durable ``SessionState`` as JSON. This is
  the shared truth every pod reconstructs a live session from.
- ``{prefix}:lease:{sid}`` — a TTL'd key holding the owning ``instance_id``.
  It provides distributed mutual exclusion *and* automatic orphan reclaim: a
  pod that dies stops renewing, the lease key expires, and any other pod may
  then acquire it and rehydrate the session.

Lease correctness (Layer-2 hardening):

- A *fresh* acquire is a single atomic ``SET NX PX`` — two pods can never both
  win when the key is absent (absent == never created OR expired == orphan
  reclaim, for free).
- *Renew*, *release*, and the *extend-if-ours* branch of acquire are each a
  single **Lua CAS** (compare ``instance_id`` then ``PEXPIRE``/``DEL``
  atomically). This closes the check-then-act window a GET-then-SET/DEL would
  leave, where a lease could expire and be re-acquired by another pod between
  the two calls — a renew would then steal the new holder's lease, and a
  release would delete it.

Resilience: :meth:`from_url` configures ``redis-py``'s bounded ``Retry`` so a
brief Redis blip is ridden out transparently; once retries are exhausted a
``RedisError`` is translated to :class:`SessionStoreUnavailable` so the API
layer can surface a clean HTTP 503 without importing ``redis``.

``redis`` is an optional dependency (extra ``stateless``); it is imported only
inside :meth:`from_url`, so the default in-memory path never requires it.
"""
from __future__ import annotations

import functools
from typing import Any, Callable, Iterable, Optional, Tuple, Type

from optics_framework.common.models import SessionState
from optics_framework.common.session_manager import (
    SessionStore,
    SessionStoreUnavailable,
)

# Lua CAS scripts. Each compares the stored owner to ARGV[1] and only then
# mutates, so the read and the write are one atomic step on the Redis server.
_RENEW_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('PEXPIRE', KEYS[1], ARGV[2]) else return 0 end"
)
_RELEASE_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) else return 0 end"
)


def _translate_errors(method: Callable) -> Callable:
    """Translate the configured Redis error types to
    :class:`SessionStoreUnavailable` so the API layer can map them to a 503
    without importing ``redis``. A no-op when ``_error_types`` is empty (the
    injected-client test path)."""

    @functools.wraps(method)
    def wrapper(self: "RedisSessionStore", *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except self._error_types as e:  # type: ignore[misc]
            raise SessionStoreUnavailable(str(e)) from e

    return wrapper


class RedisSessionStore(SessionStore):
    """A ``SessionStore`` backed by a Redis (or Redis-compatible) client.

    The client is injected so the store is testable with any object exposing
    the small surface used here (``get``/``set``/``delete``/``scan_iter``/
    ``register_script``); :meth:`from_url` builds a real ``redis.Redis`` for
    production.
    """

    DEFAULT_PREFIX = "optics"

    def __init__(
        self,
        client: Any,
        *,
        key_prefix: str = DEFAULT_PREFIX,
        error_types: Tuple[Type[BaseException], ...] = (),
    ) -> None:
        self._r = client
        self._prefix = key_prefix.rstrip(":")
        # Exception classes to translate to SessionStoreUnavailable. Empty for
        # injected test clients (nothing to translate); set to redis's error
        # hierarchy by from_url for the production client.
        self._error_types: Tuple[Type[BaseException], ...] = error_types
        self._renew_script = client.register_script(_RENEW_LUA)
        self._release_script = client.register_script(_RELEASE_LUA)

    @classmethod
    def from_url(cls, url: str, *, key_prefix: str = DEFAULT_PREFIX, **kwargs: Any) -> "RedisSessionStore":
        """Build a store from a Redis URL. Requires the optional ``redis`` package."""
        import redis  # optional dependency; imported lazily so the default path never needs it
        from redis.backoff import ExponentialBackoff
        from redis.retry import Retry

        # Bounded retry: ride out brief connection blips transparently before
        # giving up and letting the error surface as a 503.
        retry = Retry(ExponentialBackoff(cap=0.5, base=0.05), retries=3)
        client = redis.Redis.from_url(
            url,
            decode_responses=True,
            retry=retry,
            retry_on_error=[redis.exceptions.ConnectionError, redis.exceptions.TimeoutError],
            health_check_interval=30,
            **kwargs,
        )
        return cls(
            client,
            key_prefix=key_prefix,
            error_types=(redis.exceptions.RedisError,),
        )

    # --- key helpers ---------------------------------------------------------

    def _skey(self, session_id: str) -> str:
        return f"{self._prefix}:session:{session_id}"

    def _lkey(self, session_id: str) -> str:
        return f"{self._prefix}:lease:{session_id}"

    @staticmethod
    def _decode(value: Any) -> Any:
        return value.decode() if isinstance(value, (bytes, bytearray)) else value

    # --- state ---------------------------------------------------------------

    @_translate_errors
    def put_state(self, state: SessionState) -> None:
        self._r.set(self._skey(state.session_id), state.model_dump_json())

    @_translate_errors
    def get_state(self, session_id: str) -> Optional[SessionState]:
        raw = self._r.get(self._skey(session_id))
        if raw is None:
            return None
        return SessionState.model_validate_json(self._decode(raw))

    @_translate_errors
    def delete_state(self, session_id: str) -> None:
        # Drop the lease alongside the state so a deleted session leaves nothing behind.
        self._r.delete(self._skey(session_id))
        self._r.delete(self._lkey(session_id))

    @_translate_errors
    def list_states(self) -> Iterable[SessionState]:
        # SCAN (not KEYS) so this is safe against a large keyspace in production.
        states = []
        for key in self._r.scan_iter(match=self._skey("*")):
            raw = self._r.get(key)
            if raw is not None:
                states.append(SessionState.model_validate_json(self._decode(raw)))
        return states

    # --- leases --------------------------------------------------------------

    @_translate_errors
    def acquire_lease(self, session_id: str, instance_id: str, ttl: float) -> bool:
        lkey = self._lkey(session_id)
        ttl_ms = max(1, int(ttl * 1000))
        # Fresh acquire: atomic, so at most one pod wins when the key is absent
        # (absent == never created OR expired == orphan reclaim, for free).
        if self._r.set(lkey, instance_id, nx=True, px=ttl_ms):
            return True
        # Already held: extend only if it is already ours, atomically (Lua CAS).
        return bool(self._renew_script(keys=[lkey], args=[instance_id, ttl_ms]))

    @_translate_errors
    def renew_lease(self, session_id: str, instance_id: str, ttl: float) -> bool:
        lkey = self._lkey(session_id)
        ttl_ms = max(1, int(ttl * 1000))
        return bool(self._renew_script(keys=[lkey], args=[instance_id, ttl_ms]))

    @_translate_errors
    def release_lease(self, session_id: str, instance_id: str) -> None:
        lkey = self._lkey(session_id)
        self._release_script(keys=[lkey], args=[instance_id])
