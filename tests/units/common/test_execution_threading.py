"""Concurrency contracts for the ``optics serve`` keyword executor."""
import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

from optics_framework.common.execution import KeywordExecutor

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
        assert await asyncio.gather(first, second) == ["first", "second"]
        assert all(thread_id != main_thread_id for thread_id in keyword.thread_ids)

    asyncio.run(run())
