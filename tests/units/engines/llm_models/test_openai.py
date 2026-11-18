"""OpenAILLM request construction, with the openai client replaced by a fake."""
import base64
import json
from types import SimpleNamespace

import pytest

from optics_framework.common.error import OpticsError
from optics_framework.engines.llm_models import openai as openai_engine
from optics_framework.engines.llm_models.openai import strict_schema

pytest.importorskip("openai", reason="openai engine tests need the openai extra")

pytestmark = pytest.mark.white_box

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["keyword", "done"]},
        "params": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action"],
}


class _FakeCompletions:
    def __init__(self):
        self.requests = []
        self.message = SimpleNamespace(content='{"action": "done"}', refusal=None)
        self.finish_reason = "stop"

    def create(self, **request):
        self.requests.append(request)
        choice = SimpleNamespace(message=self.message, finish_reason=self.finish_reason)
        return SimpleNamespace(choices=[choice], usage=None)


@pytest.fixture
def fake_client(monkeypatch):
    completions = _FakeCompletions()
    clients = []

    def _client(**kwargs):
        clients.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    monkeypatch.setattr(openai_engine, "OpenAI", _client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    completions.clients = clients
    return completions


def _llm(url=None, **caps):
    return openai_engine.OpenAILLM({"url": url, "capabilities": {"model": "m", **caps}})


def test_model_is_required(fake_client):
    with pytest.raises(OpticsError, match="capabilities.model"):
        openai_engine.OpenAILLM({"capabilities": {}})


def test_missing_key_without_url_fails_fast(fake_client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(OpticsError, match="capabilities.api_key or OPENAI_API_KEY"):
        _llm()


def test_invalid_response_format_is_rejected(fake_client):
    with pytest.raises(OpticsError, match="response_format"):
        _llm(response_format="xml")


def test_images_are_sent_as_data_urls(fake_client):
    _llm().generate("look", images=[PNG])
    parts = fake_client.requests[-1]["messages"][-1]["content"]
    assert parts[0] == {"type": "text", "text": "look"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()


def test_json_object_mode_moves_the_schema_into_the_system_prompt(fake_client):
    _llm(response_format="json_object").generate("hi", system="be brief", response_schema=SCHEMA)
    request = fake_client.requests[-1]
    assert request["response_format"] == {"type": "json_object"}
    system = request["messages"][0]["content"]
    assert system.startswith("be brief")
    assert json.dumps(SCHEMA) in system


def test_truncated_reply_raises(fake_client):
    fake_client.finish_reason = "length"
    llm = _llm()
    with pytest.raises(OpticsError, match="max_tokens"):
        llm.generate("hi", response_schema=SCHEMA)


def test_generate_json_parses_the_reply(fake_client):
    assert _llm().generate_json("hi", SCHEMA) == {"action": "done"}


def test_undecodable_reply_surfaces_from_generate_json(fake_client):
    fake_client.message = SimpleNamespace(content="not json", refusal=None)
    llm = _llm()
    with pytest.raises(OpticsError, match="non-JSON"):
        llm.generate_json("hi", SCHEMA)


class TestStrictSchema:

    def test_nullable_enum_accepts_null(self):
        schema = {"type": "object", "properties": {"a": {"type": "string", "enum": ["x"]}}}
        assert strict_schema(schema)["properties"]["a"]["enum"] == ["x", None]
