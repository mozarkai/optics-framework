# Logging & Session Isolation

:material-progress-clock: **Status: In progress** — the *Current behaviour* section is accurate today; the *Target* section is Phase 2 and not yet implemented.

For the logging subsystem's original design and intent, see [Architecture → Logging](../architecture/logging.md). This page covers only what happens when more than one session logs at once, which is where the design breaks down.

## The short version

Optics has **two module-global loggers** — `optics.internal` and `optics.execution` — created once at import in `optics_framework/common/logging_config.py`. There is no per-session logger, no per-session handler, and no filter that separates one session's records from another's. The scaffolding that looks like session scoping (`LoggerContext`, `SessionLoggerAdapter`) only stamps a `session_id` into `extra`; nothing ever reads it.

## Current behaviour

### The queue has no listener

`execution_logger` is given a `QueueHandler` feeding `execution_log_queue = queue.Queue(-1)`. That is the correct async-safe pattern — except **`QueueListener` appears zero times in the entire repository**. Nothing ever drains the queue.

Two consequences:

- Every execution `LogRecord` — with its `args`, `exc_info`, and any captured frames — is retained for the process lifetime. `clear_queues()` only runs at `atexit`. In a long-lived server this is a steady, unbounded memory leak.
- `execution_console_handler` is constructed and formatted, then **never attached to any logger**. Execution logs reach a console only because `execution_logger.propagate` is `True`, routing them to the root logger.

The fix is to instantiate the `QueueListener` the code was clearly written for. That single change also moves all handler I/O off the calling thread, which is what makes the whole path async-safe by construction.

### Every session reconfigures logging globally

`ConfigHandler.__init__` calls `initialize_handlers(config)`, and a `ConfigHandler` is constructed **per session**. `optics serve` additionally calls `reconfigure_logging(session_config)` on every session creation.

`initialize_handlers` sets the level on the two shared loggers and adds a `RotatingFileHandler` for that session's `execution_output_path`, de-duplicated only by `baseFilename` and **never removed**. So:

- Session B starting with `log_level: DEBUG` silently flips session A from `INFO` to `DEBUG` mid-run.
- Once B's file handler is attached, **A's log lines are also written into B's log file**, and vice versa. Every session's log becomes a superset of every concurrent session's.
- Handlers accumulate for the process lifetime. N sessions across N projects means every record is written N times.

### Log capture bleeds between sessions

To populate JUnit `<log>` elements, `TestRunner._execute_keyword` attaches a `LogCaptureBuffer` to the **process-global** `execution_logger` for the duration of each keyword, then removes it.

With two sessions running, session A's per-keyword buffer captures **session B's records**, and they land in A's JUnit output. `emit` is serialized by the handler lock, but `get_records()` and `clear()` are unsynchronized and are read without one.

### The server forces DEBUG on everything

`optics serve` replaces the `uvicorn.*` and **root** logger handler lists wholesale and forces root to `DEBUG`; `expose_api` additionally hard-codes `log_level="DEBUG"` into every session `Config`. The net effect is that every record from `urllib3`, `selenium`, `asyncio`, and `appium`, plus every HTTP access line, is rendered by `RichHandler` — markup parse, syntax highlight, terminal measure, write, flush — **on the event loop thread**.

On the hot path, `EventManager._process_events` and the JUnit handler both do:

```python
internal_logger.debug(f"Processing event: {event.model_dump()}")
```

An f-string is evaluated eagerly, so that full Pydantic serialization runs **before** the level check, for every event and every subscriber, whether or not DEBUG is enabled.

### Secrets reach stdout

`create_session` logs `config.model_dump()` verbatim, which includes driver capabilities. Cloud device-farm capabilities routinely carry access keys — `browserstack.user`, `browserstack.key`, vendor tokens.

`SensitiveDataFormatter` exists and does redact, but it is attached **only to file handlers**, not to the console handler. In a container, stdout is exactly where logs are shipped. Fixed in Phase 0.

### One session's shutdown can kill logging process-wide

`disable_logger()` sets `logging.getLogger().disabled = True` on the **root** logger. It is currently only reached from `atexit`, so it doesn't bite in practice — but it is a process-global kill switch sitting in a per-session teardown path.

## Target

:material-alert: **Status: Planned** (Phase 2).

- **Configure logging exactly once, at process startup.** `ConfigHandler.__init__` stops calling `initialize_handlers`; `create_session` stops calling `reconfigure_logging`. Log level comes from process configuration, not from whichever session happened to be created last.
- **Instantiate the `QueueListener`**, so the queue is drained and handler I/O leaves the calling thread.
- **Session scoping via `contextvars`.** A `ContextVar` carrying `session_id`, a filter that stamps it onto every record, and — where per-session files are wanted — a filter that routes. This replaces add/remove of handlers on a global logger, which also removes the `LogCaptureBuffer` cross-talk without needing a lock.
- **Structured JSON to stdout in server mode**, at a configured level, so a container's log pipeline can filter by `session_id`.
- **Lazy formatting on the event path** — `%`-style args, or an `isEnabledFor` guard.
- **`SensitiveDataFormatter` on the console handler too.**

## What this means for you today

- **Do not trust a log file to contain one session's records** if more than one session ran in the process.
- **Do not run `optics serve` with untrusted capabilities in a shared log environment** until the redaction fix lands — assume anything in `capabilities` reaches stdout.
- **Prefer `%`-style logging args** (`logger.debug("x: %s", value)`) over f-strings in any code that runs per event or per keyword. The formatting cost is real and it is paid whether or not the level is enabled.
