# Roadmap

:material-progress-clock: **Status: In progress** — Phase 0 has landed (with one known hole, below). Everything from Phase 1 onward is designed but not started.

Work is phased so that **each phase ships on its own merits**. Phases 0–3 are local-correctness fixes with no new dependencies; they improve `optics execute` and `optics live` regardless of whether the stateless work ever happens. Phases 4–6 deliver cross-pod statelessness and depend on 0–3 being done first.

Finding IDs (`A1`, `E2`, `G1`, …) refer to the [audit](../superpowers/specs/2026-08-18-parallel-sessions-audit.md).

For per-phase scope, task breakdown, exit criteria and risks, see [Phase Plans](phase-plans.md).

## Phase 0 — Leaks & races

:material-check-circle: **Landed.**

**Ships:** two guaranteed session leaks closed, the three known cross-request corruption paths in `optics serve` closed, `--workers` made honest.
**Closes:** `G1` `G2` `H1` `H2` `H3` `B3` `G8` `I2` `I4` (and `A2` as a side effect).
**New dependencies:** none. **API change:** `DELETE /v1/sessions/{id}/stop` now returns `404` for an unknown or already-deleted session instead of `200`.

!!! warning "One race is deliberately left open"
    Phase 0 does **not** eliminate cross-request corruption outright. Three of the four `keyword_lock` call sites are now correct, but `asyncio.to_thread` is **not cancellable**: when an SSE client disconnects, `CancelledError` propagates at the `await` and `async with session.keyword_lock` releases the lock **while the driver command is still running in the abandoned thread**. A pending request then acquires the lock and issues a second command on the same remote session.

    This cannot be fixed by adding a lock — it is a property of the offload primitive. Phase 1's per-session `ThreadPoolExecutor(max_workers=1)` closes it structurally: the next command cannot start until the worker thread is free, whoever holds the lock.

| Task | What |
|---|---|
| 1 | `delete_session` always evicts, even when driver teardown fails — it is the only eviction path |
| 2 | `create_session` reclaims the device when auto-launch fails, and stops flattening `HTTPException` into 500 |
| 3 | Request template overrides move to a `ContextVar` — off the shared `Session` |
| 4 | The workspace SSE stream takes `keyword_lock` before issuing driver commands |
| 5 | Guard the `session.apis` swap; end the event stream with its session |
| 6 | `EventManager` lifecycle moves from per-request to per-session |
| 7 | `--workers > 1` fails loudly instead of returning sporadic 404s |
| 8 | Stop logging raw capabilities; drop credentialed wildcard CORS; delete dead code |

Follow-ups from the final review, landed on top: teardown takes `keyword_lock` (Task 1 had dropped the serialization the removed `close_and_terminate_app` call used to provide); `EventManager.stop()` cancels its dispatch task through `call_soon_threadsafe` when called from a worker thread; capability *values* are redacted at every driver log site, not just in `create_session`.

[Full plan](../superpowers/plans/2026-08-18-phase0-session-leaks-and-races.md)

## Phase 1 — The async seam

**Ships:** the change that actually unlocks concurrency. Until this lands, a keyword blocks every other session in the process.
**Closes:** `A1` `A2` `A4` `A5` `B1`.

- Extract `run_keyword_blocking(session, method, *args)` as the single sync→async seam, dispatching to a **per-session `ThreadPoolExecutor(max_workers=1)`** under the session lease.
- Route `TestRunner._try_execute_with_fallback` through it — this is `A1`, the big one.
- Fix `queue_event_sync`, which calls `asyncio.run` under a running loop and swallows the failure, silently dropping **100% of events** in `--runner pytest` mode.
- Move `pytest.main()` off the event loop.
- Add the missing throttles to the two element-source poll loops that currently spin with no sleep.
- Add the standing regression: `/health` stays responsive while a slow keyword runs.
- Close the cancellation hole Phase 0 leaves open (see the warning above): with a single worker per session, a cancelled request cannot let a second command start while the first is still in flight.

!!! warning "Carries a trap from Phase 0"
    `asyncio.to_thread` propagates `contextvars`; `loop.run_in_executor` does not. The template-override fix from Phase 0 Task 3 relies on that propagation. The executor swap must dispatch via `contextvars.copy_context().run(...)` or it regresses **silently**, surfacing as a spurious element-not-found.

## Phase 2 — Logging

**Ships:** logs you can actually attribute to a session, and an end to the unbounded queue leak.
**Closes:** `C1`–`C9`.

- Instantiate the `QueueListener` the code was written for — it does not exist anywhere in the repo today.
- Configure logging **once at startup**, not per session.
- Session scoping via `contextvars` + a filter, replacing add/remove of handlers on a global logger.
- Lazy formatting on the event hot path; `SensitiveDataFormatter` on the console handler.
- Structured JSON to stdout in server mode.

## Phase 3 — Disk & factories

**Ships:** a server that doesn't fill its own disk, and artifacts that survive concurrency.
**Closes:** `E1`–`E5` `E8` `D3`.

- `execution_output_path` becomes `<base>/execution_output/<session_id>/`.
- `page_sources_log.xml` and `api_details.har` become append-only — no more read-modify-write, no more silent corruption.
- `save_captures` gates every writer, not just `Verifier`.
- Per-subclass factory registries; drop the instance cache that retains every terminated session's driver forever.

## Phase 4 — Session store

**Ships:** the `SessionRecord` abstraction and working lifecycle management. In-memory only — no Redis yet.
**Closes:** `G3` `G4` `G5` `H4`.

- `SessionRecord` schema — the contract for what may be session state.
- `SessionStore` and `SessionLease` protocols with in-memory adapters.
- `SessionManager.sessions` demoted from source-of-truth to cache.
- TTL, `last_accessed`, a reaper, `max_sessions` with `503` backpressure, `GET /v1/sessions`, and a FastAPI `lifespan` that terminates sessions on shutdown.

## Phase 5 — Re-attach

**Ships:** a session that survives the process that created it.

- `supports_reattach` / `get_reattach_handle()` / `attach()` on `DriverInterface`.
- Appium implementation, wired to the `attach_to_session` machinery that already exists.
- Rehydration on cache miss; pod-affine refusal (`409`) for backends that can't re-attach.
- `newCommandTimeout` becomes explicit and persisted.
- Idempotency keys on `POST /action`.

## Phase 6 — Redis adapters

**Ships:** goal reached — any pod serves any session.

- `RedisSessionStore`, `RedisLease`, `RedisEventTransport`.
- `optics-framework[redis]` optional extra.
- Docker/k8s configuration; `--workers` re-enabled under the Redis backend.

## Known risks

| Risk | Why it matters | How we'd find out |
|---|---|---|
| **`newCommandTimeout` across pod restarts** | If Appium reaps the remote session during a restart, rehydration produces a valid-looking handle to a dead session. This is the single biggest unknown in Phase 5–6. | Measurable early against a real device tier — worth doing *before* building on it. |
| **GIL contention from the vision tier** | Driver calls release the GIL; OpenCV template matching and OCR do not. If vision strategies fire on most keywords, per-process throughput degrades in a way threads can't fix. | Measure with `execution_tracer` output before committing to a session-count target. Fix, if needed, is a separate process pool for vision only — much cheaper than process-per-session. |
| **Pub/sub has no replay** | A client reconnecting after a pod death loses backlog. | Stated as the contract. Redis Streams if it proves unacceptable. |
| **Rehydration latency** | First touch by a new pod pays an attach handshake plus a `StrategyManager` rebuild. | The rebuild is already a per-request cost today (`A8`); caching the registry per session fixes both. |

## Explicitly out of scope

Tracked, not scheduled:

- **Authentication** (`I1`), `project_path` validation (`I5`), rate limiting (`I6`). The server is treated as cluster-internal. Note that statelessness makes `session_id` a *portable* bearer capability — this becomes more urgent, not less, once Phase 6 lands.
- **CLI/SDK singletons** (`D1` `D2` `D4`) — parallel sessions for `optics execute` and the Robot/Python SDK. These don't affect `optics serve`.
- **`optics live` collisions** (`E6` `E7` `A12`).
- **Port and device allocation** (`F1` `F2`) — the served deployment delegates this to a Grid or cloud farm. Anyone running parallel sessions against a *local* Appium server hits it immediately; see [Resource Isolation](resource-isolation.md#mitigation-today) for the manual workaround.
- **Selenium `attach_to_session`** — a known ~40-line port of the Appium trick.
- **`httpx` is undeclared** in the dev/test dependency groups, yet every `TestClient` test needs it; a fresh `poetry install --with dev,test` cannot run them.
