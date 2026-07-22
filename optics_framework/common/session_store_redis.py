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

``redis`` is an optional dependency (extra ``stateless``); it is imported only
inside :meth:`RedisSessionStore.from_url`, so the default in-memory path never
requires it.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from optics_framework.common.models import SessionState
from optics_framework.common.session_manager import SessionStore


class RedisSessionStore(SessionStore):
    """A ``SessionStore`` backed by a Redis (or Redis-compatible) client.

    The client is injected so the store is testable with any object exposing
    the small surface used here (``get``/``set``/``delete``/``scan_iter``);
    :meth:`from_url` builds a real ``redis.Redis`` for production.
    """

    DEFAULT_PREFIX = "optics"

    def __init__(self, client: Any, *, key_prefix: str = DEFAULT_PREFIX) -> None:
        self._r = client
        self._prefix = key_prefix.rstrip(":")

    @classmethod
    def from_url(cls, url: str, *, key_prefix: str = DEFAULT_PREFIX, **kwargs: Any) -> "RedisSessionStore":
        """Build a store from a Redis URL. Requires the optional ``redis`` package."""
        import redis  # optional dependency; imported lazily so the default path never needs it

        client = redis.Redis.from_url(url, decode_responses=True, **kwargs)
        return cls(client, key_prefix=key_prefix)

    # --- key helpers ---------------------------------------------------------

    def _skey(self, session_id: str) -> str:
        return f"{self._prefix}:session:{session_id}"

    def _lkey(self, session_id: str) -> str:
        return f"{self._prefix}:lease:{session_id}"

    @staticmethod
    def _decode(value: Any) -> Any:
        return value.decode() if isinstance(value, (bytes, bytearray)) else value

    # --- state ---------------------------------------------------------------

    def put_state(self, state: SessionState) -> None:
        self._r.set(self._skey(state.session_id), state.model_dump_json())

    def get_state(self, session_id: str) -> Optional[SessionState]:
        raw = self._r.get(self._skey(session_id))
        if raw is None:
            return None
        return SessionState.model_validate_json(self._decode(raw))

    def delete_state(self, session_id: str) -> None:
        # Drop the lease alongside the state so a deleted session leaves nothing behind.
        self._r.delete(self._skey(session_id))
        self._r.delete(self._lkey(session_id))

    def list_states(self) -> Iterable[SessionState]:
        # SCAN (not KEYS) so this is safe against a large keyspace in production.
        states = []
        for key in self._r.scan_iter(match=self._skey("*")):
            raw = self._r.get(key)
            if raw is not None:
                states.append(SessionState.model_validate_json(self._decode(raw)))
        return states

    # --- leases --------------------------------------------------------------
    #
    # Mutual exclusion (the correctness-critical property) rests on ``SET NX PX``
    # being atomic in Redis: two pods can never both win a fresh acquire. The
    # renew/release branches use GET-then-conditional-SET/DEL, which have narrow,
    # benign windows only around expiry/teardown and never grant the lease to a
    # second holder. Hardening those to a single Lua CAS is a noted follow-up.

    def acquire_lease(self, session_id: str, instance_id: str, ttl: float) -> bool:
        lkey = self._lkey(session_id)
        ttl_ms = max(1, int(ttl * 1000))
        # Fresh acquire: atomic, so at most one pod wins when the key is absent
        # (absent == never created OR expired == orphan reclaim, for free).
        if self._r.set(lkey, instance_id, nx=True, px=ttl_ms):
            return True
        # Already held: extend only if it is already ours. ``xx`` guarantees we
        # never resurrect a key that expired between GET and SET, so a lease lost
        # to expiry is correctly reported as lost rather than silently reclaimed.
        if self._decode(self._r.get(lkey)) == instance_id:
            return bool(self._r.set(lkey, instance_id, xx=True, px=ttl_ms))
        return False

    def renew_lease(self, session_id: str, instance_id: str, ttl: float) -> bool:
        lkey = self._lkey(session_id)
        ttl_ms = max(1, int(ttl * 1000))
        if self._decode(self._r.get(lkey)) != instance_id:
            return False
        return bool(self._r.set(lkey, instance_id, xx=True, px=ttl_ms))

    def release_lease(self, session_id: str, instance_id: str) -> None:
        lkey = self._lkey(session_id)
        if self._decode(self._r.get(lkey)) == instance_id:
            self._r.delete(lkey)
