"""GeminiLLM request construction, with the google-genai client replaced by a fake."""
from types import SimpleNamespace

import pytest

from optics_framework.engines.llm_models import gemini

pytest.importorskip("google.genai", reason="gemini engine tests need the llm extra")


class _FakeModels:
    def __init__(self):
        self.configs = []

    def generate_content(self, *, model, contents, config):
        self.configs.append(config)
        return SimpleNamespace(text="{}", usage_metadata=None)


@pytest.fixture
def fake_client(monkeypatch):
    models = _FakeModels()
    monkeypatch.setattr(gemini.genai, "Client", lambda **_kw: SimpleNamespace(models=models))
    return models


def test_capabilities_temperature_is_sent(fake_client):
    gemini.GeminiLLM({"capabilities": {"temperature": 0.0}}).generate("hi")
    assert fake_client.configs[-1].temperature == 0.0
