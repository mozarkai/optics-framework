# Parallel Sessions & Stateless Serve — Program Reference

:material-check-circle: **Status: Current** — the single complete record of this program: the problem, how it was investigated, every assumption, every decision and why it was made, the design, all seven phases, what has shipped, and what remains.

Everything else in this section is a focused view of what is below. Where a focused page disagrees with this one, **the focused page wins** — it is closer to the code.

**Companion documents**

| Document | What it holds |
|---|---|
| [Parallel-session audit](../superpowers/specs/2026-08-18-parallel-sessions-audit.md) | All 62 findings with `file:line` anchors, two-session failure scenarios, and severities |
| [Stateless-serve design](../superpowers/specs/2026-08-18-parallel-sessions-stateless-serve-design.md) | The agreed architecture in full |
| [Phase 0 plan](../superpowers/plans/2026-08-18-phase0-session-leaks-and-races.md) | The eight-task TDD plan |
| [Phase 0 execution record](../superpowers/records/2026-08-19-phase0-execution-record.md) | Commits, decisions, plan defects, review findings |
| [Known issues](known-issues.md) | The live backlog |

---

## 1. The problem

`optics serve` could not safely run two sessions at once. The symptom set was diffuse — intermittent 404s, spurious element-not-found failures, sessions that could not be deleted, JUnit output that silently stopped — which is why it had not been pinned down before.

### 1.1 How it was investigated

Five independent audits ran in parallel, each with a different lens, so no single perspective's blind spot could hide a family of defects:

| Lens | Scope |
|---|---|
| **State isolation** | Module-level globals, singletons, class attributes used as storage, registries, output-path collisions |
| **Sync/async correctness** | Blocking calls reachable from `async def`, event-loop starvation, sync↔async bridges, lock discipline |
| **HTTP layer** | Request lifecycle, in-process state inventory, statelessness constraints, lifecycle gaps |
| **Filesystem & external resources** | Artifact collisions, temp dirs, ports, device exclusivity, process-global side effects |
| **Architecture options** | The design space for concurrency and statelessness, grounded in what the code actually does |

Result: **62 distinct findings**, each with a `file:line` anchor, a concrete two-session failure scenario, and a severity. They are referenced throughout by stable ID (`A1`, `E2`, `G1`, …).

### 1.2 The four independent failure families

Any **one** of these was sufficient to break concurrency. Fixing three would have changed nothing observable — which is why the problem resisted incremental fixes.

**Family 1 — the keyword path blocks the event loop.**
`TestRunner._try_execute_with_fallback` (`optics_framework/common/runner/test_runnner.py:436`) calls the bound keyword method directly, with no thread offload, from an entirely `async` call chain. The loop is dead for the whole call: ~200 ms for a tap, up to 30 s for an `Assert Presence`, and the entire duration of the `Sleep` keyword, which is a bare `time.sleep`. While blocked, the event dispatcher cannot run — so the live tree and JUnit XML only advance *between* keywords — and under `optics serve`, `/health` stops responding.

Underneath it, two element sources poll a remote driver in a `while` loop **with no sleep at all** (`appium_page_source.py:211`, `appium_find_element.py:203`); the throttle exists in the source as a commented-out line. A 30-second assertion becomes thousands of Appium round-trips.

**Family 2 — five process-global singletons hold per-session state.**

| Singleton | What breaks |
|---|---|
| `TreeResultPrinter._instance` | `test_state` is *replaced* wholesale per session. Session A's rows vanish, its `_update_status` silently no-ops, its live tree freezes with no error, and pass/fail is computed over the union of both sessions' test cases |
| `PytestRunner.instance` | A class attribute wired into a generated `conftest.py`; session A's generated tests execute against session B's runner and driver |
| `GenericFactory._registry` | One registry shared by **all five** factory subclasses; every terminated session's driver and OCR reader is retained forever |
| The logging manager | Every session reconfigures global logging (see Family 2b) |
| `Optics` (SDK facade) | `@library(scope="GLOBAL")` with one session slot; a second `Setup` orphans the first session's driver and temp dir without terminating them |

**Family 2b — logging deserves its own note**, because the diagnosis was counter-intuitive. The problem is not that handlers are synchronous:

- A `QueueHandler` feeds `execution_log_queue`, but **`QueueListener` appears zero times in the entire repository**. Nothing drains it. Unbounded growth for the process lifetime; `clear_queues()` only runs at `atexit`. `execution_console_handler` is constructed and formatted and then **never attached to any logger** — execution logs reach a console only via `propagate = True`.
- `ConfigHandler.__init__` calls `initialize_handlers` **per session**, adding a `RotatingFileHandler` deduplicated only by filename and never removed. Session B starting at `DEBUG` silently re-levels session A, and A's lines are thereafter written into B's log file as well.
- `optics serve` replaces the root logger's handlers and forces `DEBUG`, so every `urllib3` / `selenium` / `asyncio` record is Rich-rendered — markup parse, syntax highlight, terminal measure, write — **on the event loop thread**.
- `LogCaptureBuffer` is attached to the *global* execution logger per keyword, so session A's JUnit `<log>` elements capture session B's records.

**Family 3 — artifacts are scoped per *project*, not per *session*.**
`execution_output_path` resolves to `<project_path>/execution_output`, or `os.getcwd()/execution_output` when unset. Every writer shares it. Two are non-atomic read-modify-writes:

- `page_sources_log.xml` — opened `r+`, read whole, trailing `</logs>` stripped, rewritten. Called on nearly every keyword. Two writers lose entries; and because `r+` does not truncate, a shorter rewrite leaves stale bytes past `</logs>`, after which the `endswith` guard fails and **page-source logging silently stops for every session**. This is the most likely concrete corruption in the codebase.
- `api_details.har` — same shape, and its `except json.JSONDecodeError` handler **discards the entire accumulated history** on a partial write.

Compounding it: `optics serve` sets `save_captures=False` precisely so HTTP sessions do not touch disk, but that flag is honoured by `Verifier` only — `ActionKeyword` and both UI helpers write regardless.

**Family 4 — nothing allocates exclusive external resources.**
`systemPort`, `wdaLocalPort`, `mjpegServerPort`, `chromedriverPort` have **zero occurrences in the tree**, yet they are mandatory-unique per parallel Appium session (UiAutomator2 defaults to 8200, WDA to 8100). There is no device lease, no lockfile, no port allocator — `fcntl`, `flock`, and `filelock` also appear nowhere. Two sessions from one `config.yaml` are either rejected by Appium or race for port 8200, after which **commands land on the wrong device**.

### 1.3 Findings by category

| ID range | Category | Count |
|---|---|---|
| `A1`–`A15` | Blocking the event loop | 15 |
| `B1`–`B3` | Sync/async bridge bugs | 3 |
| `C1`–`C9` | Logging | 9 |
| `D1`–`D5` | Process-global singletons | 5 |
| `E1`–`E8` | Filesystem / artifact collisions | 8 |
| `F1`–`F4` | External resource exclusivity | 4 |
| `G1`–`G8` | Lifecycle / ops gaps | 8 |
| `H1`–`H4` | Concurrency within serve | 4 |
| `I1`–`I6` | Security | 6 |

### 1.4 Two live bugs nobody knew about

- **`B1`** — `queue_event_sync` calls `asyncio.run()` from inside a running loop, which always raises, and the handler swallows it as a warning. **In `--runner pytest` mode, 100% of events are dropped** — no JUnit XML, no tree output, no error surfaced. Verified empirically.
- **`G8`** — `--workers N` is advertised and wired to uvicorn, but `session_manager` is a module global per process. A session created on worker 1 returns `404` from worker 3, roughly `(N-1)/N` of the time, with no diagnostic.

### 1.5 What was already correct

Worth stating precisely, because it defines what *not* to rewrite:

- **The correct async pattern already existed**, in exactly one place — `optics_framework/common/execution.py:189`: `async with session.keyword_lock:` around `await asyncio.to_thread(method, ...)`. Sync engines, one seam, per-session lock. This is the template.
- **A working Appium re-attach path already existed** at `optics_framework/engines/drivers/appium.py:391` — unreachable from HTTP, and the single highest-leverage asset for statelessness.
- Session-scoped registries with real locks (`EventManagerRegistry`, `JUnitHandlerRegistry`), already writing `junit_output_<session_id>.xml`.
- Per-session temp dirs, correctly unique and correctly cleaned.
- `OpticsBuilder`, `StrategyManager`, `KeywordRegistry`, `EventSDK`, `InstanceFallback` — all genuinely per-session.
- No `os.chdir`, no `os.environ` mutation, no `sys.path` mutation, no `lru_cache` singletons anywhere.

---

## 2. Assumptions

Stated explicitly, because several were verified against the code rather than taken on trust, and one turned out to constrain the whole design.

| # | Assumption | Status |
|---|---|---|
| A1 | Driver calls are I/O-bound HTTP round-trips that release the GIL, so **threads are the right concurrency primitive** and making the engine layer `async` would buy nothing | Holds. Also: the engine layer *is* the public SDK, so async would break Robot Framework and every Python consumer |
| A2 | The vision tier (OpenCV template matching, OCR) is CPU-bound and gains nothing from async | Holds. If vision fires on most keywords, per-process throughput degrades in a way threads cannot fix — **measure before committing to a session-count target** |
| A3 | A live `webdriver.Remote` cannot be serialized or moved between processes | Holds — but the *handle* can. See §4.1 |
| A4 | An Appium session's durable state is exactly `(server_url, remote_session_id, capabilities)` | **Verified.** `attach_to_session` reconstructs from these three, and `launch_app` no-ops when a driver already exists, so a rehydrated session will not relaunch the app |
| A5 | `contextvars` propagate through `asyncio.to_thread` | **Verified — and load-bearing.** They do *not* propagate through `loop.run_in_executor`, which Phase 1 will introduce |
| A6 | `asyncio.Queue()` / `asyncio.Lock()` constructed off-loop in `Session.__init__` are safe | Holds **only** on Python 3.12+ because both bind their loop lazily. Do not rewrite them to bind eagerly |
| A7 | The server tier is effectively an Appium wrapper | **User-confirmed.** This is what makes statelessness reachable at all — see §3.3 |
| A8 | Starlette does not cancel plain HTTP handlers on client disconnect | Holds; it *does* cancel SSE generators, which is where the remaining race bites |

---

## 3. Decisions

Every decision, what was chosen, and why. Several revised earlier answers as constraints emerged — those revisions are shown rather than hidden, because the reasoning is the useful part.

### 3.1 Where do the devices live?

**Answer: all four** — local USB / same host, self-hosted Appium or Selenium Grid, cloud farm (BrowserStack / Sauce / Device Farm), and in-process Playwright.

**Why it matters.** This is the most demanding possible answer, and it settles something immediately: **"completely stateless" cannot be a global property.** Local-USB affinity is *physical* — no external store makes a USB-attached phone reachable from another host. In-process Playwright launches its own browser subprocess over a pipe and has no remote session id, so it cannot leave the process either. A cloud farm, by contrast, is already stateless at the device tier.

**Consequence for the design:** statelessness becomes a **per-backend capability that a driver declares**, with sticky affinity as the honest fallback where it is absent — not a blanket property of the server.

### 3.2 One process, or many?

**First answer: one process with real concurrency.** Then revised.

**Revised answer: both.** Per-pod, real in-process concurrency; across pods, genuine statelessness — a new pod must serve a session created by a pod that no longer exists.

The user's clarification was decisive: *"the docker image in this repo is used to host the server… if a new pod is launched, it should be able to support existing running sessions."* These are not in conflict; both are required.

**Why the first answer was insufficient:** single-process concurrency delivers throughput but not survivability. A pod restart would drop every live session, which is exactly what the deployment cannot tolerate.

### 3.3 Which surfaces need parallel sessions?

**Answer: `optics serve` and `optics mcp` only** — and, critically, *"at the end it's just a wrapper around Appium."*

**Why that sentence unlocked the design.** Statelessness was blocked by two things: in-process Playwright and local-USB affinity. If the *served* tier is Appium-only, **both drop out of scope**, and the path is already half-built in the codebase. The other drivers stay supported and are declared pod-affine.

**Deliberately out of scope:** `optics execute`, `optics live`, and the Python/Robot SDK. Their singletons (`D1`, `D2`, `D4`) do not affect `optics serve`, which sidesteps the result-printer problem entirely by using `NullResultPrinter`.

### 3.4 What happens to the SSE event streams?

**Answer: Redis pub/sub, one channel per session.**

`session.event_queue` is an in-process `asyncio.Queue` with two consumers (`/events`, `/workspace/stream`). It is the one thing that cannot follow a session across pods without replacing the transport — and the audit flagged it as **the most commonly under-scoped part of going multi-pod**, a larger change than replacing the session registry.

**Alternatives rejected:** dropping SSE from the stateless contract (cheapest, but the workspace stream implies a human-facing console that needs it); replacing streams with cursor polling (a breaking API change for existing consumers).

**Accepted trade-off:** pub/sub is fire-and-forget. A client reconnecting after a pod death gets events from that point forward, **not the backlog**. Redis Streams would fix it and is a tracked follow-up, not part of this work.

### 3.5 Is auth in scope?

**Answer: no — cluster-internal, deferred.** With two near-zero-cost hardening fixes taken anyway: stop logging raw capabilities (`I4`), and drop `allow_credentials` from the wildcard CORS (`I2`).

**What is being accepted.** `session_id` is an unscoped bearer capability in the URL path with no ownership check. Anyone who can reach the port can drive any session, including `DELETE`. **Statelessness makes this worse, not better** — an external registry turns the id into a *portable, cross-node* capability. `get_driver_session_id` additionally hands out the raw Appium session id, which combined with `attach_to_session` lets a caller bypass optics entirely.

This is a deliberate, informed deferral. It should be revisited before Phase 6, not after.

### 3.6 Must Redis be optional?

**Answer: yes — and this reshaped the architecture for the better.**

The constraint: *"we need to support both local execution and server execution, so Redis shouldn't be a hard blocker for local execution."*

`optics execute`, `optics live`, the SDK, and a locally-run `optics serve` must work with **zero Redis and zero configuration**, and local behaviour must stay identical to today.

**Why this improved the design.** It forced the stateful concerns behind three narrow interfaces instead of scattering Redis calls through the request path. The in-memory adapter holds the live record *by reference* and never serializes, so local mode pays nothing. It also forced a `SessionRecord` schema, which is useful independently: it defines what is *allowed* to be session state, and it immediately exposed `request_template_overrides` as request-scoped data wrongly living on the session — a live bug, fixed in Phase 0.

### 3.7 Where should the sync/async seam live?

**Answer: `KeywordExecutor.execute` — the one place where every async caller meets a bound sync keyword method.**

**Rejected alternatives, with reasons:**

- **`DriverInterface`** — ~40 methods × 4 drivers, plus 11 element sources, plus the vision tier. Buys nothing (these are I/O calls) and breaks the public SDK, which calls this tier synchronously.
- **The API classes** (`ActionKeyword` / `Verifier` / …) — same problem; they *are* the SDK surface.
- **The `expose_api` handlers** — then `optics mcp` and the batch runner each need their own wrap, recreating exactly the divergence that produced `A1`.

**Refinement:** a per-session `ThreadPoolExecutor(max_workers=1)` rather than bare `asyncio.to_thread`, because `to_thread` uses a shared default pool where one session's slow keyword starves another's, and because `max_workers=1` makes serialization **structural** rather than dependent on remembering to take a lock — which is precisely how the workspace-stream bug (`H1`) happened.

### 3.8 The concurrency model actually chosen

Considered and rejected, with reasons:

| Option | Verdict |
|---|---|
| **A — asyncio + per-session bounded executor, per-session lock** | **Chosen.** The seam already exists in one place; migration is incremental; zero change to `DriverInterface` or the SDK |
| B — one dedicated worker thread per session | Rejected: this is A with a hand-rolled scheduler. `ThreadPoolExecutor(max_workers=1)` gives the same thread affinity for free |
| C — process per session | Rejected: buys isolation against a failure mode that does not exist here (the crash-prone tier — Appium server, browser — is *already* out of process), and taxes every screenshot with a pickle across a process boundary |
| **D — multi-process + sticky routing + external registry** | **Adopted as Phase 6**, in the stronger form: rehydration rather than sticky routing, so plain round-robin works |
| **E — delegate statefulness to a Grid** | **Composes with D.** The Grid owns device lifecycle; optics holds a re-attachable handle |
| F — task queue (Celery/arq/RQ) | Rejected: every keyword becomes two round-trips. For an LLM-agent client doing tight observe-act loops, that latency *is* the product. It also relocates the affinity problem into the queue's routing keys rather than solving it |

---

## 4. The design

### 4.1 What can and cannot be stateless

Being honest here matters more than the architecture itself, because the wrong assumption produces a design that cannot work.

| Layer | Stateless? | Note |
|---|---|---|
| HTTP request handling | **Already is** | No request context beyond path params |
| Keyword registry, API instances | **Derived, not state** | A pure function of config; caching it per session is also a performance fix |
| Execution state (elements, modules, test cases, apis, templates) | **Yes** | All JSON-serializable |
| Artifacts | **Already is for serve** | `save_captures=False`, screenshots returned as base64 |
| Session registry | **Directory yes, object no** | The map is externalizable; a live driver is not |
| Event / SSE stream | **No, without replacing the transport** | Solved by Redis pub/sub (§3.4) |
| **Appium** driver | **Effectively yes** | Re-attachable from three JSON values |
| **Selenium** driver | **~40-line port** | Same `newSession`-interception trick; simply not written |
| **Playwright** driver | **No — hard constraint** | In-process browser subprocess, no remote session id, no `connect_over_cdp` |
| **Local USB devices** | **No — physical, not architectural** | No amount of external state helps |

**The definition adopted:** *stateless `optics serve`* means (a) no server-local **authoritative** state, (b) any instance can serve any request given the externalized handle, and (c) a process restart loses no session.

### 4.2 Three seams, two adapters each

The entire local-versus-served difference is confined to three interfaces. One switch selects the backend for all three:

```
OPTICS_SESSION_BACKEND = memory | redis    # default: memory
OPTICS_REDIS_URL       = redis://...       # only when backend=redis
```

`redis-py` ships as an optional extra (`optics-framework[redis]`), imported behind a guard exactly as `fastmcp` already is.

| Seam | Local default (no dependency) | Redis adapter |
|---|---|---|
| **`SessionStore`** — session directory and record | a dict holding the live record **by reference**; no serialization at all | one hash per session with a TTL; serialization happens only at this boundary |
| **`SessionLease`** — mutual exclusion per session | today's per-session `asyncio.Lock` | `SET NX PX` with background renewal and compare-and-delete release |
| **`EventTransport`** — fan-out to the SSE endpoints | today's in-process `asyncio.Queue` | pub/sub channel per session |

`SessionManager.sessions` demotes from source-of-truth to a **cache of live `Session` objects**. The cache exists in both modes; only *authority* differs.

### 4.3 `SessionRecord` — the contract

Anything not listed as stored must be reconstructible from what is.

**Stored** (all JSON-serializable): `session_id`, `config`, `driver_kind`, `reattach_handle` (`appium_url`, `remote_session_id`, `capabilities`), `elements` / `modules` / `test_cases` / `apis`, `inline_templates` as blob references, `created_at` / `last_accessed`, `new_command_timeout_s`.

**Derived, never stored:** the driver and element source, all vision and LLM engines, `OpticsBuilder`, `KeywordRegistry`, every `StrategyManager`, `EventSDK`, the JUnit handler, the `EventManager`, the lock, the event subscription.

**Removed from `Session` entirely:** `request_template_overrides` — request-scoped data that was living on the shared session object. Fixed in Phase 0.

### 4.4 Driver re-attach capability

`DriverInterface` gains three members so the store can tell what is movable:

```python
supports_reattach: ClassVar[bool] = False
def get_reattach_handle(self) -> dict | None: ...
@classmethod
def attach(cls, handle: dict, **kwargs) -> Self: ...
```

Appium sets `supports_reattach = True`. Playwright, Selenium, BLE and camera inherit `False`, and the store refuses to persist a handle for them — those sessions are marked **pod-affine**, and a pod that does not own one returns `409` with a clear message rather than silently constructing a second, unrelated driver.

### 4.5 Two things that will bite

**`newCommandTimeout` is load-bearing and nothing sets it today.** Appium's default is 60 seconds. If a pod restart takes longer, the remote session is reaped and the new pod faithfully rehydrates a handle to a session that no longer exists — surfacing as a confusing element-not-found. The record carries it explicitly, and rehydration failure gets its own error code. It must be tuned **together with** the session reaper: too low and sessions die between pods; too high and abandoned sessions hold devices.

**An `asyncio.Lock` protects nothing across pods.** `session.keyword_lock` is the only thing preventing interleaved WebDriver commands, and a WebDriver session is not concurrency-safe. Distributed serialization needs a distributed *lease* — which is why `SessionLease` is its own seam rather than a detail of the store.

---

## 5. The phases

Phases 0–3 are local-correctness work with no new dependency; each ships on its own merits and improves `optics execute` and `optics live` regardless. Phases 4–6 deliver statelessness and depend on 0–3.

| # | Phase | Delivers | Closes |
|---|---|---|---|
| **0** | **Leaks & races** ✅ landed | Two guaranteed session leaks closed; three cross-request corruption paths closed; `--workers` made honest | `G1` `G2` `H1` `H2` `H3` `B3` `G8` `I2` `I4` (+`A2`) |
| **1** | The async seam | The change that actually unlocks concurrency: one sync→async seam on a per-session single-worker executor; `queue_event_sync` fixed; `pytest.main` off-loop; missing poll throttles added | `A1` `A2` `A4` `A5` `B1` |
| **2** | Logging | Instantiate the missing `QueueListener`; configure once at startup; `contextvars` session scoping; lazy formatting; structured stdout | `C1`–`C9` |
| **3** | Disk & factories | Per-session output dirs; append-only artifact logs; `save_captures` honoured everywhere; per-subclass factory registries | `E1`–`E5` `E8` `D3` |
| **4** | Session store | `SessionRecord`; store and lease protocols with in-memory adapters; TTL, reaper, cap, `lifespan` | `G3` `G4` `G5` `H4` |
| **5** | Re-attach | `DriverInterface` capability; Appium implementation; rehydration on cache miss; `newCommandTimeout`; idempotency keys | — |
| **6** | Redis adapters | `RedisSessionStore`, `RedisLease`, `RedisEventTransport`; optional extra; k8s config; `--workers` re-enabled | goal |

---

## 6. Phase 0 — what shipped

**21 commits**, branch `feat/parallel-sessions-stateless-serve`, range `2a233a0..bbdb979`. Eight tasks, each test-driven and independently reviewed, then one fix wave answering the whole-branch review.

Suite at HEAD: `8 failed, 634 passed, 1 skipped, 2 xfailed, 3 errors`. All 8 failures are in `tests/units/test_optics.py`; all 3 collection errors are in files this branch never touched — zero overlap with the diff, verified by filename and by `git stash` at four points.

### 6.1 Code commits

| Commit | Change |
|---|---|
| `e49a1a2` | Always evict a session on delete, even when driver teardown fails — this endpoint is the only eviction path |
| `b61f78e` | Make `terminate_session` cleanup unconditional, so a failing driver no longer skips the temp dir, JUnit and event registry |
| `e65d7b2` | Reclaim the device when session auto-launch fails; stop flattening `HTTPException` into 500 |
| `2204a74` | Preserve the original launch error when cleanup also fails |
| `718dc45` | Scope request template overrides to the request via `ContextVar` |
| `8dcf1b7` | Bound the template-override isolation test with a timeout |
| `f95262a` | Serialize workspace-stream capture against keyword execution |
| `b69324b` | Guard the api-data swap; end the event stream with its session |
| `dd0e850` | Own `EventManager` lifecycle per session, not per request |
| `d7101bb` | Reject `--workers>1` instead of returning sporadic 404s |
| `62fbee3` | Stop logging raw capabilities; disallow credentialed wildcard CORS |
| `dd54c1e` | Serialize session teardown; 404 on unknown session |
| `5ea1b9e` | Cancel the dispatch task on the loop that owns it |
| `1682f67` | Redact credential-bearing capability values in driver logs |
| `4773a80` | Harden request-scoped cleanup and event-manager bookkeeping |

Plus six documentation commits: the audit, the design spec, the plan, the nine-page concurrency section, the known-issues backlog and the execution record.

### 6.2 What the whole-branch review caught

All eight per-task reviews passed. The whole-branch review then found **one Critical and five Important** — because every one lives *between* two diffs.

**`C1` (Critical) — a lock was removed while another was being added.**
Task 1 deleted the `close_and_terminate_app` pre-call from `delete_session` as redundant. It *was* redundant for teardown — but it was also **the only thing acquiring `session.keyword_lock` before the driver quit**. So the phase added lock coverage to the workspace stream while silently removing it from teardown: a `DELETE` could `driver.quit()` mid-command, and `rmtree` the template directory while a matcher read from it.

*Why the pre-flight scan missed it:* the scan checked whether any task **added** a nested acquisition, and correctly found none. It never considered a task **removing** an existing one — invisible in the diff that removes it and in the diff that depends on it. **Rule adopted:** enumerate lock call sites before and after, and diff the sets.

**`I1` (Important) — two correct fixes combined into a threading violation.**
Task 1 moved teardown onto `asyncio.to_thread`; Task 6 changed `remove_session` from `stop()` to `shutdown()`. Together, `Task.cancel()` ran in a worker thread against a loop-owned task. Silent, because `_check_thread()` only raises under `loop.set_debug(True)`.

**`I3` (Important) — the claim outran the code.**
A task titled "stop logging raw capabilities" fixed one line, but serve forces `DEBUG` and the Appium driver logs `final_caps` at `DEBUG`, so `browserstack.key` still reached stdout. *Why the task review missed it:* the test patched `logger.info`; the leak was at `DEBUG`. **Rule adopted:** when the requirement is "X never appears in logs", attach a real handler at the lowest level and assert on emitted output.

**`I4` (Important) — the branch's own docs contradicted its own code.**
A page written during this phase was marked "Status: Current — the page to trust before deploying anything", and the same branch then fixed five of the things it described as broken. **Rule adopted:** a "Current" marker is a claim that must be re-verified at the end of any branch touching the described behaviour.

**`I5` (Important) — a silent API contract change.** Deleting an unknown session fell through to `200 TERMINATED`. Now a proper `404` — which also changes the MCP `terminate_session` tool from always-succeeds to raises.

### 6.3 The race left open, on purpose

**`I2` — `asyncio.to_thread` is not cancellable.** When a request is cancelled — most commonly an SSE client closing a tab — `CancelledError` is raised at the `await`, and `async with session.keyword_lock` releases the lock **while the driver command is still running in the abandoned thread**. A pending request then acquires the lock and issues a second command against the same remote session.

This cannot be fixed by adding a lock; it is a property of the offload primitive. Phase 1's per-session `ThreadPoolExecutor(max_workers=1)` closes it **structurally**.

**It was documented rather than half-fixed**, because a partial fix would be load-bearing on an executor design that does not exist yet. If anyone points real load at `optics serve` before Phase 1, this is the risk they take on.

### 6.4 Decisions taken during execution

Fourteen, each recorded with what it costs if wrong. Four of them (`R3`, `R4`, `R7`, `R13`) were **defects in the plan itself**, caught by implementers transcribing the brief rather than by the plan's own self-review — which checked for placeholders and contradictions but never verified that named symbols and paths actually exist.

**Rule adopted:** a plan's self-review must include a mechanical existence check — grep every file path, function name and class name the plan asserts.

The two substantive rulings:

- **`R9`** — expanded Task 1's scope into `session_manager.py`, because finding `G1` was not closed without hardening `terminate_session` itself. *Cost if wrong:* a larger diff for one task. The alternative left the branch claiming a fix it had not made.
- **`R14`** — one fix wave for the final review, with `I2` documented rather than fixed. *Cost if wrong:* ships a known, documented hole.

Full list in the [execution record](../superpowers/records/2026-08-19-phase0-execution-record.md).

### 6.5 Process notes worth reusing

- **Batching worked.** Small same-shape tasks dispatched and reviewed as one unit halved the cycle count with no loss of scrutiny; the first zero-finding review in the phase was a batch.
- **Implementers caught plan defects reliably** — three of four, by transcribing the brief. Instructing them to report rather than guess was load-bearing.
- **The final review earned its cost.** Eight clean per-task reviews, then a Critical. Interaction findings are structurally invisible to a per-task view.
- **`pre-commit run` accepts one hook id per invocation.** Passing several fails with `unrecognized arguments` and then runs *nothing* — a silent verification skip.

---

## 7. Known issues carried forward

Full detail with costs and fixes in [Known Issues](known-issues.md).

| Item | Cost today | Closes |
|---|---|---|
| Keyword path blocks the event loop | The reason concurrency does not work | Phase 1 |
| Cancellation releases the lock early (`I2`) | Real interleaving window, triggered by closing a tab | Phase 1 |
| Events dropped in pytest-runner mode (`B1`) | No JUnit XML, no tree output, no error | Phase 1 |
| No end-to-end test for CLI-batch JUnit | None observed — but a future regression would be invisible | Highest-value test gap |
| `httpx` undeclared in test deps | A fresh `poetry install` cannot collect the serve tests | One line |
| No `pytest-timeout` | A deadlocked async test hangs CI instead of failing | One config |
| `JUnitHandlerRegistry` never unsubscribes | Microseconds-wide event-loss window | Low |
| No auth (`I1`) | Anyone reaching the port can drive any session | Out of scope; revisit before Phase 6 |
| No port/device allocation (`F1` `F2`) | Immediate for parallel sessions against a local Appium server | Out of scope; delegated to a Grid |

---

## 8. Risks and open questions

| Risk | Why it matters | How to find out |
|---|---|---|
| **`newCommandTimeout` across pod restarts** | If Appium reaps the remote session during a restart, rehydration produces a valid-looking handle to a dead session. The single biggest unknown in Phases 5–6 | Measurable early against a real device tier — **worth measuring before building on it** |
| **GIL contention from the vision tier** | Driver calls release the GIL; OpenCV and OCR do not. If vision fires on most keywords, per-process throughput degrades in a way threads cannot fix | Measure with `execution_tracer` output before committing to a session-count target. Fix is a separate process pool for vision only |
| **Pub/sub has no replay** | A client reconnecting after a pod death loses backlog | Stated as the contract; Redis Streams if unacceptable |
| **Rehydration latency** | First touch by a new pod pays an attach handshake plus a `StrategyManager` rebuild | The rebuild is already a per-request cost (`A8`); caching the registry per session fixes both |
| **Auth deferred while ids become portable** | Statelessness turns `session_id` into a cross-node bearer capability | Decide before Phase 6 ships, not after |

### What Phase 1 must carry forward

1. **The `contextvars` trap.** `asyncio.to_thread` copies context; `loop.run_in_executor` does not. Phase 0's template-override fix depends on that copy. The executor swap must dispatch through `contextvars.copy_context().run(...)`, with a test asserting an override still resolves.
2. **Release the lease on work-item completion, not coroutine cancellation** — otherwise `I2` survives the executor meant to close it.
3. **Teardown must be a lease operation.** `delete_session` has to participate in whatever serialization Phase 1 introduces, or `C1` reappears — and in Phase 6's distributed form it would be far harder to see.
4. **`EventManager` needs explicit loop affinity.** As more lifecycle work moves off the loop, every `asyncio` primitive on `Session` needs a known owning loop, not lazy binding.
5. **`session.optics.build(...)` is only safe because it never awaits.** Making engine instantiation async would let two concurrent first-keywords double-instantiate a driver.
