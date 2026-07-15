#!/usr/bin/env python3
"""
Contract tests for RoutingStore implementations.

Both backends must satisfy the same behavior; the Redis one runs against
fakeredis so CI needs no server.

Run with: python -m pytest test_routing_store.py -v
"""

import time

import fakeredis
import pytest

from routing_store import InMemoryRoutingStore, RedisRoutingStore


@pytest.fixture(params=["memory", "redis"])
def store(request):
    if request.param == "memory":
        return InMemoryRoutingStore()
    return RedisRoutingStore(client=fakeredis.FakeRedis(decode_responses=True))


WORKER_A = "http://127.0.0.1:9000"
WORKER_B = "http://127.0.0.1:9001"


class TestRoutes:
    def test_get_missing_route(self, store):
        assert store.get_route("nope") is None

    def test_put_and_get_route(self, store):
        store.put_route("sid-1", WORKER_A)
        assert store.get_route("sid-1") == WORKER_A

    def test_overwrite_route(self, store):
        store.put_route("sid-1", WORKER_A)
        store.put_route("sid-1", WORKER_B)
        assert store.get_route("sid-1") == WORKER_B

    def test_delete_route(self, store):
        store.put_route("sid-1", WORKER_A)
        store.delete_route("sid-1")
        assert store.get_route("sid-1") is None

    def test_delete_missing_route_is_noop(self, store):
        store.delete_route("nope")  # must not raise

    def test_list_routes(self, store):
        assert store.list_routes() == {}
        store.put_route("sid-1", WORKER_A)
        store.put_route("sid-2", WORKER_B)
        assert store.list_routes() == {"sid-1": WORKER_A, "sid-2": WORKER_B}


class TestWorkerRegistry:
    def test_register_and_list(self, store):
        store.register_worker(WORKER_A, ttl_s=10)
        store.register_worker(WORKER_B, ttl_s=10)
        assert set(store.list_live_workers()) == {WORKER_A, WORKER_B}

    def test_deregister(self, store):
        store.register_worker(WORKER_A, ttl_s=10)
        store.deregister_worker(WORKER_A)
        assert store.list_live_workers() == []

    def test_ttl_expiry(self, store):
        store.register_worker(WORKER_A, ttl_s=0.05)
        time.sleep(0.15)
        assert store.list_live_workers() == []

    def test_heartbeat_extends_ttl(self, store):
        store.register_worker(WORKER_A, ttl_s=0.2)
        time.sleep(0.1)
        store.heartbeat_worker(WORKER_A, ttl_s=10)
        time.sleep(0.15)  # would have expired without the heartbeat
        assert store.list_live_workers() == [WORKER_A]

    def test_heartbeat_revives_lapsed_worker(self, store):
        """Heartbeats upsert: the process owner has just verified liveness."""
        store.register_worker(WORKER_A, ttl_s=0.05)
        time.sleep(0.15)
        store.heartbeat_worker(WORKER_A, ttl_s=10)
        assert store.list_live_workers() == [WORKER_A]


class TestRoundRobin:
    def test_next_worker_empty(self, store):
        assert store.next_worker() is None

    def test_next_worker_cycles(self, store):
        store.register_worker(WORKER_A, ttl_s=10)
        store.register_worker(WORKER_B, ttl_s=10)

        picks = [store.next_worker() for _ in range(4)]
        assert picks[0] != picks[1]
        assert picks[0] == picks[2]
        assert picks[1] == picks[3]
        assert set(picks) == {WORKER_A, WORKER_B}

    def test_next_worker_skips_dead(self, store):
        store.register_worker(WORKER_A, ttl_s=10)
        store.register_worker(WORKER_B, ttl_s=10)
        store.deregister_worker(WORKER_A)

        assert all(store.next_worker() == WORKER_B for _ in range(3))


class TestLeases:
    def test_no_leases_initially(self, store):
        assert store.expired_leases() == []

    def test_active_lease_not_expired(self, store):
        store.acquire_lease("sid-1", owner="worker-1", ttl_s=10)
        assert store.expired_leases() == []

    def test_expired_lease_is_reported_with_owner(self, store):
        store.acquire_lease("sid-1", owner="worker-1", ttl_s=0.05)
        time.sleep(0.15)
        assert store.expired_leases() == [("sid-1", "worker-1")]

    def test_expired_lease_stays_visible_until_released(self, store):
        """Unlike the worker registry, expiry must not delete the lease —
        the reaper needs to observe it."""
        store.acquire_lease("sid-1", owner="worker-1", ttl_s=0.05)
        time.sleep(0.15)
        assert store.expired_leases() == [("sid-1", "worker-1")]
        assert store.expired_leases() == [("sid-1", "worker-1")]

    def test_renew_extends_lease(self, store):
        store.acquire_lease("sid-1", owner="worker-1", ttl_s=0.2)
        time.sleep(0.1)
        store.renew_lease("sid-1", ttl_s=10)
        time.sleep(0.15)  # would have expired without the renewal
        assert store.expired_leases() == []

    def test_renew_unknown_lease_is_noop(self, store):
        """A stray late request must not resurrect a released session."""
        store.renew_lease("sid-ghost", ttl_s=0.01)
        time.sleep(0.05)
        # Had the renew created a lease, it would show up expired by now.
        assert store.expired_leases() == []

    def test_release_lease(self, store):
        store.acquire_lease("sid-1", owner="worker-1", ttl_s=0.05)
        store.release_lease("sid-1")
        time.sleep(0.1)
        assert store.expired_leases() == []

    def test_release_missing_lease_is_noop(self, store):
        store.release_lease("nope")  # must not raise


class TestRedisSpecific:
    """Behavior only meaningful for the shared backend."""

    def test_two_replicas_share_state(self):
        client = fakeredis.FakeRedis(decode_responses=True)
        replica_a = RedisRoutingStore(client=client)
        replica_b = RedisRoutingStore(client=client)

        replica_a.register_worker(WORKER_A, ttl_s=10)
        replica_a.put_route("sid-1", WORKER_A)

        assert replica_b.get_route("sid-1") == WORKER_A
        assert replica_b.list_live_workers() == [WORKER_A]
        # Round-robin cursor is shared too
        assert replica_a.next_worker() == WORKER_A
        assert replica_b.next_worker() == WORKER_A

    def test_key_prefix_isolation(self):
        client = fakeredis.FakeRedis(decode_responses=True)
        store_x = RedisRoutingStore(client=client, key_prefix="x")
        store_y = RedisRoutingStore(client=client, key_prefix="y")

        store_x.put_route("sid-1", WORKER_A)
        store_x.register_worker(WORKER_A, ttl_s=10)

        assert store_y.get_route("sid-1") is None
        assert store_y.list_routes() == {}
        assert store_y.list_live_workers() == []


if __name__ == "__main__":
    pytest.main([__file__])
