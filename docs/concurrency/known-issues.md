# Known Issues & Follow-Ups

:material-check-circle: **Status: Current** — the consolidated backlog of everything deliberately deferred during the concurrency work, with enough detail to act on without re-deriving it.

Each entry says what is wrong, what it costs today, and what closes it. Finding IDs (`A1`, `E2`, `G1`, …) refer to the [parallel-session audit](../superpowers/specs/2026-08-18-parallel-sessions-audit.md); phase numbers refer to the [roadmap](roadmap.md).

## Blocking real concurrency

### The keyword path blocks the event loop (`A1`)

`TestRunner._try_execute_with_fallback` (`optics_framework/common/runner/test_runnner.py:436`) calls the bound keyword method directly, with no thread offload, from an entirely `async` call chain. The loop is dead for the duration — 200 ms for a tap, up to 30 s for an `Assert Presence`, and the whole of a `Sleep` keyword.

**Cost today:** `optics execute` and `optics serve`'s batch/dry-run mode freeze the process, including `/health` and the event dispatcher. This is the single reason concurrency does not work, and no amount of locking fixes it.

**Closes in:** Phase 1.

### Cancellation releases `keyword_lock` while the driver command is still running

`asyncio.to_thread` is **not cancellable**. When a request is cancelled — most commonly an SSE client disconnecting — `CancelledError` is raised at the `await`, and `async with session.keyword_lock` releases the lock while the worker thread is still executing its WebDriver command. A pending request then acquires the lock and issues a second command against the same remote session.

This is a property of the offload primitive, not of any call site; three of the four `keyword_lock` acquisitions are otherwise correct. It cannot be fixed by adding another lock.

**Cost today:** a real interleaving window in `optics serve`, triggered by closing a browser tab. Symptom is spurious stale-element / `NoSuchElement` failures.

**Closes in:** Phase 1, structurally — a per-session `ThreadPoolExecutor(max_workers=1)` means the next command cannot start until the worker is free, regardless of who holds what lock. **Design note:** release the lease when the *work item* completes, not when the awaiting coroutine is cancelled, or the hole survives the executor.

### Events are silently dropped in `--runner pytest` mode (`B1`)

`queue_event_sync` (`optics_framework/common/runner/test_runnner.py:88`) calls `asyncio.run()` from inside a running loop, which always raises `RuntimeError`, and the handler swallows it as a warning. All eight call sites are affected.

**Cost today:** no JUnit XML and no tree output in that mode, with no error surfaced.

**Closes in:** Phase 1.

## Correctness, not yet urgent

### `JUnitHandlerRegistry.cleanup_session` never unsubscribes `"junit"`

`optics_framework/common/Junit_eventhandler.py` — `cleanup_session` flushes and closes the handler but leaves it subscribed to the session's `EventManager`. This is what makes the double-close possible; it was addressed by making `JUnitEventHandler.close()` idempotent rather than by coupling the two registries.

**Cost today:** effectively none. An event dispatched between `cleanup_junit`'s flush and `remove_session`'s cancel would reach a closed handler and be dropped — a window measured in microseconds, reachable only on the serve path.

**Fix:** unsubscribe in `cleanup_session` as belt-and-braces. Keep the idempotent `close()`.

### No end-to-end test covers CLI-batch JUnit finalization

`tests/units/common/test_session_manager.py` mocks both `cleanup_junit` and `get_event_manager_registry`, so it cannot observe finalization or double-close. Phase 0 moved the flush's trigger, and only a static trace confirms `optics execute` still writes its XML.

**Cost today:** none observed. But this is the highest-value gap to close, because a future regression here would be invisible.

**Fix:** an integration test that runs a small project through `optics execute` with `json_log: true` and asserts the resulting `junit_output_<session>.xml` is well-formed and complete.

### `get_event_manager` can resurrect a dead session

`optics_framework/common/execution.py` — the registry creates an `EventManager` on miss. A request racing a `DELETE` inserts a fresh, never-started manager keyed by a dead session id. Phase 0 moved the lookup after validation, which closes the common path.

**Cost today:** none known after the Phase 0 fix. Worth re-checking once Phase 4 adds a reaper.

## Sharp edges to know about

### `SENSITIVE_KEY_PATTERN` is deliberately over-inclusive

`optics_framework/common/logging_config.py` — capability keys matching a sensitivity pattern have their values redacted in logs. Verified **not** to eat the capabilities operators debug with first: `deviceName`, `udid`, `app`, `systemPort`, `browserstack.user` all survive. It *does* mask `appium:unicodeKeyboard`, `appium:resetKeyboard` (substring "Key"), and `appium:keystorePath`.

The helper does not recurse into **lists** of dicts. Irrelevant at the current call sites; a gap if a future site logs `driver_sources` directly.

**Judged the right trade-off** — a cheap loss at DEBUG against leaking a cloud-farm access key into container stdout.

### `contextvars` propagation depends on the offload primitive

`asyncio.to_thread` copies the current context into the worker thread. **`loop.run_in_executor` does not**, and `run_coroutine_threadsafe` (used by `optics_framework/common/async_utils.py` for every Playwright call) does not either.

Request-scoped template overrides live in a `ContextVar`. They resolve correctly today *only* because keywords run under `to_thread`. Phase 1's executor swap must dispatch through `contextvars.copy_context().run(...)` or overrides silently stop resolving, surfacing as an element-not-found with no error.

**Add a test** asserting an override still resolves through whatever dispatch mechanism Phase 1 lands on.

### `asyncio` primitives are constructed off-loop

`Session.__init__` creates `asyncio.Queue()` and `asyncio.Lock()` inside a `to_thread` worker. This is safe on Python 3.12 **only** because both bind their loop lazily on first use. Do not rewrite them into a form that calls `get_running_loop()` eagerly.

The same discipline now extends to `EventManager._process_task`, which captures its owning loop in `start()` and cancels through `call_soon_threadsafe` when `stop()` is reached from a worker thread.

### `session.optics.build(...)` is only safe because it never awaits

If Phase 1 makes engine instantiation async or moves `build()` off the loop, two concurrent first-keywords on one session could double-instantiate a driver or an OCR reader.

## Environment and tooling

### `httpx` is undeclared

Every `TestClient` test in `tests/units/common/test_expose_api.py` needs `httpx`, but it reaches the venv only through the **optional** `fastmcp` / `google-genai` extras. `poetry install --with dev,test` without extras produces a venv that cannot collect that file.

**Mitigating:** no CI workflow runs `pytest` at all — `.github/workflows/` covers pre-commit, Sonar, CodeQL, docs, and publish.

**Fix:** add `httpx` to the test dependency group. One line.

### No `pytest-timeout`

An async test that regresses into a deadlock **hangs** rather than failing, burning the whole CI job. Every async test added during Phase 0 is individually bounded with `asyncio.wait_for(..., timeout=5)` as a workaround.

**Fix:** add `pytest-timeout` with a global default, so the discipline is enforced rather than remembered.

### `gitleaks` and `commitizen` pre-commit hooks cannot install

`SSL: CERTIFICATE_VERIFY_FAILED` fetching hook environments from GitHub, reproduced both inside and outside the sandbox — a local certificate condition, not a repo problem. Also note `pre-commit run` accepts **one hook id per invocation**; passing several fails with `unrecognized arguments` and silently runs nothing.

**Cost today:** `gitleaks` is not guarding any commit on the affected machine.

### Pre-existing test failures

8 failures in `tests/units/test_optics.py` and 3 collection errors (`test_strategies.py`, `test_appium_find_element.py`, `test_selenium_find_element.py`, from missing `appium`/`selenium` extras). Present before the concurrency work and unrelated to it.

## Out of scope for the current phases

Tracked, not scheduled. See the [roadmap](roadmap.md#explicitly-out-of-scope).

| Item | Why it matters |
|---|---|
| **Authentication** (`I1`) | `session_id` is an unscoped bearer capability with no ownership check. Anyone who can reach the port can drive any session, including `DELETE`. Statelessness makes it *portable* across nodes — more urgent after Phase 6, not less. |
| **`project_path` validation** (`I5`) | Caller-controlled server-side path, used to enumerate images and create directories. |
| **Rate limiting** (`I6`) | No cap on session creation; combined with no TTL, a trivial unauthenticated DoS. |
| **CLI/SDK singletons** (`D1` `D2` `D4`) | `TreeResultPrinter`, `PytestRunner.instance`, and `Optics` at `scope="GLOBAL"` each hold one session's state. Blocks parallel `optics execute` and parallel Robot suites; does not affect `optics serve`. |
| **`optics live` collisions** (`E6` `E7` `A12`) | Second-precision directory and log stamps, a fixed truncating `/tmp` stderr path, a `/save` TOCTOU, and three TUI handlers that block the prompt loop. |
| **Port and device allocation** (`F1` `F2`) | `systemPort` / `wdaLocalPort` / `mjpegServerPort` appear **nowhere** in the tree and are mandatory-unique for parallel Appium sessions. No device lease exists. The served deployment delegates this to a Grid or cloud farm; anyone running parallel sessions against a *local* Appium server hits it immediately. See [Resource Isolation](resource-isolation.md#mitigation-today) for the manual workaround. |
| **Selenium `attach_to_session`** | A ~40-line port of the working Appium trick. Needed if Selenium sessions must survive a pod restart. |
| **SSE backlog replay** | Redis pub/sub is fire-and-forget; a client reconnecting after a pod death loses backlog. Redis Streams would fix it. |
