# Parallel Session Limits

:material-check-circle: **Status: Current** — this page describes the code as it exists today, and is the page to trust before deploying anything.

## Can I run N sessions at once?

| Surface | Verdict | Why |
|---|---|---|
| **`optics serve`** | :material-alert: **One at a time, in practice** | Sessions are created, looked up, and torn down correctly, and the cross-request corruption listed below is fixed — but a single keyword still blocks the event loop for its whole duration (Phase 1). See below. |
| **`optics mcp`** | :material-alert: **Same as `serve`** | A thin in-process wrapper over the same code; it inherits every limitation and adds none. |
| **`optics serve --workers N`** | :material-close-circle: **Rejected at startup** | Each worker process gets its own in-memory session registry, so a session created on one worker returned `404` from the others. `--workers > 1` now fails immediately with `E0501` rather than starting a server that loses sessions. |
| **`optics execute`** | :material-close-circle: **One per process** | The result printer is a process-wide singleton whose state is replaced per session; a second run silently freezes the first one's live tree. |
| **`optics execute --runner pytest`** | :material-close-circle: **One per process, and events are lost** | The runner is a class-level singleton wired into a generated `conftest.py`. Separately, **100% of events are silently dropped in this mode** — see below. |
| **`optics live`** | :material-alert: **One per host, practically** | Session directories and log files are stamped to second precision with no session id, so two sessions started in the same second share both. |
| **Python SDK / Robot Framework** | :material-close-circle: **One per process** | `Optics` is a `GLOBAL`-scope library with a single session slot; a second `Setup` orphans the first session's driver and temp directory without terminating it. |

## The failure modes, concretely

### A slow keyword freezes everything

`TestRunner._try_execute_with_fallback` (`optics_framework/common/runner/test_runnner.py:436`) calls the bound keyword method directly:

```python
method(*resolved_positional_params, **resolved_kw_params)
```

No `asyncio.to_thread`, no executor. The call chain reaching it is entirely `async`, so this runs **on the event loop thread**. For the duration — 200 ms for a tap, up to 30 s for an `Assert Presence`, and the whole of a `Sleep` keyword, which is a bare `time.sleep` — nothing else in the process runs. Not other sessions, not the event dispatcher (so the live tree and JUnit XML only advance *between* keywords), and under `optics serve` not `/health` either.

Underneath it, two element-source poll loops spin with no throttle at all (`appium_page_source.py:211`, `appium_find_element.py:203`), turning a 30-second assertion into thousands of Appium round-trips. The throttle exists in the source as a commented-out line.

!!! note "Partially mitigated"
    A previous fix wrapped `KeywordExecutor.execute` — the path used by `optics serve` in keyword mode — in `asyncio.to_thread` under a per-session lock. That path does not block the loop (though see the cancellation hole below). The batch and dry-run paths are not offloaded at all, and `optics serve` reaches the batch path in `mode="batch"`.

### Two concurrent requests on one session — fixed, with one hole left

:material-check-circle: Three mechanisms in `optics_framework/common/expose_api.py` used to let two requests on one session corrupt each other. All three are closed:

- **Template overrides** moved off the shared `Session` onto a `contextvars.ContextVar`, which is per-task and therefore per-request. Request A's cleanup can no longer wipe request B's templates.
- **The workspace SSE stream** now takes `session.keyword_lock` before issuing its screenshot / element-collection / page-source commands, so it no longer interleaves WebDriver commands with an in-flight keyword.
- **The per-session `EventManager`** is started once per session and shut down in `EventManagerRegistry.remove_session`, not per request. A finishing request drains the queue but no longer cancels the dispatch task a sibling is still using.

`delete_session` also takes `keyword_lock` before tearing the driver down, bounded by a 60-second acquisition timeout so a wedged keyword cannot make a session un-evictable.

!!! danger "Remaining hole: cancellation does not stop the worker thread"
    `asyncio.to_thread` is **not cancellable**. When an SSE client disconnects — or any request is cancelled — `CancelledError` is raised at the `await`, and `async with session.keyword_lock` releases the lock **while the driver command is still running in the abandoned thread**. A pending request then acquires the lock and issues a second command against the same remote session.

    Three of the four `keyword_lock` call sites are otherwise correct; this is a property of the offload primitive, not of any one call site. It is closed in Phase 1 by the per-session `ThreadPoolExecutor(max_workers=1)`, where serialization is structural: the second command cannot start until the thread is free, regardless of who holds what lock.

### Sessions no longer leak, but nothing reclaims an idle one

:material-check-circle: Both guaranteed leaks are closed:

- `delete_session` evicts unconditionally. A driver that refuses to quit still surfaces its failure to the caller, but the session, its temp directory, its JUnit handler, and its `EventManager` are cleaned up regardless. Deleting an unknown or already-deleted session returns `404` rather than falsely reporting success.
- `create_session` reclaims the device when the automatic app launch fails, and no longer flattens the launch error into a generic `500`.

:material-alert: What remains is everything that would reclaim a session **nobody asked to delete**: there is still **no TTL, no reaper, no heartbeat, no session cap, and no shutdown handler**. A client that opens a session and disconnects holds a device indefinitely, and on `SIGTERM` every live driver session is orphaned. That is Phase 4.

### Events are silently dropped in pytest-runner mode

`queue_event_sync` (`optics_framework/common/runner/test_runnner.py:88`) calls `asyncio.run()` from inside a running event loop. That always raises `RuntimeError`, and the handler swallows it as a warning:

```python
try:
    asyncio.run(subscriber.on_event(event))
except RuntimeError as e:
    internal_logger.warning(f"Failed to process event synchronously: {e}")
```

All eight call sites are affected. In `--runner pytest` mode this means **no JUnit XML and no tree output**, with no error surfaced to the user.

### Files corrupt silently

`page_sources_log.xml` is rewritten in full on nearly every keyword: read the whole file, strip the trailing `</logs>`, rewrite. Two concurrent writers lose entries, and because the file is opened `r+` without truncation, a shorter rewrite leaves stale bytes past `</logs>`. The next call's `endswith("</logs>")` guard then fails and **page-source logging silently stops for every session**.

`api_details.har` has the same read-modify-write shape, and its `except json.JSONDecodeError` handler discards the entire accumulated history on a partial write.

Compounding this: `optics serve` sets `save_captures=False` specifically so HTTP sessions don't touch disk, but that flag is honoured by `Verifier` only — `ActionKeyword` and both UI helpers write regardless. So the server writes screenshots and rewrites `page_sources_log.xml` into its working directory on every keyword, in exactly the deployment where concurrency is normal.

### Nothing stops two sessions grabbing the same device

Parallel Appium sessions require unique `systemPort` (UiAutomator2, defaults to 8200) and `wdaLocalPort` (WDA, defaults to 8100). Grep the tree for either: **zero occurrences**. Two sessions from the same `config.yaml` will either be rejected by Appium or — worse — race for port 8200, after which commands land on the wrong device.

There is no device lease, no lockfile, no port allocator, and no allocation table anywhere in the codebase.

## Safe patterns today

Until the roadmap work lands, these are the configurations that actually work:

- **One session per process.** Scale by running multiple single-session processes, each with its own `project_path` so output directories don't collide.
- **`optics serve` with one worker and one session at a time.** Sequential requests are fine; the correctness problems are all concurrency problems.
- **Give every concurrent process its own `project_path`.** This is the single highest-value mitigation — it separates the output directory, which is where most of the file corruption lives.
- **Set `systemPort` / `wdaLocalPort` explicitly per session** in your `config.yaml` capabilities if you run parallel Appium sessions against a local Appium server. Optics will pass them through; it just won't generate them for you.
- **Avoid `--runner pytest`** if you need JUnit output.
- **Don't reach for `--workers > 1`** — it is rejected at startup. Run several single-worker instances instead.

## Where this is going

See the [Roadmap](roadmap.md). Phase 0 (landed) closed the leaks and the cross-request corruption, except for the cancellation hole above; Phase 1 fixes both that and the loop blocking, which is the change that actually unlocks concurrency.
