# Concurrency & Sessions

How Optics handles more than one thing at a time — what is isolated per session, what is shared across the whole process, where synchronous code meets the event loop, and what it would take to run `optics serve` as a stateless, horizontally-scalable service.

!!! warning "Read the status marker on every page"
    Much of this section documents a **target design that is not yet built**. Every page opens with one of three markers. Do not deploy against a page marked *Planned*.

    - :material-check-circle: **Current** — describes the code as it exists today.
    - :material-progress-clock: **In progress** — partially landed; the page says which parts.
    - :material-alert: **Planned** — designed and agreed, not implemented.

## Start here

| If you want to… | Read |
|---|---|
| **The whole story in one place** — problem, assumptions, decisions, design, phases | [Program Reference](program-reference.md) |
| Know whether you can run N sessions at once **right now** | [Parallel Session Limits](parallel-session-limits.md) — the honest status table, per surface |
| Understand what a `Session` owns and what it doesn't | [Session Lifecycle](session-lifecycle.md) |
| Add a keyword, driver, or endpoint without blocking the event loop | [The Async Model](async-model.md) — contributor rules |
| Understand why logs from two sessions interleave | [Logging & Session Isolation](logging-and-isolation.md) |
| Know why two sessions fight over files, ports, and devices | [Resource Isolation](resource-isolation.md) |
| Deploy `optics serve` on Kubernetes | [Stateless Serve](stateless-serve.md) |
| See what's shipping and when | [Roadmap](roadmap.md) |
| Find a known bug, gap, or sharp edge before hitting it | [Known Issues & Follow-Ups](known-issues.md) |

## The one-paragraph summary

Optics is a **synchronous codebase with an asyncio veneer**. Every keyword, every driver call, and every vision model is blocking; asyncio exists to orchestrate them and to serve HTTP. That is a legitimate and deliberate architecture — driver calls are HTTP round-trips that release the GIL, so threads are the right tool and making the engine layer `async` would buy nothing while breaking the public SDK. The problems arise where the boundary between the two worlds is drawn inconsistently: in some paths blocking work is correctly pushed to a thread, and in others it runs directly on the event loop, freezing every other session in the process.

## The four independent reasons parallel sessions don't work today

Any one of these alone is sufficient to break concurrency. They are being fixed in order; see the [Roadmap](roadmap.md).

1. **The keyword path blocks the event loop.** `TestRunner._try_execute_with_fallback` calls the keyword method directly, with no thread offload. While it runs — up to 30 seconds for an `Assert Presence` — nothing else in the process makes progress, including the event dispatcher and `/health`. See [The Async Model](async-model.md).

2. **Process-global singletons hold per-session state.** The result printer, the pytest runner, the engine factory registry, the logging manager, and the `Optics` SDK facade each hold state that belongs to one session. A second session overwrites the first's. See [Session Lifecycle](session-lifecycle.md).

3. **Artifacts are namespaced per *project*, not per *session*.** Two sessions on one project share an output directory, and two of the files in it are non-atomic read-modify-writes that corrupt under concurrency — silently. See [Resource Isolation](resource-isolation.md).

4. **Nothing allocates exclusive external resources.** Parallel Appium sessions require unique `systemPort` / `wdaLocalPort` values; these appear nowhere in the codebase. There is no device lease of any kind. See [Resource Isolation](resource-isolation.md).

## What already works

It is worth being precise about this, because the list is longer than it looks and it defines what *not* to rewrite:

- **The correct async pattern already exists**, in exactly one place — `KeywordExecutor.execute` holds a per-session lock and offloads to a thread. It is the template everything else should follow. (`asyncio.to_thread` is not cancellable, so the lock is released early when a request is cancelled; Phase 1's per-session executor makes the serialization structural.)
- **Session-scoped registries with proper locking**: the event-manager registry and the JUnit handler registry are both keyed by session id and guarded by a `threading.Lock`. JUnit output is already written per session.
- **Per-session temp directories** are correctly unique and correctly cleaned up.
- **`OpticsBuilder`, `StrategyManager`, `KeywordRegistry`, `EventSDK`, and `InstanceFallback`** are all genuinely per-session with no shared state.
- **A working driver re-attach path for Appium** already exists in the codebase — it is the foundation the stateless design is built on. See [Stateless Serve](stateless-serve.md).
- **No `os.chdir`, no `os.environ` mutation, no `sys.path` mutation, no `lru_cache` singletons** anywhere in the runtime.

## Design records

The full working documents behind this section. These are point-in-time records, not living documentation — where they disagree with the pages above, the pages above win.

- [Parallel-session audit](../superpowers/specs/2026-08-18-parallel-sessions-audit.md) — 62 findings with stable IDs (`A1`, `E2`, `G1`, …), each with a file:line anchor, a concrete two-session failure scenario, and a severity. The pages in this section cite these IDs.
- [Stateless-serve design](../superpowers/specs/2026-08-18-parallel-sessions-stateless-serve-design.md) — the agreed architecture, including an honest table of what can and cannot be made stateless per driver backend.
- [Phase 0 implementation plan](../superpowers/plans/2026-08-18-phase0-session-leaks-and-races.md) — the first tranche of work.
- [Phase 0 execution record](../superpowers/records/2026-08-19-phase0-execution-record.md) — what actually happened: commits, decisions taken during execution, defects found in the plan itself, and what the reviews caught.

## Related architecture pages

- [Execution](../architecture/execution.md) — the execution engine and runners
- [Event System](../architecture/event_system.md) — events, subscribers, JUnit
- [Logging](../architecture/logging.md) — the logging subsystem as originally designed
- [REST API Layer](../architecture/api_layer.md) — `optics serve` endpoints
