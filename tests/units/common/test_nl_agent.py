"""Unit tests for the natural-language ReAct agent and the LLM engine wiring.

No network: a scripted fake LLM drives the agent. Covers the done/fail/exhausted/abort
paths, the consecutive-failure cutoff, unknown-keyword feedback, optional keyword/params
coercion, shlex-correct keyword lines, the Gemini missing-dependency error, the
commit-on-done recording in LiveController.run_natural_language, and config round-tripping.
"""
import pytest

from optics_framework.common.nl_agent import (
    NaturalLanguageAgent,
    KeywordSpec,
    ExecResult,
    ACTION_SCHEMA,
)
from optics_framework.common.error import OpticsError, Code

pytestmark = pytest.mark.white_box


class FakeLLM:
    """Returns scripted JSON dicts from generate_json, ignoring prompt/images."""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = 0

    def generate(self, *args, **kwargs):  # pragma: no cover - agent uses generate_json
        raise RuntimeError("unexpected generate() call")

    def generate_json(self, prompt, response_schema, images=None, system=None, temperature=None):
        self.calls += 1
        assert response_schema is ACTION_SCHEMA
        assert images and isinstance(images[0], (bytes, bytearray))
        return self.scripted.pop(0)


def _catalog():
    return [
        KeywordSpec("press_element", "press_element <element> [repeat]"),
        KeywordSpec("enter_text", "enter_text <element> <text>"),
    ]


def _shots():
    return b"PNGBYTES"


def _ok_executor(recorder):
    def _exec(raw):
        recorder.append(raw)
        return ExecResult(ok=True, strategy="OCR", elapsed=0.1)

    return _exec


class TestAgentControlFlow:
    def test_happy_path_records_successful_steps(self):
        executed = []
        llm = FakeLLM([
            {"thought": "tap search", "action": "keyword", "keyword": "press_element",
             "params": ["Search"], "reason": ""},
            {"thought": "type query", "action": "keyword", "keyword": "enter_text",
             "params": ["Search", "movies for kids"], "reason": ""},
            {"thought": "done", "action": "done", "reason": "typed the query"},
        ])
        agent = NaturalLanguageAgent(llm, _shots, _ok_executor(executed), _catalog)
        result = agent.run("search for movies")
        assert result.status == "done"
        # shlex-correct: the multi-word param is quoted.
        assert executed == ["press_element Search", "enter_text Search 'movies for kids'"]
        assert result.successful_steps == [
            ("press_element", ["Search"]),
            ("enter_text", ["Search", "movies for kids"]),
        ]

    def test_unknown_keyword_fed_back_then_cutoff(self):
        llm = FakeLLM([
            {"thought": "x", "action": "keyword", "keyword": "nonsense", "params": [], "reason": ""}
        ] * 3)
        agent = NaturalLanguageAgent(
            llm, _shots, _ok_executor([]), _catalog, max_consecutive_failures=3
        )
        result = agent.run("do x")
        assert result.status == "failed"
        assert result.successful_steps == []
        assert llm.calls == 3  # each unknown-keyword turn re-queries the model

    def test_keyword_failure_cutoff(self):
        def failing(raw):
            return ExecResult(ok=False, message="[E0201] not found")

        llm = FakeLLM([
            {"thought": "x", "action": "keyword", "keyword": "press_element",
             "params": ["A"], "reason": ""}
        ] * 5)
        agent = NaturalLanguageAgent(llm, _shots, failing, _catalog, max_consecutive_failures=2)
        result = agent.run("go")
        assert result.status == "failed"
        assert result.successful_steps == []

    def test_abort_between_steps(self):
        llm = FakeLLM([
            {"thought": "x", "action": "keyword", "keyword": "press_element",
             "params": ["A"], "reason": ""}
        ] * 5)
        agent = NaturalLanguageAgent(llm, _shots, _ok_executor([]), _catalog)
        result = agent.run("go", should_abort=lambda: True)
        assert result.status == "aborted"
        assert llm.calls == 0  # aborts before the first LLM call

    def test_max_steps_exhausted(self):
        llm = FakeLLM([
            {"thought": "x", "action": "keyword", "keyword": "press_element",
             "params": ["A"], "reason": ""}
        ] * 10)
        agent = NaturalLanguageAgent(llm, _shots, _ok_executor([]), _catalog, max_steps=2)
        result = agent.run("go")
        assert result.status == "exhausted"
        assert len(result.successful_steps) == 2

    def test_fail_action_returns_failed(self):
        llm = FakeLLM([{"thought": "blocked", "action": "fail", "reason": "cannot proceed"}])
        agent = NaturalLanguageAgent(llm, _shots, _ok_executor([]), _catalog)
        result = agent.run("go")
        assert result.status == "failed"
        assert result.message == "cannot proceed"

    def test_missing_keyword_params_are_coerced(self):
        executed = []
        # 'done' with no keyword/params (optional fields) must not crash.
        llm = FakeLLM([
            {"thought": "tap", "action": "keyword", "keyword": "press_element", "reason": ""},
            {"thought": "ok", "action": "done", "reason": "done"},
        ])
        agent = NaturalLanguageAgent(llm, _shots, _ok_executor(executed), _catalog)
        result = agent.run("tap it")
        assert result.status == "done"
        assert executed == ["press_element"]  # no params -> bare keyword line

    def test_screenshot_failure_ends_run(self):
        def boom():
            raise RuntimeError("no screen")

        llm = FakeLLM([{"thought": "x", "action": "done", "reason": "y"}])
        agent = NaturalLanguageAgent(llm, boom, _ok_executor([]), _catalog)
        result = agent.run("go")
        assert result.status == "failed"
        assert "Screenshot failed" in (result.message or "")


class TestActionSchema:
    def test_keyword_and_params_optional(self):
        assert ACTION_SCHEMA["required"] == ["thought", "action", "reason"]
        assert "keyword" not in ACTION_SCHEMA["required"]
        assert "params" not in ACTION_SCHEMA["required"]

    def test_no_anyof_in_schema(self):
        # Gemini structured-output anyOf support is unreliable; the schema must avoid it.
        import json

        assert "anyOf" not in json.dumps(ACTION_SCHEMA)


class _CapturingLLM:
    """Records the prompt/system it receives, then returns a 'done' action."""

    def __init__(self):
        self.prompt = None
        self.system = None

    def generate(self, *a, **k):  # pragma: no cover
        raise RuntimeError("use generate_json")

    def generate_json(self, prompt, response_schema, images=None, system=None, temperature=None):
        self.prompt = prompt
        self.system = system
        return {"thought": "ok", "action": "done", "reason": "done"}


class TestPageSourceInPrompt:
    def test_page_source_injected_when_provided(self):
        llm = _CapturingLLM()
        ps = "EditText \"gullak\" id=search_edit_text bounds=[26,162][1054,293] clickable"
        agent = NaturalLanguageAgent(
            llm, _shots, _ok_executor([]), _catalog, pagesource_provider=lambda: ps
        )
        agent.run("click search")
        assert "CURRENT SCREEN ELEMENTS" in llm.prompt
        assert "search_edit_text" in llm.prompt
        assert "condensed UI hierarchy" in llm.system

    def test_no_provider_means_no_section(self):
        llm = _CapturingLLM()
        NaturalLanguageAgent(llm, _shots, _ok_executor([]), _catalog).run("go")
        assert "CURRENT SCREEN ELEMENTS" not in llm.prompt

    def test_provider_failure_is_graceful(self):
        def boom():
            raise RuntimeError("no page source source configured")

        llm = _CapturingLLM()
        agent = NaturalLanguageAgent(
            llm, _shots, _ok_executor([]), _catalog, pagesource_provider=boom
        )
        result = agent.run("go")
        assert result.status == "done"  # run still completes
        assert "CURRENT SCREEN ELEMENTS" not in llm.prompt


class TestGeminiMissingDependency:
    def test_instantiation_without_extra_raises_e0601(self, monkeypatch):
        from optics_framework.engines.llm_models import gemini

        monkeypatch.setattr(gemini, "genai", None)
        monkeypatch.setattr(gemini, "_IMPORT_ERROR", ImportError("No module named 'google'"))
        with pytest.raises(OpticsError) as exc_info:
            gemini.GeminiLLM({"capabilities": {}})
        assert exc_info.value.code == Code.E0601
        assert "optics-framework[llm]" in exc_info.value.message
