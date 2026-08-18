# Design: parallel sessions and a stateless `optics serve`

Status: approved for planning
Date: 2026-08-18
Companion: [`2026-08-18-parallel-sessions-audit.md`](2026-08-18-parallel-sessions-audit.md) — 62 findings with stable IDs (`A1`, `E2`, `G1`, …). This document cites those IDs rather than restating them.

## 1. Goals

1. **Per-process concurrency.** Many sessions execute simultaneously inside one process without interfering. Required by every deployment, local and served.
2. **Cross-pod statelessness for `optics serve`.** A newly started pod can serve a session created by a pod that no longer exists. The Docker image in this repo hosts the server; k8s routing is plain round-robin, so *any* pod must be able to serve *any* request.
3. **Redis is optional.** `optics execute`, `optics live`, the Python/Robot SDK, and a locally-run `optics serve` must work with zero Redis and zero extra configuration. Local behaviour stays byte-identical to today.

## 2. Non-goals

- **Authentication.** Deferred by decision; the server is treated as cluster-internal. Two near-zero-cost hardening fixes are still in scope (`I2`, `I4`). `I1` (session id as unscoped bearer capability), `I5`, `I6` are tracked follow-ups.
- **Statelessness for non-Appium backends.** Playwright launches an in-process browser subprocess and has no remote session id (`K`); Selenium's `get_driver_session_id` raises `NotImplementedError`; BLE and camera are exclusive local hardware. These stay supported and stay **pod-affine**.
- **Multi-process `optics execute` / `optics live`.** `D1` (`TreeResultPrinter` singleton), `D2` (`PytestRunner.instance`), `D4` (`Optics` at `scope="GLOBAL"`), `E6`, `E7` are out of scope. They do not affect `optics serve`, which uses `NullResultPrinter`. Tracked as follow-ups.
- **Replacing the SSE API shape.** `/events` and `/workspace/stream` keep their contract; only the transport behind them changes.

## 3. The one idea this rests on

`Appium.attach_to_session` (`optics_framework/engines/drivers/appium.py:391`) already reconstructs a live driver from an existing remote session: `SessionAttachmentWebDriver` intercepts the `newSession` command, returns a synthetic response carrying the target session id, and forces `driver.session_id`. It is reachable from config today via `SESSION_ID_CAP_KEYS` (`:65`) and `_try_attach_or_clear_session_caps` (`:184-210`), which falls back to creating a new session if attach fails. And `launch_app` (`:685`) only starts a session when `self.driver is None`, so a rehydrated session will not relaunch the app.

Therefore **the durable state of an Appium session is exactly `(appium_url, remote_session_id, capabilities)`** — three JSON-serializable values. Everything else a `Session` holds is either derived from config or is execution data that is already serializable. That is what makes goal 2 reachable without moving a live socket between processes.

## 4. Architecture: three seams, two adapters each

The entire local-vs-served difference is confined to three interfaces. One switch selects the backend for all three:

```
OPTICS_SESSION_BACKEND = memory | redis      # default: memory
OPTICS_REDIS_URL       = redis://...         # required only when backend=redis
```

`redis-py` ships as an optional extra (`optics-framework[redis]`), alongside the existing `[mcp]` and `[llm]` extras. Importing it is guarded exactly as `fastmcp` is in `helper/mcp_server.py`, so the default import path never needs it.

### 4.1 `SessionStore` — the session directory

```python
class SessionStore(Protocol):
    async def put(self, record: SessionRecord) -> None: ...
    async def get(self, session_id: str) -> SessionRecord | None: ...
    async def delete(self, session_id: str) -> None: ...
    async def touch(self, session_id: str) -> None: ...        # refresh TTL / last_seen
    async def list_ids(self) -> list[str]: ...
    async def expired(self, older_than: float) -> list[str]: ... # for the reaper
```

- **`MemorySessionStore`** — a dict holding the live `SessionRecord` **by reference**. No serialization, no copying, no TTL machinery beyond a timestamp. This is what makes local mode free.
- **`RedisSessionStore`** — one hash per session with a TTL. `SessionRecord` is serialized only here, at the adapter boundary.

`SessionManager.sessions` (`common/session_manager.py:153`) demotes from source-of-truth to a **cache of live `Session` objects**. It gains the `threading.Lock` it currently lacks (`H4`), matching the deliberate locking already present in `EventManagerRegistry` and `JUnitHandlerRegistry`.

### 4.2 `SessionLease` — mutual exclusion per session

A WebDriver session is not concurrency-safe, and `session.keyword_lock` is an `asyncio.Lock` (`common/session_manager.py:146`) which protects nothing across processes.

```python
class SessionLease(Protocol):
    @asynccontextmanager
    async def hold(self, session_id: str, *, ttl_s: float) -> AsyncIterator[None]: ...
```

- **`LocalLease`** — wraps today's per-session `asyncio.Lock`. Identical semantics to current behaviour.
- **`RedisLease`** — `SET <key> <token> NX PX <ttl>`, renewed by a background task while the keyword runs, released with a compare-and-delete Lua script so a lease that expired mid-keyword is never deleted by its original holder. Acquisition blocks with bounded retry, then returns `409 Conflict`.

The lease TTL must exceed the longest expected keyword. It is derived from the keyword's own timeout where one exists, with a configured floor.

### 4.3 `EventTransport` — fan-out to the SSE endpoints

```python
class EventTransport(Protocol):
    async def publish(self, session_id: str, event: dict) -> None: ...
    def subscribe(self, session_id: str) -> AsyncIterator[dict]: ...
    async def close(self, session_id: str) -> None: ...
```

- **`LocalEventTransport`** — today's per-session `asyncio.Queue`.
- **`RedisEventTransport`** — publish to a per-session channel; `subscribe` yields from a pub/sub subscription. Fire-and-forget with no backlog replay: a client that reconnects gets events from that point forward. This is stated in the API docs as the contract; adding replay means Redis Streams and is a follow-up, not part of this work.

The `/events` and `/workspace/stream` handlers change only in where they read from. `event_generator` also gains the session-liveness check that `workspace_generator` already has (`G7`).

## 5. `SessionRecord`: what is stored, what is derived

The schema is the contract. Anything not listed as *stored* must be reconstructible from what is.

**Stored** (all JSON-serializable):

| Field | Source today |
|---|---|
| `session_id` | `session_manager.py:157` |
| `config` | `Session.config`, a pydantic `Config` |
| `driver_kind` | e.g. `"appium"` |
| `reattach_handle` | `{appium_url, remote_session_id, capabilities}` — see §6 |
| `elements`, `modules`, `test_cases`, `apis` | `session_manager.py:113-118` |
| `inline_templates` | name → blob ref. Bytes go to the store, not a pod-local `mkdtemp` |
| `created_at`, `last_accessed` | new (`G3`) |
| `new_command_timeout_s` | new — see §7 |

**Derived, never stored** — reconstructed on rehydration: `driver` / `element_source` / text / image / LLM engines, `OpticsBuilder`, `KeywordRegistry`, all `StrategyManager`s, `EventSDK`, the JUnit handler, `EventManager`, the lease, and the event transport subscription.

**Removed from `Session` entirely:** `request_template_overrides` (`common/session_manager.py:119`). It is request-scoped data that currently lives on the session object, is written before the lock, and is `.clear()`ed in a `finally` outside it — so one request wipes another's templates (`H2`). It becomes a request-scoped resolver threaded through `ExecuteRequest` → `ExecutionParams`. This is a live bug fix today, independent of Redis.

## 6. Driver re-attach capability

`DriverInterface` (`common/driver_interface.py`) gains three members:

```python
supports_reattach: ClassVar[bool] = False
def get_reattach_handle(self) -> dict | None: ...
@classmethod
def attach(cls, handle: dict, **kwargs) -> Self: ...
```

- `Appium` sets `supports_reattach = True` and implements both against the existing `attach_to_session` machinery.
- Playwright, Selenium, BLE, camera inherit `False`.

`SessionStore.put` refuses to persist a `reattach_handle` for a driver that cannot re-attach. Such sessions are marked **pod-affine**: they still work, still appear in the directory, but a pod that does not own them returns `409` with a clear message rather than silently constructing a second, unrelated driver. This is what lets the other backends keep working while the Appium path goes stateless.

Selenium's equivalent is a known ~40-line port of the same `newSession`-interception trick and is a tracked follow-up, not part of this work.

## 7. Rehydration path

On a request for `session_id`:

1. **Local cache hit** → use it. (Common case; identical to today.)
2. **Miss** → `store.get(session_id)`.
   - `None` → `404`, as today.
   - Record exists, driver not re-attachable → `409` pod-affine.
   - Record exists with a handle → acquire the lease, then build a `Session` from the record with `existingSessionId` injected into capabilities, which routes through `_try_attach_or_clear_session_caps` (`appium.py:184`). Insert into the local cache.
3. Execute under the lease. Write back mutated record fields. `touch()` the TTL.

**`newCommandTimeout` is load-bearing and nothing sets it today.** Appium's default (60s) will reap the remote session during any gap between pods, after which rehydration produces a valid-looking handle to a session that no longer exists — surfacing as a confusing `E0201`. The record therefore carries an explicit `new_command_timeout_s`, injected into capabilities at session creation with a deliberate default, and rehydration failure is reported as a distinct error code rather than element-not-found.

**Idempotency.** A pod dying mid-keyword leaves the client unable to distinguish "tap happened" from "tap didn't". UI actions are not idempotent, so a blind retry double-taps. `execute_keyword` accepts an optional `Idempotency-Key` (the `execution_id` already minted at `expose_api.py:895` becomes client-suppliable) and caches `(session_id, key) → response` in the store with a short TTL. Included because retrofitting it after the API shape settles is more expensive than adding it now.

## 8. The async seam

**Exactly one place in the codebase converts sync to async.** Today the correct pattern exists at `common/execution.py:189-190` and nowhere else; the batch path calls keywords directly on the loop (`A1`).

Extract `run_keyword_blocking(session, method, *args, **kwargs)` in `common/execution.py`, holding the lease and dispatching to a **per-session `ThreadPoolExecutor(max_workers=1)`** rather than the shared default pool. Route both `KeywordExecutor.execute` and `TestRunner._try_execute_with_fallback` (`test_runnner.py:436`) through it.

Per-session single-thread executor rather than bare `asyncio.to_thread`, because:
- `to_thread` uses the loop's default executor (`min(32, cpu+4)`), a shared pool where one session's slow keyword starves another's, sized at startup when session count is unknown.
- `max_workers=1` makes per-session serialization *structural* rather than dependent on remembering the lock — which is exactly the bug in `H1`.
- Thread affinity for free, and a bounded footprint (1 thread per session, dies with the session).

After this change the invariant is greppable: `to_thread` / `run_in_executor` appear in exactly one module.

Engines stay synchronous. The seam is deliberately *not* `DriverInterface` or the API classes (`ActionKeyword`/`Verifier`/…) — those are the public SDK surface consumed synchronously by `optics.py:Optics` and Robot Framework, and making them async would break every consumer for no gain, since driver calls are HTTP round-trips that release the GIL.

## 9. Logging

Current state is the single worst isolation defect after the singletons: a `QueueHandler` with **no `QueueListener` anywhere in the repo** (`C1` — unbounded queue, never drained), global reconfiguration per session with accumulating file handlers (`C2`), Rich rendering on the loop thread at forced DEBUG (`C3`), and per-keyword `LogCaptureBuffer` attachment to a global logger (`C5`).

Target:
- Configure logging **once at process startup**, never per session. `ConfigHandler.__init__` stops calling `initialize_handlers` (`config_handler.py:140`); `create_session` stops calling `reconfigure_logging` (`expose_api.py:522`).
- Instantiate the `QueueListener` the code was written for, so the queue is actually drained and handler I/O leaves the loop thread.
- Session scoping via a `contextvars`-based filter keyed on `session_id`, replacing add/remove of handlers on a global logger. This also fixes `C5` without a lock.
- Server mode emits structured JSON to stdout at a configured level; stop forcing DEBUG (`expose_api.py:498`, `serve.py:49`).
- Lazy log formatting on the event hot path (`C4`), and `SensitiveDataFormatter` applied to the console handler too, not just file handlers (`C9`) — which is also the `I4` fix, since containers ship stdout.

## 10. Disk discipline

A pod must not accumulate files. `save_captures=False` is honoured by `Verifier` (`api/verifier.py:26`) but **ignored by `ActionKeyword` and both UI helpers** (`E8`), so today every keyword writes a JPG and rewrites `page_sources_log.xml` into the pod's working directory.

- Gate every write in `ActionKeyword`, `appium_UI_helper`, and `selenium_UI_helper` on `save_captures` (`E8`).
- Namespace `execution_output_path` under `<base>/execution_output/<session_id>/` (`E1`), which resolves most of `E3`–`E5` as a side effect.
- Replace the `page_sources_log.xml` read-modify-write with an append-only stream (`E2`). This is the most likely concrete corruption in the codebase and it fires on nearly every keyword; its failure mode is silent, because a short rewrite leaves trailing bytes past `</logs>` and the `endswith` guard then disables page-source logging for every session.
- Same treatment for `api_details.har` (`E3`).

## 11. Lifecycle

- `terminate_session` moves into a `finally` in `delete_session`, and driver-teardown failure becomes a warning (`G1`). Today a device reboot makes a session permanently un-evictable, with no other eviction path. The pre-call to `close_and_terminate_app` is dropped as redundant — `terminate_session` already calls `driver.terminate()`.
- The `create_session` failure path terminates the half-built session and stops swallowing `HTTPException` into a generic 500 (`G2`).
- `created_at` / `last_accessed` on the record, any request as a heartbeat, a background reaper, `GET /v1/sessions`, and a `max_sessions` cap returning `503` (`G3`, `G5`). In Redis mode the reaper is store-driven so it survives pod churn.
- A FastAPI `lifespan` that terminates local sessions on shutdown (`G4`), plus a readiness endpoint reporting session count and draining state so an orchestrator can distinguish healthy-but-full from idle (`G7` area).
- `--workers N` is currently advertised and silently broken (`G8`) — each worker gets its own module-global `SessionManager`, so a client gets a 404 on a session that demonstrably exists. It is gated to fail loudly unless `OPTICS_SESSION_BACKEND=redis`, under which it becomes genuinely correct.

## 12. Phasing

Phases 0–3 are local-correctness work with no new dependency. Each is independently shippable and green, and each is worth landing even if the Redis work never happens.

| Phase | Content | Closes |
|---|---|---|
| **0 — leaks & races** | `delete_session`/`create_session` cleanup; request-scoped template overrides; `keyword_lock` around the workspace stream; `EventManager` lifecycle moved to session create/terminate; gate `--workers`; drop the dead `_executor`; the two cheap security fixes | `G1` `G2` `H1` `H2` `H3` `B3` `G8` `I2` `I4` |
| **1 — the async seam** | `run_keyword_blocking` + per-session executor; route `TestRunner` and `delete_session` through it; fix `queue_event_sync`; `pytest.main` off-loop; add the missing busy-loop throttles | `A1` `A2` `A4` `A5` `B1` |
| **2 — logging** | Configure once at startup; instantiate the `QueueListener`; `contextvars` session filter; lazy formatting; sensitive formatter on console; structured stdout in server mode | `C1`–`C9` |
| **3 — disk & factories** | `save_captures` gating; per-session output dir; append-only page-source log and HAR; per-subclass factory registries and drop the instance cache | `E1`–`E5` `E8` `D3` |
| **4 — session store** | `SessionRecord`; `SessionStore` + `SessionLease` protocols; memory adapters; `SessionManager` demoted to a cache; TTL/reaper/cap/lifespan | `G3` `G4` `G5` `H4` |
| **5 — re-attach** | `DriverInterface` capability; `Appium` implementation; rehydration path; `newCommandTimeout`; idempotency keys | — |
| **6 — Redis adapters** | `RedisSessionStore`, `RedisLease`, `RedisEventTransport`; optional extra; Docker/k8s config; enable `--workers` | goal 2 |

Phases 4–6 are what deliver cross-pod statelessness. Phases 0–3 are prerequisites for them *and* standalone improvements to `optics execute` and `optics live`.

## 13. Testing

- **Phases 0–3** are unit-testable against the existing two trees (`tests/units/`, `tests/feature/`). The loop-blocking fixes need a regression test that asserts `/health` stays responsive while a slow keyword runs — the generalization of what `cfda461` did by hand.
- **Concurrency** needs a new test class: N concurrent sessions driving a fake driver, asserting no cross-talk in artifacts, logs, JUnit output, or events. This is the test that would have caught `D1`, `C2`, `C5`, and `E2`.
- **Phases 4–6** need a fake `SessionStore`/`SessionLease` for unit tests plus an integration test against a real Redis (containerized, skipped when absent), covering: rehydration on cache miss, lease contention between two `SessionManager` instances in one test process, and pod-affine refusal for a non-re-attachable driver.
- Both adapters run the same store/lease/transport conformance suite, so local and Redis modes cannot drift.

## 14. Risks

- **Rehydration latency.** First touch by a new pod pays an attach handshake plus a `StrategyManager` rebuild. The rebuild is already a per-request cost today (`A8`) and is fixed by caching the registry on the session — worth doing in phase 4 regardless.
- **`newCommandTimeout` tuning is deployment-specific.** Too low and sessions die between pods; too high and abandoned sessions hold devices until the reaper fires. The reaper and the timeout must be tuned together.
- **GIL contention from the vision tier.** Driver calls release the GIL, but OpenCV template matching and OCR do not. If vision strategies fire on most keywords, per-process throughput will degrade in a way threads cannot fix. Measure with `execution_tracer` output before committing to a session-count target; the fix if it bites is a separate process pool for vision only.
- **Pub/sub has no replay.** A client reconnecting after a pod death loses backlog. Stated as the contract; Redis Streams is the follow-up if that proves unacceptable.
- **CLAUDE.md drift.** Section `L` of the audit lists stale anchors and two documented-but-false claims (`logs.json` is never written; JUnit is already session-scoped). Fixed as part of this work, per the repo's own hard rule.

## 15. Tracked follow-ups (explicitly out of scope)

`I1` auth / session-token, `I5` `project_path` validation, `I6` rate limiting, `D1` `D2` `D4` CLI/SDK singletons, `E6` `E7` `optics live` collisions, `A12` live TUI blocking, Selenium `attach_to_session` port, `F1` `F2` port/device allocation for local parallel Appium, Redis Streams backlog replay.

`F1` deserves a note: `systemPort` / `wdaLocalPort` / `mjpegServerPort` have **zero occurrences in the tree** and are mandatory-unique for parallel Appium sessions. It is out of scope here only because the served deployment delegates device management to a Grid or cloud farm that assigns them. Anyone running parallel sessions against a local Appium server will hit it immediately.
