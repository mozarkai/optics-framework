"""Concurrency contracts for the ``optics serve`` keyword executor."""
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from optics_framework.common.execution import ExecutionEngine, KeywordExecutor

pytestmark = pytest.mark.white_box


class _Session:
    session_id = "sess-1"

    def __init__(self):
        self.keyword_lock = asyncio.Lock()


class _Runner:
    def __init__(self, keyword_map):
        self.keyword_map = keyword_map


class _BlockingKeyword:
    def __init__(self):
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.invocations: list[str] = []
        self.thread_ids: list[int] = []

    def block(self, marker: str):
        self.invocations.append(marker)
        self.thread_ids.append(threading.get_ident())
        if marker == "first":
            self.first_started.set()
            assert self.release_first.wait(timeout=1)
        return marker


def test_keyword_execution_uses_a_thread_and_serializes_per_session():
    async def run():
        main_thread_id = threading.get_ident()
        session = _Session()
        keyword = _BlockingKeyword()
        runner = _Runner({"block": keyword.block})
        first = asyncio.create_task(
            KeywordExecutor("block", ["first"], AsyncMock()).execute(session, runner)
        )

        await asyncio.wait_for(asyncio.to_thread(keyword.first_started.wait), timeout=0.5)
        second = asyncio.create_task(
            KeywordExecutor("block", ["second"], AsyncMock()).execute(session, runner)
        )
        await asyncio.sleep(0.05)

        assert keyword.invocations == ["first"]
        keyword.release_first.set()
        gathered = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
        assert gathered == ["first", "second"]
        assert all(thread_id != main_thread_id for thread_id in keyword.thread_ids)

    # M7a: bounded so a lock regression fails instead of hanging the suite --
    # this repo has no pytest-timeout.
    asyncio.run(asyncio.wait_for(run(), timeout=5))


def test_concurrent_keywords_on_one_session_keep_event_dispatch_alive():
    """B3: one request completing must not cancel the shared dispatch task."""
    from optics_framework.common.events import Event, EventManager, EventStatus

    async def scenario():
        mgr = EventManager()
        mgr.start()
        delivered: list[Event] = []

        class _Sub:
            async def on_event(self, event):
                delivered.append(event)

        mgr.subscribe("probe", _Sub())

        engine = ExecutionEngine(MagicMock())
        # Request A finishes and drains.
        await engine._drain_events(mgr)
        # Request B, still in flight, publishes afterwards.
        late = Event(
            entity_type="keyword", entity_id="B", name="block",
            status=EventStatus.RUNNING,
        )
        await mgr.event_queue.put(late)
        await asyncio.sleep(0.2)
        running = mgr._running
        mgr.shutdown()
        return delivered, running

    delivered, running = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert running is True, "dispatch loop was stopped by a sibling request"
    assert delivered and delivered[-1].entity_id == "B", (
        "event published after a sibling drained was lost"
    )

def test_event_manager_stop_from_worker_thread_cancels_on_owning_loop():
    """I1: Task.cancel() from a worker thread races the loop it belongs to.

    ``terminate_session`` runs under ``asyncio.to_thread`` (delete_session,
    and create_session's launch-failure cleanup), and it reaches
    ``EventManager.stop()`` via ``EventManagerRegistry.remove_session``. The
    cancel must be marshalled back onto the loop that created the task.
    """
    from optics_framework.common.events import EventManager

    class _CancelProbe:
        def __init__(self):
            self.thread_id: int | None = None

        def cancel(self):
            self.thread_id = threading.get_ident()

    async def scenario():
        mgr = EventManager()
        mgr.start()
        real_task = mgr._process_task
        probe = _CancelProbe()
        mgr._process_task = probe  # type: ignore[assignment]

        await asyncio.to_thread(mgr.stop)
        # Give the loop a chance to run the marshalled callback.
        for _ in range(5):
            await asyncio.sleep(0)

        real_task.cancel()
        return threading.get_ident(), probe.thread_id

    loop_thread_id, cancel_thread_id = asyncio.run(
        asyncio.wait_for(scenario(), timeout=5)
    )
    assert cancel_thread_id is not None, "dispatch task was never cancelled"
    assert cancel_thread_id == loop_thread_id, (
        "Task.cancel() ran on a worker thread instead of the owning event loop"
    )


def test_terminate_session_from_worker_thread_stops_event_manager():
    """I1 end-to-end: the off-loop teardown path must still stop dispatch."""
    from optics_framework.common.events import get_event_manager, get_event_manager_registry
    from optics_framework.common.session_manager import SessionManager

    async def scenario():
        manager = SessionManager()
        mgr = get_event_manager("sess-i1")
        mgr.start()
        task = mgr._process_task
        # Mirrors expose_api: terminate_session is offloaded to a worker.
        await asyncio.to_thread(manager.terminate_session, "sess-i1")
        for _ in range(5):
            await asyncio.sleep(0)
        return mgr, task

    mgr, task = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert mgr._running is False
    assert task.cancelled() or task.done(), "dispatch task survived teardown"
    assert "sess-i1" not in get_event_manager_registry().get_active_sessions()


def test_execute_against_missing_session_leaves_no_event_manager():
    """M4: the registry creates on miss, so a request racing a DELETE would
    otherwise register a never-started EventManager under a dead session id
    that nothing ever removes."""
    from optics_framework.common.error import OpticsError
    from optics_framework.common.events import get_event_manager_registry
    from optics_framework.common.execution import ExecutionParams

    manager = MagicMock()
    manager.get_session.return_value = None
    engine = ExecutionEngine(manager)
    params = ExecutionParams(
        session_id="sess-gone", mode="keyword", keyword="noop",
        params=[], runner_type="keyword_runner", use_printer=False,
    )

    async def scenario():
        with pytest.raises(OpticsError):
            await engine.execute(params)

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert "sess-gone" not in get_event_manager_registry().get_active_sessions()
