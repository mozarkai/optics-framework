import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Coroutine, Optional

from optics_framework.common.logging_config import internal_logger
from optics_framework.common.error import OpticsError, Code


"""
================================================================================
Async Utilities Module
================================================================================

Design intent:
- Provide a safe, deterministic way to execute async coroutines from
  synchronous code paths.
- Support environments where an event loop may or may not already exist
  (pytest, Playwright, FastAPI, CLI tools).
- Avoid deadlocks caused by blocking calls on the currently running loop.

Key architectural decisions:
- A SINGLE persistent background event loop is created and reused.
- All async coroutines are executed on that loop via
  `asyncio.run_coroutine_threadsafe`.
- This avoids nested-loop errors and Playwright deadlocks.

Important constraints:
- This module is intentionally conservative and defensive.
- Stability and predictability are prioritized over performance.
================================================================================
"""

# ---------------------------------------------------------------------
# Persistent background event loop state
# ---------------------------------------------------------------------
# NOTE:
# - These are module-level globals by design.
# - Access is guarded by `_loop_lock` to ensure thread safety.
# - Optional[...] is used instead of Python 3.10 `| None`
#   for backward compatibility.
# ---------------------------------------------------------------------
_persistent_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_loop_lock = threading.Lock()

# ---------------------------------------------------------------------
# Shared executor
# ---------------------------------------------------------------------
# Design note:
# - A single-thread executor is sufficient because actual async execution
#   happens inside the event loop.
# - This avoids thread churn and resource exhaustion.
# ---------------------------------------------------------------------
_executor = ThreadPoolExecutor(max_workers=1)


def _start_loop(loop: asyncio.AbstractEventLoop):
    """
    Entry point for the background thread.

    Responsibilities:
    - Bind the provided event loop to the current thread.
    - Run the loop indefinitely.

    This function never returns unless the loop is explicitly stopped.
    """
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _get_or_create_persistent_loop() -> asyncio.AbstractEventLoop:
    """
    Retrieve the shared persistent event loop, creating it if necessary.

    Design considerations:
    - Ensures exactly ONE background event loop exists.
    - Safe for concurrent access via `_loop_lock`.
    - Automatically recreates the loop if it was closed.

    Returns:
        asyncio.AbstractEventLoop:
            A running, reusable event loop suitable for scheduling coroutines.
    """
    global _persistent_loop, _loop_thread

    with _loop_lock:
        if _persistent_loop is None or _persistent_loop.is_closed():
            internal_logger.info("[AsyncUtils] Creating persistent event loop")

            _persistent_loop = asyncio.new_event_loop()
            _loop_thread = threading.Thread(
                target=_start_loop,
                args=(_persistent_loop,),
                daemon=True,
                name="optics-async-loop",
            )
            _loop_thread.start()

    return _persistent_loop


def run_async(coro: Coroutine[Any, Any, Any]):
    """
    Execute an async coroutine safely from synchronous code.

    Why this exists:
    - `asyncio.run()` cannot be used when an event loop is already running.
    - Directly awaiting coroutines is impossible from sync code.
    - Playwright + FastAPI frequently run inside existing loops.

    How it works:
    1. Detect whether a loop is already running (for awareness only).
    2. Always schedule the coroutine on a dedicated background loop.
    3. Block synchronously until the result is available.

    Error handling:
    - Timeouts are converted into OpticsError with a clear message.
    - Pending coroutines are cancelled to prevent runaway execution.

    Args:
        coro (Coroutine):
            The async coroutine to execute.

    Returns:
        Any:
            The result returned by the coroutine.

    Raises:
        OpticsError:
            If execution times out or fails unexpectedly.
    """

    # -----------------------------------------------------------------
    # Detect existing event loop (informational only)
    # -----------------------------------------------------------------
    # IMPORTANT:
    # - We do NOT use the running loop even if one exists.
    # - Blocking on the same loop would cause a deadlock.
    # - This try/except is intentional and expected.
    # -----------------------------------------------------------------
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running event loop in this thread.
        # This is expected for sync execution contexts.
        pass

    # -----------------------------------------------------------------
    # Always schedule on the persistent background loop
    # -----------------------------------------------------------------
    loop = _get_or_create_persistent_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)

    try:
        # Blocking wait with a generous timeout for browser operations
        return future.result(timeout=120)

    except (TimeoutError, FutureTimeoutError) as e:
        # -----------------------------------------------------------------
        # Timeout handling
        # -----------------------------------------------------------------
        # - Cancel the coroutine if still running
        # - Convert to a domain-specific OpticsError
        # -----------------------------------------------------------------
        if not future.done():
            future.cancel()

        raise OpticsError(
            Code.E0102,
            "Async operation timed out after 120 seconds",
            cause=e,
        )

    except Exception:
        # -----------------------------------------------------------------
        # Defensive cleanup
        # -----------------------------------------------------------------
        # Ensure the coroutine does not continue running in background
        # after an unexpected exception.
        # -----------------------------------------------------------------
        if not future.done():
            future.cancel()
        raise
