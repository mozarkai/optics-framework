"""AnthropicLLM request construction, with the anthropic client replaced by a fake."""
import base64
from types import SimpleNamespace

import pytest

from optics_framework.common.error import OpticsError
from optics_framework.engines.llm_models import anthropic as anthropic_engine

pytest.importorskip("anthropic", reason="anthropic engine tests need the anthropic extra")

pytestmark = pytest.mark.white_box

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
SCHEMA = {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]}


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(payload):
    return SimpleNamespace(type="tool_use", input=payload)


class _FakeMessages:
    def __init__(self):
        self.requests = []
        self.content = [_tool_use({"action": "done"})]
        self.stop_reason = "tool_use"

    def create(self, **request):
        self.requests.append(request)
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0)
        return SimpleNamespace(content=self.content, stop_reason=self.stop_reason, usage=usage)


@pytest.fixture
def fake_sdk(monkeypatch):
    messages = _FakeMessages()
    built = []

    def _factory(kind):
        def _client(**kwargs):
            built.append((kind, kwargs))
            return SimpleNamespace(messages=messages)
        return _client

    sdk = SimpleNamespace(
        Anthropic=_factory("anthropic"),
        AnthropicBedrock=_factory("bedrock"),
        AnthropicVertex=_factory("vertex"),
    )
    monkeypatch.setattr(anthropic_engine, "anthropic_sdk", sdk)
    messages.built = built
    return messages


def _llm(url=None, **caps):
    return anthropic_engine.AnthropicLLM({"url": url, "capabilities": {"model": "m", **caps}})


def test_model_is_required(fake_sdk):
    with pytest.raises(OpticsError, match="capabilities.model"):
        anthropic_engine.AnthropicLLM({"capabilities": {}})


def test_invalid_platform_is_rejected(fake_sdk):
    with pytest.raises(OpticsError, match="platform"):
        _llm(platform="azure")


def test_default_platform_reads_named_key_variable(fake_sdk, monkeypatch):
    monkeypatch.setenv("TEAM_CLAUDE_KEY", "k")
    _llm(url="https://gateway.example", api_key_env="TEAM_CLAUDE_KEY")
    assert fake_sdk.built[-1] == ("anthropic", {"api_key": "k", "base_url": "https://gateway.example"})


def test_bedrock_platform(fake_sdk):
    _llm(platform="bedrock", region="us-east-1")
    assert fake_sdk.built[-1] == ("bedrock", {"aws_region": "us-east-1", "base_url": None})


def test_vertex_platform(fake_sdk):
    _llm(platform="vertex", project="p", region="us-east5")
    assert fake_sdk.built[-1] == ("vertex", {"base_url": None, "region": "us-east5", "project_id": "p"})


def test_schema_forces_a_single_tool_call(fake_sdk):
    reply = _llm().generate("hi", system="be brief", response_schema=SCHEMA)
    request = fake_sdk.requests[-1]
    assert request["tools"][0]["input_schema"] == SCHEMA
    assert request["tool_choice"] == {"type": "tool", "name": request["tools"][0]["name"]}
    assert request["system"] == [
        {"type": "text", "text": "be brief", "cache_control": {"type": "ephemeral"}}
    ]
    assert reply == '{"action": "done"}'


def test_images_precede_the_prompt(fake_sdk):
    fake_sdk.content = [_text("ok")]
    _llm().generate("look", images=[PNG])
    content = fake_sdk.requests[-1]["messages"][0]["content"]
    assert content[0]["source"] == {
        "type": "base64", "media_type": "image/png", "data": base64.b64encode(PNG).decode(),
    }
    assert content[1] == {"type": "text", "text": "look"}


def test_plain_text_reply_without_schema(fake_sdk):
    fake_sdk.content = [_text("a"), _text("b")]
    assert _llm().generate("hi") == "ab"
    assert "tools" not in fake_sdk.requests[-1]


def test_temperature_precedence(fake_sdk):
    llm = _llm(temperature=0.0)
    llm.generate("hi")
    assert fake_sdk.requests[-1]["temperature"] == 0.0
    llm.generate("hi", temperature=0.7)
    assert fake_sdk.requests[-1]["temperature"] == 0.7
    _llm().generate("hi")
    assert "temperature" not in fake_sdk.requests[-1]


def test_truncated_reply_raises(fake_sdk):
    fake_sdk.stop_reason = "max_tokens"
    llm = _llm(max_tokens=10)
    with pytest.raises(OpticsError, match="max_tokens"):
        llm.generate("hi", response_schema=SCHEMA)


def test_missing_tool_call_raises(fake_sdk):
    fake_sdk.content = [_text("sorry")]
    llm = _llm()
    with pytest.raises(OpticsError, match="no structured response"):
        llm.generate("hi", response_schema=SCHEMA)


def test_generate_json_parses_the_tool_input(fake_sdk):
    assert _llm().generate_json("hi", SCHEMA) == {"action": "done"}
