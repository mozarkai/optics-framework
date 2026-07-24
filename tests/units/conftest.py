"""Shared fixtures for the unit suite.

Kept deliberately small: only the collaborators that more than one test file needs
live here. Test-file-specific fakes stay in their own module.
"""
import json
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.models import ElementData
from optics_framework.common.runner.test_runnner import Runner


@pytest.fixture
def mock_driver():
    """A DriverInterface-spec'd mock for verifying keyword→driver delegation."""
    mock = MagicMock(spec=DriverInterface)
    mock.get_app_version.return_value = ""
    mock.get_text_element.return_value = ""
    return mock


@pytest.fixture
def mock_runner():
    """A Runner-spec'd mock carrying real ElementData and a temp output path."""
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
        self.text = text if text is not None else (str(json_data) if json_data is not None else "")
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
    """Factory building a fake ``requests`` response.

    Usage: ``monkeypatch.setattr('requests.request', lambda *a, **k: fake_response(json_data=...))``.
    """
    return _FakeResponse
