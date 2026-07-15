#!/usr/bin/env python3
"""
Tests for the WorkerLauncher implementations.

SubprocessLauncher tests spawn real uvicorn processes running the stub worker
app (no Appium needed) on ephemeral ports; each takes a couple of seconds.

Run with: python -m pytest test_launcher.py -v
"""

import asyncio
import os
from pathlib import Path

import pytest

from launcher import SubprocessLauncher, WorkerHandle, create_launcher

HERE = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def stub_worker_importable(monkeypatch):
    """Ensure spawned uvicorn workers can import the stub app."""
    monkeypatch.setenv("PYTHONPATH", str(HERE) + os.pathsep + os.environ.get("PYTHONPATH", ""))


@pytest.fixture
def launcher():
    instance = SubprocessLauncher(worker_app="stub_worker:app")
    yield instance
    # Belt-and-braces: kill anything a failing test left behind.
    for handle_id in instance.owned_handle_ids():
        proc, log_fh = instance._procs[handle_id]
        proc.kill()
        proc.wait()
        if log_fh:
            log_fh.close()


def run(coro):
    return asyncio.run(coro)


class TestSubprocessLauncher:
    def test_launch_wait_ready_stop(self, launcher):
        async def scenario():
            handle = await launcher.launch()
            assert handle.endpoint.startswith("http://127.0.0.1:")
            assert await launcher.wait_ready(handle, timeout_s=30)
            assert await launcher.is_alive(handle)

            await launcher.stop(handle)
            assert not await launcher.is_alive(handle)

        run(scenario())

    def test_ephemeral_ports_do_not_collide(self, launcher):
        async def scenario():
            first = await launcher.launch()
            second = await launcher.launch()
            try:
                assert first.endpoint != second.endpoint
                assert first.id != second.id
                assert await launcher.wait_ready(first, timeout_s=30)
                assert await launcher.wait_ready(second, timeout_s=30)
            finally:
                await launcher.stop(first)
                await launcher.stop(second)

        run(scenario())

    def test_wait_ready_times_out_for_dead_worker(self):
        broken = SubprocessLauncher(worker_app="no_such_module:app")

        async def scenario():
            handle = await broken.launch()
            try:
                # The process exits immediately (bad import), which wait_ready
                # detects without burning the whole timeout.
                assert not await broken.wait_ready(handle, timeout_s=10)
            finally:
                await broken.stop(handle)

        run(scenario())

    def test_stop_unknown_handle_is_noop(self, launcher):
        ghost = WorkerHandle(id="not-ours", endpoint="http://127.0.0.1:1")
        run(launcher.stop(ghost))  # must not raise
        assert not run(launcher.is_alive(ghost))


class TestCreateLauncher:
    def test_default_is_subprocess(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_LAUNCHER", raising=False)
        assert isinstance(create_launcher(), SubprocessLauncher)

    def test_explicit_subprocess(self):
        assert isinstance(create_launcher("subprocess"), SubprocessLauncher)

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="Unknown SUPERVISOR_LAUNCHER"):
            create_launcher("teleport")


if __name__ == "__main__":
    pytest.main([__file__])
