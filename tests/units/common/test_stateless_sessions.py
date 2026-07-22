"""Unit tests for the stateless session layer (Layer 1).

Covers the SessionState/runtime split, the SessionStore, the driver
capability gate, detach vs terminate, export/import, and rehydration with
strict reattach — per docs/contribution/stateless_api_design.md.
"""
import asyncio
import base64
import os

import pytest
from fastapi import HTTPException

from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.models import DriverBinding, SessionState, SessionStatus
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.session_manager import (
    InMemorySessionStore,
    SessionManager,
    SessionOwnedElsewhere,
    build_session_store_from_env,
    resolve_driver_binding,
)
from optics_framework.common.session_store_redis import RedisSessionStore

FAKE_BACKEND_SESSION = "backend-123"
FAKE_ENDPOINT = "http://fake-driver:4723"


class FakeMigratableDriver:
    """Driver that opts into the session-migration contract (design §6)."""

    NAME = "fakedrv"
    supports_session_migration = True

    def __init__(self):
        self.backend_session = FAKE_BACKEND_SESSION
        self.attached_handle = None
        self.detach_calls = 0
        self.terminate_calls = 0

    def get_reattach_params(self):
        return {
            "reattach_handle": self.backend_session,
            "endpoint": FAKE_ENDPOINT,
            "capabilities": {"udid": "device-1"},
            "device_id": "device-1",
        }

    def reattach(self, reattach_params, strict=True):
        handle = (reattach_params or {}).get("reattach_handle")
        if handle != self.backend_session:
            raise OpticsError(Code.E0102, message=f"No such backend session: {handle}")
        self.attached_handle = handle

    def detach(self):
        self.detach_calls += 1

    def terminate(self):
        self.terminate_calls += 1


class FakeStickyDriver:
    """Driver that keeps the default: non-migratable, detach == terminate."""

    NAME = "fakedrv"
    supports_session_migration = False

    def __init__(self):
        self.terminate_calls = 0

    def terminate(self):
        self.terminate_calls += 1


def _make_config(tmp_path) -> Config:
    return Config(
        driver_sources=[
            {"fakedrv": DependencyConfig(enabled=True, url=FAKE_ENDPOINT, capabilities={"udid": "device-1"})}
        ],
        project_path=str(tmp_path),
    )


@pytest.fixture
def fake_driver():
    return FakeMigratableDriver()


@pytest.fixture
def manager(monkeypatch, fake_driver):
    """SessionManager whose sessions get an InstanceFallback around the fake driver."""
    monkeypatch.setattr(
        OpticsBuilder, "get_driver", lambda self: InstanceFallback([fake_driver])
    )
    return SessionManager()


def _create(manager, tmp_path):
    return manager.create_session(
        _make_config(tmp_path), test_cases=None, modules=None, elements=None, apis=None
    )


# --- SessionStore -----------------------------------------------------------

def _dummy_state(session_id="sid-1") -> SessionState:
    return SessionState(
        session_id=session_id,
        config={},
        driver_binding=DriverBinding(driver_type="fakedrv"),
        created_at=1.0,
        updated_at=1.0,
    )


def test_in_memory_store_crud():
    store = InMemorySessionStore()
    state = _dummy_state()
    store.put_state(state)
    assert store.get_state("sid-1") is state
    assert list(store.list_states()) == [state]
    store.delete_state("sid-1")
    assert store.get_state("sid-1") is None
    store.delete_state("sid-1")  # idempotent


def test_in_memory_store_leases_always_granted():
    store = InMemorySessionStore()
    state = _dummy_state()
    store.put_state(state)
    assert store.acquire_lease("sid-1", "inst-a", ttl=60) is True
    assert state.owner_instance_id == "inst-a"
    assert state.lease_expires_at is not None
    # In-memory leases are advisory: a second instance is also granted.
    assert store.acquire_lease("sid-1", "inst-b", ttl=60) is True
    assert state.owner_instance_id == "inst-b"
    assert store.renew_lease("sid-1", "inst-b", ttl=60) is True
    store.release_lease("sid-1", "inst-a")  # not the owner: no-op
    assert state.owner_instance_id == "inst-b"
    store.release_lease("sid-1", "inst-b")
    assert state.owner_instance_id is None
    assert state.lease_expires_at is None


# --- resolve_driver_binding seam ---------------------------------------------

def test_resolve_driver_binding_reads_first_enabled_source(tmp_path):
    binding = resolve_driver_binding(_make_config(tmp_path))
    assert binding.driver_type == "fakedrv"
    assert binding.endpoint == FAKE_ENDPOINT
    assert binding.device_id == "device-1"
    assert binding.migratable is False  # driver not consulted yet


def test_resolve_driver_binding_requires_enabled_driver():
    with pytest.raises(OpticsError):
        resolve_driver_binding(Config())


# --- create / state-runtime split --------------------------------------------

def test_create_session_records_state_in_store(manager, tmp_path):
    sid = _create(manager, tmp_path)
    state = manager.store.get_state(sid)
    assert state is not None
    assert state.status == SessionStatus.ACTIVE
    assert state.owner_instance_id == manager.instance_id
    assert state.driver_binding.migratable is True
    assert state.config["project_path"] == str(tmp_path)
    session = manager.get_session(sid)
    assert session is not None and session.state is state
    assert session.runtime is not None


def test_get_session_unknown_returns_none(manager):
    assert manager.get_session("nope") is None


# --- export -------------------------------------------------------------------

def test_export_state_carries_reattach_handle_and_template_bytes(manager, tmp_path, fake_driver):
    sid = _create(manager, tmp_path)
    session = manager.get_session(sid)
    template_path = os.path.join(session._inline_templates_dir, "btn.png")
    with open(template_path, "wb") as f:
        f.write(b"\x89PNG-fake")
    session.inline_templates["btn"] = template_path

    exported = manager.export_state(sid)
    assert exported.driver_binding.reattach_handle == FAKE_BACKEND_SESSION
    assert exported.driver_binding.endpoint == FAKE_ENDPOINT
    assert base64.b64decode(exported.inline_templates["btn"]) == b"\x89PNG-fake"
    # Deep copy: mutating the export must not touch the stored truth.
    exported.metadata["x"] = 1
    assert "x" not in manager.store.get_state(sid).metadata


# --- detach vs terminate --------------------------------------------------------

def test_detach_keeps_state_and_backend_but_drops_runtime(manager, tmp_path, fake_driver):
    sid = _create(manager, tmp_path)
    session = manager.get_session(sid)
    templates_dir = session._inline_templates_dir

    state = manager.detach_session(sid)

    assert state.status == SessionStatus.DETACHED
    assert fake_driver.detach_calls == 1
    assert fake_driver.terminate_calls == 0
    assert sid not in manager.sessions
    assert session.runtime is None
    assert not os.path.isdir(templates_dir)
    stored = manager.store.get_state(sid)
    assert stored is not None and stored.status == SessionStatus.DETACHED
    assert stored.owner_instance_id is None


def test_detach_refuses_busy_session(manager, tmp_path):
    sid = _create(manager, tmp_path)
    manager.mark_busy(sid, True)
    with pytest.raises(OpticsError, match="busy"):
        manager.detach_session(sid)
    manager.mark_busy(sid, False)
    assert manager.detach_session(sid).status == SessionStatus.DETACHED


def test_detach_refuses_open_stream(manager, tmp_path):
    sid = _create(manager, tmp_path)
    manager.get_session(sid).runtime.open_streams = 1
    with pytest.raises(OpticsError, match="busy"):
        manager.detach_session(sid)


def test_detach_refuses_sticky_driver(monkeypatch, tmp_path):
    sticky = FakeStickyDriver()
    monkeypatch.setattr(OpticsBuilder, "get_driver", lambda self: InstanceFallback([sticky]))
    manager = SessionManager()
    sid = _create(manager, tmp_path)
    assert manager.store.get_state(sid).driver_binding.migratable is False
    with pytest.raises(OpticsError, match="does not support session migration"):
        manager.detach_session(sid)
    # The session stays fully usable on its origin instance.
    assert manager.get_session(sid) is not None


def test_terminate_ends_backend_and_deletes_state(manager, tmp_path, fake_driver):
    sid = _create(manager, tmp_path)
    manager.terminate_session(sid)
    assert fake_driver.terminate_calls == 1
    assert manager.store.get_state(sid) is None
    assert manager.get_session(sid) is None


def test_terminate_detached_session_reattaches_to_kill_backend(manager, tmp_path, fake_driver):
    sid = _create(manager, tmp_path)
    manager.detach_session(sid)
    manager.terminate_session(sid)
    assert fake_driver.attached_handle == FAKE_BACKEND_SESSION
    assert fake_driver.terminate_calls == 1
    assert manager.store.get_state(sid) is None


# --- rehydration (the unifying path) --------------------------------------------

def test_get_session_rehydrates_after_detach(manager, tmp_path, fake_driver):
    sid = _create(manager, tmp_path)
    original = manager.get_session(sid)
    manager.detach_session(sid)

    rehydrated = manager.get_session(sid)

    assert rehydrated is not None and rehydrated is not original
    assert rehydrated.runtime is not None
    assert fake_driver.attached_handle == FAKE_BACKEND_SESSION
    state = manager.store.get_state(sid)
    assert state.status == SessionStatus.ACTIVE
    assert state.owner_instance_id == manager.instance_id


def test_rehydrate_strict_attach_fails_closed(manager, tmp_path, fake_driver):
    sid = _create(manager, tmp_path)
    manager.detach_session(sid)
    # Backend session died while detached: strict reattach must raise, never
    # silently launch a fresh session (design §5).
    fake_driver.backend_session = "some-other-session"
    with pytest.raises(OpticsError):
        manager.get_session(sid)
    assert fake_driver.attached_handle is None


def test_rehydrate_restores_inline_templates(manager, tmp_path, fake_driver):
    sid = _create(manager, tmp_path)
    session = manager.get_session(sid)
    template_path = os.path.join(session._inline_templates_dir, "btn.png")
    with open(template_path, "wb") as f:
        f.write(b"template-bytes")
    session.inline_templates["btn"] = template_path
    manager.detach_session(sid)

    rehydrated = manager.get_session(sid)

    restored_path = rehydrated.inline_templates["btn"]
    assert restored_path != template_path
    with open(restored_path, "rb") as f:
        assert f.read() == b"template-bytes"


# --- export / import across instances --------------------------------------------

def test_export_import_round_trip_across_managers(monkeypatch, tmp_path):
    fake = FakeMigratableDriver()
    monkeypatch.setattr(OpticsBuilder, "get_driver", lambda self: InstanceFallback([fake]))
    manager_a = SessionManager()
    manager_b = SessionManager()

    sid = _create(manager_a, tmp_path)
    exported = manager_a.export_state(sid)
    manager_a.detach_session(sid)

    imported_sid = manager_b.create_session_from_state(exported)

    assert imported_sid == sid
    assert fake.attached_handle == FAKE_BACKEND_SESSION
    state = manager_b.store.get_state(sid)
    assert state.status == SessionStatus.ACTIVE
    assert state.owner_instance_id == manager_b.instance_id


def test_import_refuses_session_already_live_here(manager, tmp_path):
    sid = _create(manager, tmp_path)
    exported = manager.export_state(sid)
    with pytest.raises(OpticsError, match="already has a live runtime"):
        manager.create_session_from_state(exported)


def test_import_failure_leaves_no_state_behind(monkeypatch, tmp_path):
    fake = FakeMigratableDriver()
    monkeypatch.setattr(OpticsBuilder, "get_driver", lambda self: InstanceFallback([fake]))
    manager_a = SessionManager()
    sid = _create(manager_a, tmp_path)
    exported = manager_a.export_state(sid)
    manager_a.detach_session(sid)

    manager_b = SessionManager()
    fake.backend_session = "gone"  # strict reattach will fail on import
    with pytest.raises(OpticsError):
        manager_b.create_session_from_state(exported)
    assert manager_b.store.get_state(sid) is None


# --- HTTP endpoints ---------------------------------------------------------------

def test_endpoints_translate_optics_errors(monkeypatch, tmp_path):
    from optics_framework.common import expose_api

    fake = FakeMigratableDriver()
    monkeypatch.setattr(OpticsBuilder, "get_driver", lambda self: InstanceFallback([fake]))
    manager = SessionManager()
    monkeypatch.setattr(expose_api, "session_manager", manager)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(expose_api.export_session("missing"))
    assert exc.value.status_code == 404

    sid = _create(manager, tmp_path)
    exported = asyncio.run(expose_api.export_session(sid))
    assert exported.session_id == sid
    assert exported.driver_binding.reattach_handle == FAKE_BACKEND_SESSION

    migrate_response = asyncio.run(expose_api.migrate_session(sid))
    assert migrate_response.status == expose_api.STATUS_DETACHED
    assert manager.store.get_state(sid).status == SessionStatus.DETACHED

    # Import back through the endpoint (session no longer live locally).
    manager.sessions.pop(sid, None)
    manager.store.delete_state(sid)
    imported = asyncio.run(expose_api.import_session(exported))
    assert imported.session_id == sid
    assert imported.status == expose_api.STATUS_IMPORTED


def test_migrate_endpoint_refuses_sticky_driver(monkeypatch, tmp_path):
    from optics_framework.common import expose_api

    sticky = FakeStickyDriver()
    monkeypatch.setattr(OpticsBuilder, "get_driver", lambda self: InstanceFallback([sticky]))
    manager = SessionManager()
    monkeypatch.setattr(expose_api, "session_manager", manager)
    sid = _create(manager, tmp_path)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(expose_api.migrate_session(sid))
    assert exc.value.status_code == 400


# --- Layer 2: RedisSessionStore --------------------------------------------------
#
# A minimal in-memory double for the small Redis command surface the store uses
# (get / set with nx|xx|px / delete / scan_iter). Expiry is driven by a logical
# clock the test advances, so lease-timeout behavior is deterministic without
# sleeping.

class FakeRedis:
    def __init__(self):
        self._data = {}          # name -> (value, expiry_or_None)
        self._clock = 0.0

    def advance(self, seconds):
        self._clock += seconds

    def _expired(self, name):
        entry = self._data.get(name)
        if entry is None:
            return True
        _, expiry = entry
        if expiry is not None and expiry <= self._clock:
            del self._data[name]
            return True
        return False

    def set(self, name, value, nx=False, xx=False, px=None, ex=None):
        exists = not self._expired(name)
        if nx and exists:
            return None
        if xx and not exists:
            return None
        expiry = None
        if px is not None:
            expiry = self._clock + px / 1000.0
        elif ex is not None:
            expiry = self._clock + ex
        self._data[name] = (str(value), expiry)
        return True

    def get(self, name):
        if self._expired(name):
            return None
        return self._data[name][0]

    def delete(self, *names):
        removed = 0
        for name in names:
            if name in self._data:
                del self._data[name]
                removed += 1
        return removed

    def scan_iter(self, match=None):
        import fnmatch
        for name in list(self._data.keys()):
            if self._expired(name):
                continue
            if match is None or fnmatch.fnmatch(name, match):
                yield name


def _redis_state(sid="rs-1") -> SessionState:
    return SessionState(
        session_id=sid,
        config={},
        driver_binding=DriverBinding(driver_type="fakedrv"),
        created_at=1.0,
        updated_at=1.0,
    )


def test_redis_store_crud_round_trip():
    store = RedisSessionStore(FakeRedis())
    store.put_state(_redis_state())
    got = store.get_state("rs-1")
    assert got is not None and got.session_id == "rs-1"
    assert [s.session_id for s in store.list_states()] == ["rs-1"]
    store.delete_state("rs-1")
    assert store.get_state("rs-1") is None
    assert list(store.list_states()) == []


def test_redis_store_namespacing_and_json_fidelity():
    store = RedisSessionStore(FakeRedis(), key_prefix="opt:")
    state = _redis_state("rs-2")
    state.driver_binding.reattach_handle = "backend-xyz"
    state.metadata["workspace_hash"] = "abc123"
    store.put_state(state)
    got = store.get_state("rs-2")
    assert got.driver_binding.reattach_handle == "backend-xyz"
    assert got.metadata["workspace_hash"] == "abc123"


def test_redis_lease_mutual_exclusion():
    store = RedisSessionStore(FakeRedis())
    assert store.acquire_lease("s", "A", 10) is True
    assert store.acquire_lease("s", "B", 10) is False      # A holds it
    assert store.renew_lease("s", "B", 10) is False        # B cannot renew A's lease
    assert store.renew_lease("s", "A", 10) is True         # A can
    store.release_lease("s", "B")                          # non-owner release is a no-op
    assert store.renew_lease("s", "A", 10) is True
    store.release_lease("s", "A")
    assert store.acquire_lease("s", "B", 10) is True       # now free


def test_redis_lease_orphan_reclaim_on_expiry():
    redis = FakeRedis()
    store = RedisSessionStore(redis)
    assert store.acquire_lease("s", "A", ttl=5) is True
    assert store.acquire_lease("s", "B", ttl=5) is False
    redis.advance(6)                                        # A stopped renewing (died)
    assert store.acquire_lease("s", "B", ttl=5) is True     # lease reclaimed


def test_redis_lease_reacquire_by_owner_extends():
    redis = FakeRedis()
    store = RedisSessionStore(redis)
    assert store.acquire_lease("s", "A", ttl=5) is True
    redis.advance(3)
    assert store.acquire_lease("s", "A", ttl=5) is True     # extend, still ours
    redis.advance(3)                                        # would have expired at t=5 without extend
    assert store.renew_lease("s", "A", ttl=5) is True


def test_build_store_from_env_defaults_to_memory(monkeypatch):
    monkeypatch.delenv("OPTICS_SESSION_STORE", raising=False)
    assert isinstance(build_session_store_from_env(), InMemorySessionStore)


def test_build_store_from_env_unknown_falls_back_to_memory(monkeypatch):
    monkeypatch.setenv("OPTICS_SESSION_STORE", "bogus")
    assert isinstance(build_session_store_from_env(), InMemorySessionStore)


def test_redis_cross_instance_rehydrate_and_conflict(monkeypatch, tmp_path):
    """Two SessionManagers share one Redis-backed store: while instance A holds
    a live lease, B is refused; after A detaches, B rehydrates and reattaches."""
    fake = FakeMigratableDriver()
    monkeypatch.setattr(OpticsBuilder, "get_driver", lambda self: InstanceFallback([fake]))
    store = RedisSessionStore(FakeRedis())
    manager_a = SessionManager(store=store)
    manager_b = SessionManager(store=store)

    sid = _create(manager_a, tmp_path)

    with pytest.raises(SessionOwnedElsewhere):
        manager_b.get_or_rehydrate(sid)

    manager_a.detach_session(sid)                           # releases the lease

    session_b = manager_b.get_or_rehydrate(sid)
    assert session_b is not None
    assert fake.attached_handle == FAKE_BACKEND_SESSION
    reclaimed = store.get_state(sid)
    assert reclaimed.status == SessionStatus.ACTIVE
    assert reclaimed.owner_instance_id == manager_b.instance_id


def test_redis_local_hit_conflict_when_lease_lost(monkeypatch, tmp_path):
    """A session live locally whose lease expires and is reclaimed elsewhere
    must surface a conflict on the next lookup, not serve a stale runtime."""
    fake = FakeMigratableDriver()
    monkeypatch.setattr(OpticsBuilder, "get_driver", lambda self: InstanceFallback([fake]))
    redis = FakeRedis()
    store = RedisSessionStore(redis)
    manager = SessionManager(store=store, lease_ttl_s=10)

    sid = _create(manager, tmp_path)
    redis.advance(11)                                       # our lease expires
    assert store.acquire_lease(sid, "other-instance", ttl=10) is True

    with pytest.raises(SessionOwnedElsewhere):
        manager.get_session(sid)
