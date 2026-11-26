"""Shared fixtures for the unit suite."""
import io
import json
import logging
import sys
from types import SimpleNamespace

import pytest

import optics_framework.api.action_keyword as action_keyword_module
import optics_framework.common.agent_core as agent_core_module
import optics_framework.api.verifier as verifier_module
import optics_framework.common.strategies as strategies_module
from tests.units.fakes import FakeTime


@pytest.fixture
def fake_time(monkeypatch):
    """The keywords' waits on a FakeTime, so a wait for a timeout passes at once."""
    clock = FakeTime()
    for module in (action_keyword_module, agent_core_module, strategies_module, verifier_module):
        monkeypatch.setattr(module, "time", clock)
    monkeypatch.setattr(agent_core_module, "asyncio", SimpleNamespace(sleep=clock.async_sleep))
    return clock


TOOL = """#!/bin/sh
echo "${0##*/} $*" >> "$FAKE_TOOL_LOG"
printf '%s' "$FAKE_TOOL_OUTPUT"
exit "${FAKE_TOOL_STATUS:-0}"
"""


@pytest.fixture(scope="session")
def tool_folder(tmp_path_factory):
    """One script linked under each tool's name: macOS checks a new executable on its first run, about 0.5 s a file."""
    if sys.platform == "win32":
        pytest.skip("the fake tools are shell scripts")
    folder = tmp_path_factory.mktemp("tools")
    (folder / "tool").write_text(TOOL)
    (folder / "tool").chmod(0o755)
    for name in ("adb", "ideviceinstaller", "python"):
        (folder / name).symlink_to(folder / "tool")
    return folder


@pytest.fixture
def tools(request, tmp_path, monkeypatch):
    """Fake device and installer tools on PATH. ``reply(output, status, installed)`` sets what each prints and exits
    with, or leaves them off PATH, and returns a function that lists the command lines they ran."""
    ran = tmp_path / "ran"

    def reply(output="", status=0, installed=True):
        monkeypatch.setenv("PATH", str(request.getfixturevalue("tool_folder") if installed else tmp_path))
        monkeypatch.setenv("FAKE_TOOL_LOG", str(ran))
        monkeypatch.setenv("FAKE_TOOL_OUTPUT", output)
        monkeypatch.setenv("FAKE_TOOL_STATUS", str(status))
        return lambda: ran.read_text().splitlines() if ran.exists() else []
    return reply


class _Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def typed(monkeypatch):
    """``typed(*lines)``: what the user types at the terminal, one line per answer."""
    def type_lines(*lines):
        monkeypatch.setattr(sys, "stdin", _Terminal("".join(f"{line}\n" for line in lines)))
    return type_lines


class _Kept(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append((record.levelname, record.getMessage()))


@pytest.fixture
def internal_log():
    """The level and text of each message logged on optics.internal, which does not propagate to caplog."""
    kept, logger = _Kept(), logging.getLogger("optics.internal")
    logger.addHandler(kept)
    yield kept.messages
    logger.removeHandler(kept)


class _FakeResponse:
    """A minimal stand-in for a ``requests.Response`` used by invoke_api tests."""

    def __init__(self, json_data=None, status_code=200, text=None, content_type="application/json"):
        self._json_data = json_data
        self.status_code = status_code
        self.reason = "OK" if status_code == 200 else "Error"
        self.headers = {"Content-Type": content_type}
        if text is None:
            text = str(json_data) if json_data is not None else ""
        self.text = text
        self.content = self.text.encode()

    def json(self):
        if self._json_data is None:
            raise json.JSONDecodeError("Expecting value", self.text, 0)
        return self._json_data

    @property
    def elapsed(self):
        return SimpleNamespace(total_seconds=lambda: 0.01)


@pytest.fixture
def fake_response():
    return _FakeResponse
