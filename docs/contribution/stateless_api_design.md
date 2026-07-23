# Stateless API Layer — Design

**Status**: Layers 1–2 implemented — Layer 1 (`SessionState`/`SessionStore` split, driver capability gate, `/export` · `/import` · `/migrate`) and Layer 2 (`RedisSessionStore` with distributed leases + orphan reclaim, env-selected store via `OPTICS_SESSION_STORE`, enforced cross-instance lookup, hardened with atomic lease CAS + keepalive heartbeat + bounded-retry→503) | **Tracks**: [Help Wanted §1 — Stateless API Layer](help_wanted.md#1-stateless-api-layer)

This document is the implementation design for making `optics serve` stateless. It is deliberately scoped to **Layer 1** (a stateless session layer) but designs Layer 1's *seams* so **Layer 2** (multi-worker / multi-pod deployment) and **Layer 3** (device/session scheduling across a driver/device fleet) drop in behind interfaces that already exist — no call-site rework, no schema migration.

---

## 1. Framing: three layers, one enabler

"Autoscaling `optics serve`" is really three independent concerns. It is important not to conflate them:

| Layer | Concern | What it delivers |
|-------|---------|------------------|
| **1** | Stateless session layer | Sessions are no longer pinned to one process; any instance can reconstruct a session from shared state. |
| **2** | Multi-worker / multi-pod deployment | Run N processes/pods behind a load balancer; each holds no irreplaceable state. |
| **3** | Device/session scheduling | Place sessions on free devices from any instance; scale up to the device fleet. |

**Layer 1 is the enabler, not the throughput win.** Today `optics serve` is a single process holding sessions in an in-memory dict (`SessionManager.sessions`, `common/expose_api.py`). The moment you add a second worker or pod to gain parallelism, a request load-balanced to a worker that didn't create the session hits `get_session() → None → "Session not found"`. Layer 1 removes that wall by making session state shared and sessions reconstructable on demand. Layers 2 and 3 then become deployment + scheduling problems rather than rewrites.

### Non-goal for this design

Concurrency **inside** a single process (the synchronous driver call chain blocks the event loop, so simultaneous requests serialize) is a *separate* axis. Layer 1 does not fix it. It is expected to be sidestepped by running multiple processes/pods (Layer 2). It is called out here only so it is not mistaken for a statelessness bug.

---

## 2. The anticipation principle

> The shared store — keyed by `session_id` — holds a fully serializable `SessionState` describing how to **reconstruct and reattach** a session. Every instance treats live `Session` objects as a disposable local cache over that state, and rebuilds them on demand.

The corollary is the design's litmus test:

> **Migration and autoscaling are the same code path.** A request landing on an instance without the live runtime *rehydrates* it from the store. `/export`, `/import`, and `/migrate` are thin wrappers over machinery a load balancer exercises implicitly. If migration is a *special* path, the design is wrong.

### Why this is tractable: reattachability is a driver-declared capability, not a design assumption

The Help Wanted brief lists "driver instances … that cannot be serialized" as the core challenge. They do not need to be serialized. Optics already isolates every backend behind `DriverInterface`, so the design's only question is: **can this session be resumed by another instance?** That is not a fact the framework decides — it is a capability each driver *declares* through the contract in §6. The design must never assume any backend is reattachable; it routes every migration decision through that one gate.

When a driver declares it *is* reattachable, "migrate a session" reduces to: rebuild the `Session` on another instance, injecting the driver's reattach handle (via `get_reattach_params()`) so its driver **attaches** to the still-live backend session instead of launching fresh. The device connection never drops because it was never optics's to hold — it lives wherever the driver's backend keeps it.

When a driver declares it is *not* reattachable — because it holds in-process state (local subprocess, BLE/USB handle, in-memory object) *or* because its backend refuses a fresh client on an existing session — the live session is pinned to its origin instance (see §6). The serializable recipe still lets another instance **re-create** an equivalent session from scratch; only the *live* session is unmovable.

The design names no specific backend. Whether any given driver falls on the migratable or sticky side is entirely a function of what it reports through the capability gate.

---

## 3. Split `Session` into state + runtime

Today `Session.__init__` (`common/session_manager.py`) eagerly builds live handles and mixes them with the declarative recipe. Separate the two.

### `SessionState` — the durable truth (Pydantic, in `models.py`)

```python
class SessionStatus(str, Enum):
    CREATING = "creating"
    ACTIVE = "active"
    DETACHED = "detached"     # runtime dropped, backend driver session still alive
    TERMINATED = "terminated"

class DriverBinding(BaseModel):
    driver_type: str                      # which optics driver (config key)
    endpoint: str                         # driver-tier address — the Layer-3 device pin
    reattach_handle: str | None           # opaque, driver-defined handle to reattach to
    capabilities: dict[str, Any]          # subset the driver needs to reattach
    device_id: str | None = None          # device key — Layer-3 scheduling
    migratable: bool = False              # what the driver declared (§6)

class SessionState(BaseModel):
    session_id: str
    config: dict[str, Any]                # normalized SessionConfig recipe
    apis: ApiData | None = None
    inline_templates: dict[str, str] = {} # image bytes as base64 — NOT temp paths
    driver_binding: DriverBinding
    status: SessionStatus = SessionStatus.CREATING
    owner_instance_id: str | None = None  # which instance holds the live runtime
    lease_expires_at: float | None = None
    busy: bool = False                    # in-flight execution / open stream
    metadata: dict[str, Any] = {}         # workspace hash, execution cursor, etc.
    created_at: float
    updated_at: float
```

`inline_templates` ships **bytes**, not the per-instance temp path — paths do not survive a move. `DriverBinding` is the linchpin for Layers 2 **and** 3: it pins each session to a concrete endpoint + device, and carries the reattach handle.

### `SessionRuntime` — the non-serializable handles

Exists only in the process that currently owns the session: `optics` (`OpticsBuilder`), the live `driver` (`InstanceFallback`), `event_sdk`, `event_queue`, temp dirs.

`Session` becomes `state: SessionState + runtime: SessionRuntime | None`.

This split is the move that makes all three layers coherent.

---

## 4. `SessionStore` — the seam for Layer 2 (define the whole interface now)

```python
class SessionStore(ABC):
    def put_state(self, s: SessionState) -> None: ...
    def get_state(self, sid: str) -> SessionState | None: ...
    def delete_state(self, sid: str) -> None: ...
    def list_states(self) -> Iterable[SessionState]: ...
    # Leasing: trivially always-granted in-memory; a real distributed lock in Redis.
    def acquire_lease(self, sid: str, instance_id: str, ttl: float) -> bool: ...
    def renew_lease(self, sid: str, instance_id: str, ttl: float) -> bool: ...
    def release_lease(self, sid: str, instance_id: str) -> None: ...
```

Layer 1 ships **only** `InMemorySessionStore` (leases always succeed). The critical anticipation: **`SessionManager`'s call sites already call `acquire`/`renew`/`release` in Layer 1**, even though the in-memory store does not need them. Layer 2 then adds `RedisSessionStore` as a pure new class plus a config switch — with zero call-site churn. Designing the lease methods into the interface *before* they are needed is what avoids the rework.

---

## 5. `SessionManager` = cache + rehydrator (the unifying path)

```python
def get_or_rehydrate(self, sid: str) -> Session | None:
    if sid in self._local_runtimes:                        # live here already
        return self._local_runtimes[sid]
    state = self.store.get_state(sid)
    if state is None:
        return None
    if not self.store.acquire_lease(sid, self.instance_id, ttl):
        raise SessionOwnedElsewhere(sid)                   # Layer 1: never fires
    runtime = self._reconstruct_runtime(state)             # rebuild + reattach driver
    session = Session(state, runtime)
    self._local_runtimes[sid] = session
    return session
```

In Layer 1 this runs only after an explicit import. In Layer 2 the *same function* handles a request the load balancer routed to a pod without the runtime. **Every endpoint calls `get_or_rehydrate` instead of the current `sessions.get()`** — that one substitution is what makes the API layer stateless.

### Reconstruction + strict attach

`_reconstruct_runtime(state)`:

1. Rebuild `Config` from `state.config`; build `OpticsBuilder`.
2. Feed `binding.get_reattach_params()` (endpoint + reattach handle) into the driver so it attaches rather than launching fresh. Only reached for drivers that declared themselves migratable (§6).
3. **Attach strictly** (see below).
4. Rewrite `inline_templates` bytes to a fresh local temp dir.
5. Re-establish instance-local machinery: the per-session `EventManager`, JUnit handler, logging, `event_queue` (see §8).

**Strict attach closes a footgun.** A driver's attach path may, by default, fall back to *silently creating a brand-new session* when reattach fails (a lenient mode that is correct for interactive first-launch). During reconstruction that is dangerous — a failed reattach would launch a fresh session on a possibly-wrong target and report success. Reconstruction must request the driver's **strict** attach (fail-closed: a failed reattach **raises**), leaving the lenient path only for genuine first-launch. Because attach behavior varies per driver, "strict attach" is a mode the `DriverInterface` reattach contract must expose, not a per-backend tweak.

### Detach vs terminate — the sharpest edge

- `terminate(sid)` → `driver.terminate()` (ends the backend session) + `delete_state` + cleanup. The session is truly gone.
- `detach(sid)` → drop the local runtime, `release_lease`, clean temp dirs + event manager, **but keep the backend driver session alive and keep `SessionState` in the store** (status → `DETACHED`).

This is why `DriverInterface.detach()` (§6) is distinct from `terminate()`: it drops the client handle without ending the backend session. A driver that cannot separate the two (non-migratable) inherits the default `detach()` = `terminate()`, which is the correct conservative behavior for a sticky session.

---

## 6. Driver capability gate (the one place migratability is decided)

Every driver answers the migration question for itself through one contract on `DriverInterface`:

```python
supports_session_migration: bool           # class attribute, default False
def get_reattach_params(self) -> dict: ...  # driver-defined: {reattach_handle, endpoint, device_id, ...}
def detach(self) -> None: ...               # default: falls back to terminate()
```

Optics reads only what the driver reports here — it never inspects the backend or hard-codes which products are reattachable. Two outcomes, treated uniformly:

| Driver declares | Why | Optics behavior |
|---|---|---|
| `supports_session_migration = False` (default) | in-process state (subprocess, BLE/USB, in-memory), **or** a backend that will not accept a fresh client on an existing session | `DriverBinding.migratable = False`; the live session is **sticky** — pinned to its origin instance, and `/migrate` refuses it. Default `detach()` = `terminate()` keeps it correct if never migrated. |
| `supports_session_migration = True` + valid `get_reattach_params()` | the backend holds the session and accepts a fresh client reattaching to it | `DriverBinding.migratable = True`; the session rehydrates / migrates freely via the reattach handle. |

A driver that opts in must implement `get_reattach_params()` and a `detach()` that drops the client handle **without** ending the backend session (§5).

This is how the design stays honest across a heterogeneous driver set without knowing any driver by name: opted-in drivers autoscale by moving their live session; everything else degrades gracefully to session affinity. Even for the sticky case, `SessionState` is fully serializable, so another instance can always **re-create** an equivalent session from the recipe — the capability gate only governs whether the *live* session moves, never whether a session can exist elsewhere.

---

## 7. The seams table — what each layer adds

| Concern | Layer 1 builds | Layer 2 adds | Layer 3 adds |
|---------|----------------|--------------|--------------|
| Session truth | `SessionState` in `SessionStore` | — (same schema) | — |
| Store backend | `InMemorySessionStore` (+ lease no-ops) | `RedisSessionStore` (real leases) | — |
| Session lookup | `get_or_rehydrate` | (unchanged — now cross-pod) | — |
| Ownership | `instance_id`, `owner_instance_id`, lease fields | leases enforced; orphan reclaim | — |
| Driver endpoint | `resolve_driver_binding(config)` seam → reads endpoint from config | — | seam body swapped for `DeviceRegistry` lookup |
| Device tier | `DriverBinding.endpoint` / `device_id` recorded | — | `DeviceRegistry` (free/busy) + placement |

The two seams that specifically buy Layer 3:

1. **`DriverBinding` already pins each session to a concrete endpoint + device.**
2. **All driver-endpoint selection routes through one `resolve_driver_binding(config)` function.** In Layer 1 its body just reads the driver endpoint from config. Layer 3 replaces the body with a registry lookup ("give me a free device") and adds a `DeviceRegistry` as another view over the same store.

Do not let a driver endpoint be read directly from config in scattered attach sites — funnel it through the seam, whatever the driver.

---

## 8. Instance-local state that must also move

The Help Wanted file list omits process-local globals that break the moment there are two instances. Layer 1 should route these through per-session state so Layer 2 does not have to retrofit them:

- Module-level `session_manager` and `workspace_hashes` (`common/expose_api.py`) — `workspace_hashes` moves into `SessionState.metadata` (or the store) so change-detection survives rehydration.
- The per-session `EventManager` registry, `setup_junit` / `cleanup_junit`, and `reconfigure_logging` — re-established during `_reconstruct_runtime`, and torn down on `detach` **without touching the driver**.

---

## 9. Endpoint semantics — and a simplification

Once the store + rehydrate + detach primitives exist, the three proposed endpoints collapse:

- `POST /v1/sessions/{id}/export` → `export_state(sid)` → `SessionState` JSON. For cross-cluster portability / debugging.
- `POST /v1/sessions/import` → `create_session_from_state(state)` → rehydrate + strict attach.
- `POST /v1/sessions/{id}/migrate` → **is just `detach`** (and only for a session whose driver declared itself migratable — §6). Detach releases the lease and leaves state + backend session alive; the next request (routed anywhere by the load balancer) rehydrates. In-cluster migration is *emergent*, not a bespoke path. For a non-migratable (sticky) session, `/migrate` refuses. `/migrate` is kept as sugar (optionally warm a named target), but the primitive is `detach`.

That collapse is the strongest signal the design is right: migration is not a feature you code, it is a consequence of the state/runtime split.

### Layer-1 scope boundaries (with hooks left in)

- **Idle-only migration.** `event_queue` / SSE streams are instance-local; refuse `detach` / `migrate` when `state.busy` or a stream is open. The `busy` flag + `status` anticipate active migration later.
- **No Redis, no scheduler, no active-execution handoff** — but the store interface, `DriverBinding`, and the resolve seam are all in place.

### Open decision: inline template bytes vs reference

`/export` can ship inline template **bytes** in the JSON (portable, self-contained, but large payloads), or a **reference** the target resolves from shared storage (small payloads, but assumes a shared filesystem / object store). Recommendation: **inline base64 in Layer 1** with a documented size cap; add reference-based export as a Layer-2 option once shared object storage exists. Templates resolved from `project_path` (`discover_templates`) already assume the target instance can see the same path — cross-instance migration of those requires shared storage regardless.

---

## 10. File-by-file change map

- `common/models.py` — `SessionState`, `DriverBinding`, `SessionStatus`.
- `common/session_manager.py` — `SessionStore` (ABC + `InMemorySessionStore`); `SessionManager` refactor to store + cache + rehydrate; `detach` / `terminate`; `instance_id`; `resolve_driver_binding`; `export_state` / `create_session_from_state`.
- `common/optics_builder.py` — accept reattach params / `DriverBinding`; thread `strict` into the attach path.
- `common/driver_interface.py` — the migration contract (`detach()`, `supports_session_migration`, `get_reattach_params()`) with safe defaults so existing drivers stay non-migratable until they opt in. This is where the normative behavior lives.
- `engines/drivers/<driver>.py` — per driver, *optionally* implement the contract (reattach handle, non-quitting `detach`, strict attach mode) to opt that driver into migration. Drivers that do nothing remain sticky and still work. Pick one reattachable driver as the reference implementation first.
- `common/expose_api.py` — `/export`, `/import`, `/migrate`; construct `session_manager` with an env-selected store; migrate `workspace_hashes` into state.

---

## 11. Suggested PR sequence

1. **State/runtime split + `SessionStore` interface + `InMemorySessionStore`** — pure refactor, no behavior change. De-risks everything.
2. **`export_state` / `create_session_from_state` + `/export` + `/import`** — same-instance round-trip test (export, detach without quit, import, keep driving).
3. **`detach` + capability gate + strict attach + instance-local cleanup.**
4. **`resolve_driver_binding` seam + fully-populated `DriverBinding`** — the Layer-3 anchor, cheap to add now.

Layer 2 (`RedisSessionStore` + enforced leases + cross-pod rehydrate) and Layer 3 (`DeviceRegistry` + placement) then land as new implementations behind these interfaces.

### Layer 2 — enabling the shared store (implemented)

`RedisSessionStore` (`common/session_store_redis.py`) implements the `SessionStore` interface with a real distributed lease, so it drops in with no call-site changes. It is selected from the environment when constructing the server's `SessionManager`:

| Env var | Default | Meaning |
|---------|---------|---------|
| `OPTICS_SESSION_STORE` | `memory` | `memory` → `InMemorySessionStore` (single process); `redis` → `RedisSessionStore`. |
| `OPTICS_REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL (used only when the backend is `redis`). |
| `OPTICS_LEASE_TTL_S` | `300` | Lease TTL in seconds — how long an orphaned session is held before another instance may reclaim it. The keepalive renews well within it. |

Install the optional dependency with `pip install optics-framework[stateless]` (adds `redis`).

Redis layout: the durable `SessionState` lives at `optics:session:{sid}` (JSON); a TTL'd `optics:lease:{sid}` holds the owning `instance_id`. Mutual exclusion rests on atomic `SET NX PX`; **orphan reclaim is automatic** — a pod that dies stops renewing, the lease key expires, and any other pod may then acquire it and rehydrate the session. When a request lands on an instance while another holds a live lease, `get_or_rehydrate` raises `SessionOwnedElsewhere`, surfaced by the API as **HTTP 409** so the caller retries against the owning pod (or after the current owner detaches). This is the cross-pod path the load balancer exercises implicitly; `/migrate` (detach) is how an owner voluntarily hands a migratable session off.

#### Layer-2 lease hardening (implemented)

Three robustness fixes once leases are enforced across pods:

- **Atomic lease CAS.** `renew`/`release` (and acquire's extend-if-ours branch) are single Lua scripts that compare the owner and mutate in one atomic step. A GET-then-conditional-SET/DEL leaves a window where a lease can expire and be re-acquired by another pod between the two calls — a renew would then *steal* the new holder's lease and a release would *delete* it. A fresh acquire stays a single `SET NX PX`.
- **Keepalive heartbeat.** Renewing only inside `get_or_rehydrate` ties lease-holding to request traffic: a live-but-idle session loses its lease at TTL and is reclaimed/reattached elsewhere while this pod still holds the runtime (the same backend session/device driven from two places). `SessionManager` runs a background loop that renews all live local sessions' leases every ~`TTL/3`, started/stopped from the FastAPI lifespan and a no-op under the in-memory store.
- **Store-outage handling.** `from_url` configures `redis-py`'s bounded `Retry` so a brief blip is ridden out transparently; once retries are exhausted a `RedisError` is translated to `SessionStoreUnavailable` and mapped to **HTTP 503** (retryable), without the API layer importing `redis`.

---

## Related Documentation

- [Help Wanted §1 — Stateless API Layer](help_wanted.md#1-stateless-api-layer)
- [Session Management](../architecture/components.md#sessionmanager)
- [REST API Layer](../architecture/api_layer.md)
