"""Shared fixtures for the unit suite."""
import json
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import optics_framework.api.action_keyword as action_keyword_module
import optics_framework.common.agent_core as agent_core_module
import optics_framework.api.verifier as verifier_module
import optics_framework.common.strategies as strategies_module
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.models import ElementData
from optics_framework.common.runner.test_runnner import Runner
from tests.units.fakes import FakeTime


@pytest.fixture
def fake_time(monkeypatch):
    """The keywords' waits on a FakeTime, so a wait for a timeout passes at once."""
    clock = FakeTime()
    for module in (action_keyword_module, agent_core_module, strategies_module, verifier_module):
        monkeypatch.setattr(module, "time", clock)
    monkeypatch.setattr(agent_core_module, "asyncio", SimpleNamespace(sleep=clock.async_sleep))
    return clock


@pytest.fixture
def mock_driver():
    mock = MagicMock(spec=DriverInterface)
    mock.get_app_version.return_value = ""
    mock.get_text_element.return_value = ""
    return mock


@pytest.fixture
def mock_runner():
    runner = MagicMock(spec=Runner)
    runner.elements = ElementData()
    runner.config_handler = MagicMock()
    temp_dir = tempfile.mkdtemp()
    runner.config_handler.config = SimpleNamespace(
        execution_output_path=temp_dir, project_path=temp_dir
    )
    return runner


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
