#!/usr/bin/env python3
"""Routing store seam for the Optics supervisor.

Holds everything a supervisor replica must agree on with its peers:

- session routes: session_id -> worker endpoint (full base URL,
  e.g. "http://127.0.0.1:9001")
- worker registry: the set of live worker endpoints, kept alive by TTL
  heartbeats
- round-robin cursor for spreading new sessions over live workers

The in-memory implementation reproduces the original single-process globals
(session_map / worker_index) behind this interface; a shared-store
implementation makes any supervisor replica able to route any session.
"""

import threading
import time
from abc import ABC, abstractmethod
from typing import Any

try:  # optional dependency: pip install optics-framework[supervisor]
    import redis as _redis
except ImportError:  # pragma: no cover - exercised only without the extra
    _redis = None


class RoutingStore(ABC):
    """Session-routing + worker-registry contract shared by supervisor replicas."""

    # -- session routes -------------------------------------------------
    @abstractmethod
    def get_route(self, session_id: str) -> str | None:
        """Return the worker endpoint owning session_id, or None."""

    @abstractmethod
    def put_route(self, session_id: str, endpoint: str) -> None:
        """Bind session_id to a worker endpoint."""

    @abstractmethod
    def delete_route(self, session_id: str) -> None:
        """Remove the binding for session_id (no-op if absent)."""

    @abstractmethod
    def list_routes(self) -> dict[str, str]:
        """Snapshot of session_id -> endpoint. Used for health introspection
        and for cleaning up routes of a crashed worker."""

    # -- worker registry -------------------------------------------------
    @abstractmethod
    def register_worker(self, endpoint: str, ttl_s: float) -> None:
        """Add a live worker endpoint with a liveness TTL."""

    @abstractmethod
    def heartbeat_worker(self, endpoint: str, ttl_s: float) -> None:
        """Refresh a worker's liveness TTL. Upserts: a lapsed registration is
        revived, since only the process owner heartbeats and it has just
        verified the worker is alive."""

    @abstractmethod
    def deregister_worker(self, endpoint: str) -> None:
        """Remove a worker endpoint immediately (crash or shutdown)."""

    @abstractmethod
    def list_live_workers(self) -> list[str]:
        """All endpoints whose TTL has not expired, in registration order."""

    @abstractmethod
    def next_worker(self) -> str | None:
        """Atomic round-robin pick over live workers; None if none live."""


class InMemoryRoutingStore(RoutingStore):
    """Exactly the original single-supervisor behavior. Default."""

    def __init__(self) -> None:
        self._routes: dict[str, str] = {}
        self._workers: dict[str, float] = {}  # endpoint -> monotonic expiry
        self._rr_index = 0
        self._lock = threading.Lock()

    # -- session routes -------------------------------------------------
    def get_route(self, session_id: str) -> str | None:
        with self._lock:
            return self._routes.get(session_id)

    def put_route(self, session_id: str, endpoint: str) -> None:
        with self._lock:
            self._routes[session_id] = endpoint

    def delete_route(self, session_id: str) -> None:
        with self._lock:
            self._routes.pop(session_id, None)

    def list_routes(self) -> dict[str, str]:
        with self._lock:
            return dict(self._routes)

    # -- worker registry -------------------------------------------------
    def register_worker(self, endpoint: str, ttl_s: float) -> None:
        with self._lock:
            self._workers[endpoint] = time.monotonic() + ttl_s

    def heartbeat_worker(self, endpoint: str, ttl_s: float) -> None:
        self.register_worker(endpoint, ttl_s)

    def deregister_worker(self, endpoint: str) -> None:
        with self._lock:
            self._workers.pop(endpoint, None)

    def list_live_workers(self) -> list[str]:
        with self._lock:
            return self._live_workers_locked()

    def next_worker(self) -> str | None:
        with self._lock:
            live = self._live_workers_locked()
            if not live:
                return None
            endpoint = live[self._rr_index % len(live)]
            self._rr_index += 1
            return endpoint

    def _live_workers_locked(self) -> list[str]:
        now = time.monotonic()
        return [ep for ep, expiry in self._workers.items() if expiry > now]


class RedisRoutingStore(RoutingStore):
    """Shared-store backend: any supervisor replica can route any session.

    Key layout (all under one prefix so routes / worker registry / cursor are
    separate key spaces of a single store):

    - ``{prefix}:route:{session_id}`` -> worker endpoint (no TTL; deleted on
      stop, crash cleanup, or reaping)
    - ``{prefix}:worker:{endpoint}``  -> "1" with PX TTL (liveness)
    - ``{prefix}:rr``                 -> round-robin cursor via atomic INCR
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        client: Any | None = None,
        key_prefix: str = "optics:supervisor",
    ) -> None:
        if client is not None:
            self._redis = client
        else:
            if _redis is None:
                raise RuntimeError(
                    "SUPERVISOR_STORE=redis requires the redis package; "
                    "install with: pip install optics-framework[supervisor]"
                )
            self._redis = _redis.Redis.from_url(
                url or "redis://127.0.0.1:6379/0", decode_responses=True
            )
        self._prefix = key_prefix

    # -- keys --------------------------------------------------------------
    def _route_key(self, session_id: str) -> str:
        return f"{self._prefix}:route:{session_id}"

    def _worker_key(self, endpoint: str) -> str:
        return f"{self._prefix}:worker:{endpoint}"

    # -- session routes -------------------------------------------------
    def get_route(self, session_id: str) -> str | None:
        return self._redis.get(self._route_key(session_id))

    def put_route(self, session_id: str, endpoint: str) -> None:
        self._redis.set(self._route_key(session_id), endpoint)

    def delete_route(self, session_id: str) -> None:
        self._redis.delete(self._route_key(session_id))

    def list_routes(self) -> dict[str, str]:
        prefix = f"{self._prefix}:route:"
        keys = list(self._redis.scan_iter(match=f"{prefix}*"))
        if not keys:
            return {}
        values = self._redis.mget(keys)
        return {
            key[len(prefix):]: value
            for key, value in zip(keys, values)
            if value is not None
        }

    # -- worker registry -------------------------------------------------
    def register_worker(self, endpoint: str, ttl_s: float) -> None:
        self._redis.set(self._worker_key(endpoint), "1", px=max(1, int(ttl_s * 1000)))

    def heartbeat_worker(self, endpoint: str, ttl_s: float) -> None:
        self.register_worker(endpoint, ttl_s)

    def deregister_worker(self, endpoint: str) -> None:
        self._redis.delete(self._worker_key(endpoint))

    def list_live_workers(self) -> list[str]:
        prefix = f"{self._prefix}:worker:"
        # Sorted (SCAN has no stable order) so every replica agrees on the
        # round-robin sequence.
        return sorted(
            key[len(prefix):] for key in self._redis.scan_iter(match=f"{prefix}*")
        )

    def next_worker(self) -> str | None:
        live = self.list_live_workers()
        if not live:
            return None
        cursor = int(self._redis.incr(f"{self._prefix}:rr"))
        return live[(cursor - 1) % len(live)]
