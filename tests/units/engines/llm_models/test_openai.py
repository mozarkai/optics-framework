"""OpenAILLM request construction, with the openai client replaced by a fake."""
import base64
import json
from types import SimpleNamespace

import pytest

from optics_framework.common.ai_self_heal import HEAL_ACTION_SCHEMA
from optics_framework.common.error import OpticsError
from optics_framework.common.nl_agent import ACTION_SCHEMA
from optics_framework.common.step_curation import CURATION_SCHEMA
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
    with pytest.raises(OpticsError, match="set OPENAI_API_KEY"):
        _llm()


def test_local_server_without_key_gets_placeholder(fake_client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    _llm(url="http://localhost:11434/v1")
    assert fake_client.clients[-1] == {"api_key": "unused", "base_url": "http://localhost:11434/v1"}


def test_api_key_env_names_the_variable(fake_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    _llm(url="https://openrouter.ai/api/v1", api_key_env="OPENROUTER_API_KEY")
    assert fake_client.clients[-1]["api_key"] == "or-key"


def test_invalid_response_format_is_rejected(fake_client):
    with pytest.raises(OpticsError, match="response_format"):
        _llm(response_format="xml")


def test_text_only_prompt_is_a_plain_string(fake_client):
    _llm().generate("hi", system="be brief")
    assert fake_client.requests[-1]["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]


def test_images_are_sent_as_data_urls(fake_client):
    _llm().generate("look", images=[PNG])
    parts = fake_client.requests[-1]["messages"][-1]["content"]
    assert parts[0] == {"type": "text", "text": "look"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()


def test_schema_is_sent_as_strict_json_schema(fake_client):
    _llm().generate("hi", response_schema=SCHEMA)
    response_format = fake_client.requests[-1]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == strict_schema(SCHEMA)


def test_json_object_mode_moves_the_schema_into_the_system_prompt(fake_client):
    _llm(response_format="json_object").generate("hi", system="be brief", response_schema=SCHEMA)
    request = fake_client.requests[-1]
    assert request["response_format"] == {"type": "json_object"}
    system = request["messages"][0]["content"]
    assert system.startswith("be brief")
    assert json.dumps(SCHEMA) in system


def test_temperature_precedence(fake_client):
    llm = _llm(temperature=0.0)
    llm.generate("hi")
    assert fake_client.requests[-1]["temperature"] == 0.0
    llm.generate("hi", temperature=0.7)
    assert fake_client.requests[-1]["temperature"] == 0.7
    _llm().generate("hi")
    assert "temperature" not in fake_client.requests[-1]


def test_max_tokens_is_sent_only_when_configured(fake_client):
    _llm().generate("hi")
    assert "max_completion_tokens" not in fake_client.requests[-1]
    _llm(max_tokens=512).generate("hi")
    assert fake_client.requests[-1]["max_completion_tokens"] == 512


def test_truncated_reply_raises(fake_client):
    fake_client.finish_reason = "length"
    llm = _llm()
    with pytest.raises(OpticsError, match="max_tokens"):
        llm.generate("hi", response_schema=SCHEMA)


def test_refusal_raises(fake_client):
    fake_client.message = SimpleNamespace(content=None, refusal="no")
    llm = _llm()
    with pytest.raises(OpticsError, match="refused"):
        llm.generate("hi", response_schema=SCHEMA)


def test_generate_json_parses_the_reply(fake_client):
    assert _llm().generate_json("hi", SCHEMA) == {"action": "done"}


def test_strict_mode_nulls_for_optional_properties_are_dropped(fake_client):
    fake_client.message = SimpleNamespace(content='{"action": "done", "params": null}', refusal=None)
    assert _llm().generate_json("hi", SCHEMA) == {"action": "done"}


def test_undecodable_reply_surfaces_from_generate_json(fake_client):
    fake_client.message = SimpleNamespace(content="not json", refusal=None)
    llm = _llm()
    with pytest.raises(OpticsError, match="non-JSON"):
        llm.generate_json("hi", SCHEMA)


class TestStrictSchema:
    def test_optional_properties_become_required_and_nullable(self):
        out = strict_schema(SCHEMA)
        assert out["required"] == ["action", "params"]
        assert out["additionalProperties"] is False
        assert out["properties"]["action"] == SCHEMA["properties"]["action"]
        assert out["properties"]["params"]["type"] == ["array", "null"]

    def test_nullable_enum_accepts_null(self):
        schema = {"type": "object", "properties": {"a": {"type": "string", "enum": ["x"]}}}
        assert strict_schema(schema)["properties"]["a"]["enum"] == ["x", None]

    def test_nested_objects_are_closed(self):
        schema = {
            "type": "array",
            "items": {"type": "object", "properties": {"a": {"type": "string"}}},
        }
        assert strict_schema(schema)["items"]["additionalProperties"] is False

    def test_input_is_not_mutated(self):
        before = json.dumps(SCHEMA, sort_keys=True)
        strict_schema(SCHEMA)
        assert json.dumps(SCHEMA, sort_keys=True) == before

    @pytest.mark.parametrize("schema", [ACTION_SCHEMA, HEAL_ACTION_SCHEMA, CURATION_SCHEMA])
    def test_framework_schemas_satisfy_strict_mode(self, schema):
        out = strict_schema(schema)
        assert set(out["required"]) == set(out["properties"])
        assert out["additionalProperties"] is False
