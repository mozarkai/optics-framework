# The Async Model

:material-progress-clock: **Status: In progress** — the target rule is stated below and is the one to write new code against. The section marked *Where the rule is currently broken* lists the paths that don't follow it yet.

## The architecture in one sentence

**Engines are synchronous; asyncio orchestrates them; there is exactly one place where sync becomes async.**

## Why synchronous engines are the right choice

This is deliberate, not legacy. It is worth understanding before proposing to "make it all async":

- **Driver calls are HTTP round-trips.** Appium and Selenium clients are blocking HTTP clients talking to a remote server. They release the GIL while waiting, so a thread per in-flight call is cheap at device scale — tens of concurrent sessions, not thousands of connections.
- **The vision tier is CPU-bound.** OpenCV template matching and OCR gain *strictly nothing* from `async`. They need threads or processes, and `async` would only obscure that.
- **The engine layer is the public SDK.** `optics_framework.optics.Optics` is a Robot Framework library, and `ActionKeyword` / `Verifier` / `AppManagement` / `FlowControl` are consumed synchronously by `optics execute`, by Robot suites, and by Python users. Making them `async` breaks every one of those consumers for no throughput gain.

So the async boundary belongs *above* the engines, not inside them.

## The rule

!!! success "The one seam"
    Blocking work crosses into async at **`KeywordExecutor.execute`** in `optics_framework/common/execution.py`, and nowhere else.

    ```python
    async with session.keyword_lock:
        result = await asyncio.to_thread(method, *deserialized_params)
    ```

    Sync engines. One offload. A per-session lock, because a WebDriver session is not concurrency-safe.

This pattern already exists at that location and is correct. The work in progress is making it the *only* seam — extracting it as `run_keyword_blocking(...)` and routing every other caller through it, so the invariant becomes greppable: `to_thread` and `run_in_executor` should appear in exactly one module.

### Why a per-session executor, not bare `to_thread`

The target refines the pattern to a per-session `ThreadPoolExecutor(max_workers=1)`:

- `asyncio.to_thread` uses the loop's **default** executor, sized `min(32, cpu+4)` at startup. It is a shared pool: one session's slow keyword starves another's, and sizing it correctly means knowing the session count in advance.
- `max_workers=1` per session makes serialization **structural** rather than dependent on somebody remembering to take the lock. That reliance is exactly how the workspace-stream bug happened.
- Thread affinity comes free, which is the correct home for any engine holding thread-locals.
- The footprint is bounded and obvious: one thread per session, dying with the session.

!!! danger "Context propagation changes with the executor"
    `asyncio.to_thread` propagates the current `contextvars.Context` into the worker thread. **`loop.run_in_executor` does not.** Request-scoped state that lives in a `ContextVar` — template overrides, for one — will silently stop working when the executor swaps in, unless the dispatch goes through `contextvars.copy_context().run(...)`. This is a real trap; it fails silently and looks like an element-not-found.

## Rules for contributors

### Adding a keyword

Write it synchronously. Do not make it `async`. It will be dispatched to a thread for you.

Do **not** call `time.sleep` in a poll loop without asking whether the loop belongs there at all — and if you must poll, throttle it. Two element sources currently poll a remote driver with no sleep whatsoever, which is thousands of requests where a handful would do.

### Adding an endpoint to `optics serve`

Every one of the 15 existing handlers is `async def`, which means **anyio's 40-thread pool is never used** and any blocking call that slips into a handler freezes the entire server rather than occupying one worker.

If your handler touches the driver, the filesystem, or the network, either:

- route it through the keyword seam, or
- wrap the blocking part in `asyncio.to_thread`, **and take `session.keyword_lock`** if it issues driver commands.

Skipping the lock is what makes the workspace stream interleave commands with in-flight keywords.

### Bridging sync → async

If you find yourself writing `asyncio.run(...)` inside code that might already be running under a loop: **stop**. That raises `RuntimeError` unconditionally, and the codebase already contains one instance of this where the error is caught and swallowed, silently dropping every event in `--runner pytest` mode.

The correct bridges are `asyncio.run_coroutine_threadsafe(coro, loop)` from a non-loop thread, and `loop.call_soon_threadsafe(...)` for fire-and-forget. Capture the loop *before* leaving it.

### Locks

- **`asyncio.Lock` is not reentrant.** A nested acquisition deadlocks. Before adding an acquisition, `grep -rn "keyword_lock"` and check no caller in your path already holds it.
- **Never acquire a `threading.Lock` on the event loop thread if a worker thread can hold it across blocking I/O.** The JUnit handler currently does exactly this — its lock is held across a `minidom` pretty-print and a file write, and is acquired from both the loop and a worker. That is a full server stall on a plain mutex.
- **Nothing should hold a lock across an `await`.** The codebase is currently clean on this; keep it that way.

### asyncio primitives

`asyncio.Queue()` and `asyncio.Lock()` are constructed inside a `to_thread` worker in `Session.__init__`. This is safe on Python 3.12 **only** because both bind their loop lazily on first use. Do not "optimize" these constructors into a form that calls `get_running_loop()` eagerly.

Prefer `asyncio.get_running_loop()` over the deprecated `asyncio.get_event_loop()`.

## Where the rule is currently broken

| Path | Problem |
|---|---|
| `runner/test_runnner.py:436` | The batch/dry-run keyword call runs directly on the loop. This is the big one. |
| `expose_api.delete_session` | Driver teardown on the loop — including a retry loop with `time.sleep` around a 10-second HTTP post, worst case ~36 s of frozen loop. |
| `runner/test_runnner.py` (pytest runner) | `pytest.main()` — the entire suite — runs on the loop. |
| `runner/test_runnner.py:88` | `queue_event_sync` calls `asyncio.run` under a running loop and swallows the failure. |
| `expose_api.execute_keyword` | Rebuilds the whole keyword registry per request on the loop, including `inspect.getsource` calls per strategy per element source. |
| `common/async_utils.py` | Every Playwright call is marshalled onto a single **process-global** loop thread with a hard-coded 15-second timeout, so `navigation_timeout_ms` (default 60000) can never be honoured. |
| `helper/live_tui.py` | Three TUI handlers block the prompt_toolkit loop, including one that shells out to `adb` and `idevice_id` with 10-second timeouts each. |

Each is tracked with a finding id in the [audit](../superpowers/specs/2026-08-18-parallel-sessions-audit.md); the fix order is in the [Roadmap](roadmap.md).

## Testing async correctness

The regression that catches this class of bug: **assert `/health` stays responsive while a slow keyword runs**. If the loop is blocked, that request never completes. Generalising this into a standing test is part of Phase 1.
