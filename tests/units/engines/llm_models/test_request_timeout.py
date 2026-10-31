"""A request to a server that never answers raises E0801 once capabilities.timeout_s passes."""
import socket
import threading
import time

import pytest

from optics_framework.common.error import Code, OpticsError

pytestmark = pytest.mark.white_box


@pytest.fixture
def silent_server():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    held = []

    def accept():
        while True:
            try:
                held.append(server.accept()[0])
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}"
    server.close()
    for conn in held:
        conn.close()


def _openai(url):
    pytest.importorskip("openai")
    from optics_framework.engines.llm_models.openai import OpenAILLM

    return OpenAILLM({"url": url, "capabilities": {"model": "m", "timeout_s": 0.2}})


def _anthropic(url):
    pytest.importorskip("anthropic")
    from optics_framework.engines.llm_models.anthropic import AnthropicLLM

    return AnthropicLLM({"url": url, "capabilities": {"model": "m", "timeout_s": 0.2}})


def _gemini(url):
    pytest.importorskip("google.genai")
    from optics_framework.engines.llm_models.gemini import GeminiLLM

    return GeminiLLM({"capabilities": {"timeout_s": 0.2}})


@pytest.mark.parametrize("make", [_openai, _anthropic, _gemini])
def test_hung_request_raises(make, silent_server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", silent_server)
    llm = make(silent_server)
    start = time.monotonic()
    with pytest.raises(OpticsError) as exc_info:
        llm.generate_json("hi", {"type": "object"})
    assert exc_info.value.code == Code.E0801
    assert time.monotonic() - start < 10


@pytest.mark.parametrize("value", [0, -1, "soon"])
def test_timeout_must_be_positive(value):
    pytest.importorskip("openai")
    from optics_framework.engines.llm_models.openai import OpenAILLM

    with pytest.raises(OpticsError) as exc_info:
        OpenAILLM({"url": "http://localhost:1", "capabilities": {"model": "m", "timeout_s": value}})
    assert exc_info.value.code == Code.E0601
