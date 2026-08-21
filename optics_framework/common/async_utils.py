import asyncio
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Coroutine
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.error import OpticsError, Code

_persistent_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _start_loop(loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _get_or_create_persistent_loop() -> asyncio.AbstractEventLoop:
    global _persistent_loop, _loop_thread

    with _loop_lock:
        if _persistent_loop is None or _persistent_loop.is_closed():
            internal_logger.info("[AsyncUtils] Creating persistent event loop")

            _persistent_loop = asyncio.new_event_loop()
            _loop_thread = threading.Thread(
                target=_start_loop,
                args=(_persistent_loop,),
                daemon=True,
                name="optics-async-loop"
            )
            _loop_thread.start()

    return _persistent_loop


def run_async(coro: Coroutine[Any, Any, Any]):
    """
    Safely run async coroutine from sync code.
    - Works with pytest / nested loops
    - Avoids deadlocks
    - Stable for Playwright
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass

    # 🔹 Always use persistent background loop to avoid deadlocks when called from async context
    # When called from FastAPI/async context, using the running loop causes deadlocks because
    # we're blocking synchronously while waiting for a coroutine scheduled on the same loop
    loop = _get_or_create_persistent_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=15)
    except (TimeoutError, FutureTimeoutError) as e:
        # Cancel the coroutine if it's still running to prevent it from continuing
        if not future.done():
            future.cancel()
        raise OpticsError(Code.E0102, f"Async operation timed out after 15 seconds: {str(e) or 'Operation exceeded timeout limit'}", cause=e)
    except Exception:
        # Cancel the coroutine if it's still running to prevent it from continuing
        if not future.done():
            future.cancel()
        raise
