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
    CURATION_SCHEMA,
    SYSTEM_PROMPT,
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


class CuratingFakeLLM(FakeLLM):
    """FakeLLM that also serves the post-'done' curation turn.

    ACTION_SCHEMA turns come from the base ``scripted`` list; the single CURATION_SCHEMA
    turn returns ``curation_reply``. Curation is text-only, so ``images`` must be None.
    """

    def __init__(self, scripted, curation_reply):
        super().__init__(scripted)
        self.curation_reply = curation_reply
        self.curation_calls = 0

    def generate_json(self, prompt, response_schema, images=None, system=None, temperature=None):
        if response_schema is CURATION_SCHEMA:
            self.curation_calls += 1
            assert images is None
            return self.curation_reply
        return super().generate_json(prompt, response_schema, images, system, temperature)


def _kw(keyword, params):
    return {"thought": f"do {keyword}", "action": "keyword", "keyword": keyword,
            "params": params, "reason": ""}


def _done():
    return {"thought": "ok", "action": "done", "reason": "goal reached"}


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

    def test_repeated_coordinate_guessing_is_blocked(self):
        # press_by_percentage always "passes" mechanically, so without the guardrail
        # the model could nudge coordinates forever. After max_blind_repeats the run
        # must stop instead of flailing.
        executed = []
        coord = lambda px, py: {  # noqa: E731
            "thought": "tap home", "action": "keyword",
            "keyword": "press_by_percentage", "params": [px, py], "reason": "guess",
        }
        llm = FakeLLM([coord("50", "97"), coord("50", "95"), coord("50", "98"),
                       coord("50", "99"), coord("50", "96")])
        agent = NaturalLanguageAgent(
            llm, _shots, _ok_executor(executed),
            lambda: [KeywordSpec("press_by_percentage", "press_by_percentage <x> <y>")],
            max_blind_repeats=3,
        )
        result = agent.run("click home button")
        assert result.status == "failed"
        assert "coordinate" in (result.message or "").lower()
        # Only the allowed number of taps actually executed; the rest were blocked.
        assert len(executed) == 3

    @pytest.mark.parametrize("keyword", [
        "press_by_coordinates", "scroll", "detect_and_press", "swipe_from_element",
        "press_keycode",
    ])
    def test_any_non_verifying_keyword_is_bounded(self, keyword):
        # Every keyword that acts without verifying a target (coordinate taps, scroll,
        # detect_and_press's swallow-on-not-found, element-anchored gestures, keycodes)
        # must be bounded the same way — not just press_by_percentage.
        executed = []
        step = {"thought": "t", "action": "keyword", "keyword": keyword,
                "params": ["x"], "reason": "r"}
        llm = FakeLLM([dict(step) for _ in range(6)])
        agent = NaturalLanguageAgent(
            llm, _shots, _ok_executor(executed),
            lambda: [KeywordSpec(keyword, f"{keyword} <p>")],
            max_blind_repeats=3,
        )
        result = agent.run("do it")
        assert result.status == "failed"
        assert len(executed) == 3

    def test_verifying_keyword_resets_blind_streak(self):
        # A locating/verifying action between blind keywords resets the streak, so
        # legitimate occasional coordinate use is not penalised.
        executed = []
        coord = {"thought": "t", "action": "keyword", "keyword": "press_by_percentage",
                 "params": ["50", "50"], "reason": "r"}
        press = {"thought": "t", "action": "keyword", "keyword": "press_element",
                 "params": ["Search"], "reason": "r"}
        done = {"thought": "ok", "action": "done", "reason": "done"}
        llm = FakeLLM([coord, coord, press, coord, coord, done])
        agent = NaturalLanguageAgent(
            llm, _shots, _ok_executor(executed),
            lambda: [KeywordSpec("press_by_percentage", "press_by_percentage <x> <y>"),
                     KeywordSpec("press_element", "press_element <element>")],
            max_blind_repeats=3,
        )
        result = agent.run("do it")
        assert result.status == "done"
        assert len(executed) == 5  # nothing blocked

    def test_cycling_gesture_variants_is_blocked(self):
        # Real failure mode for "swipe up": the model cycled scroll -> swipe_by_percentage
        # -> swipe -> swipe_from_element -> ... None repeats the SAME keyword, but they are
        # all non-verifying gestures, so the CLASS streak must bound them together.
        executed = []
        def g(keyword, *params):
            return {"thought": "t", "action": "keyword", "keyword": keyword,
                    "params": list(params), "reason": "r"}
        llm = FakeLLM([
            g("scroll", "up"),
            g("swipe_by_percentage", "50", "80", "up", "50"),
            g("swipe", "540", "2000", "up", "1000"),
            g("swipe_from_element", "Apps", "up", "500"),
            g("swipe_by_percentage", "50", "90", "up", "80"),
        ])
        agent = NaturalLanguageAgent(
            llm, _shots, _ok_executor(executed),
            lambda: [KeywordSpec("scroll", "scroll <dir>"),
                     KeywordSpec("swipe", "swipe <x> <y> <dir> <len>"),
                     KeywordSpec("swipe_by_percentage", "swipe_by_percentage <x> <y> <dir> <len>"),
                     KeywordSpec("swipe_from_element", "swipe_from_element <el> <dir> <len>")],
            max_blind_repeats=3,
        )
        result = agent.run("swipe up")
        assert result.status == "failed"
        assert len(executed) == 3  # blocked at the 4th gesture regardless of variant

    def test_system_prompt_documents_keycodes(self):
        # System buttons must steer the model to press_keycode rather than coordinates.
        assert "press_keycode" in SYSTEM_PROMPT
        for token in ("HOME=3", "BACK=4", "187"):
            assert token in SYSTEM_PROMPT

    def test_validate_non_dict_degrades_to_fail(self):
        # generate_json guarantees decodable JSON, not a JSON object. A valid
        # list/scalar reply must become a recoverable 'fail' step, not raise
        # AttributeError and abort the run.
        for bad in ([1, 2, 3], "hello", 42, None):
            step = NaturalLanguageAgent._validate(bad)
            assert step.action == "fail"


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


class TestCurationSchema:
    def test_no_anyof_in_schema(self):
        import json

        assert "anyOf" not in json.dumps(CURATION_SCHEMA)

    def test_required_fields(self):
        assert CURATION_SCHEMA["required"] == ["keep", "reason"]


class TestCurateOnDone:
    """The post-'done' curation pass (curate_on_done=True) prunes to a replayable subset."""

    def _agent(self, llm, executed=None, curate=True):
        return NaturalLanguageAgent(
            llm, _shots, _ok_executor(executed if executed is not None else []),
            _catalog, curate_on_done=curate,
        )

    def test_curation_prunes_non_contributing_step(self):
        executed = []
        llm = CuratingFakeLLM(
            [_kw("press_element", ["Menu"]),      # 1 - dead end
             _kw("press_element", ["Search"]),    # 2 - needed
             _kw("enter_text", ["Search", "kids"]),  # 3 - needed
             _done()],
            curation_reply={"keep": [2, 3], "reason": "step 1 was a dead-end"},
        )
        result = self._agent(llm, executed).run("search kids movies")
        assert result.status == "done"
        assert llm.curation_calls == 1
        assert result.successful_steps == [
            ("press_element", ["Search"]),
            ("enter_text", ["Search", "kids"]),
        ]
        # All three still executed live; curation is post-hoc.
        assert len(executed) == 3
        assert "kept 2 of 3 steps" in (result.message or "")

    def test_curation_sorts_dedups_and_range_checks(self):
        llm = CuratingFakeLLM(
            [_kw("press_element", ["A"]), _kw("press_element", ["B"]),
             _kw("press_element", ["C"]), _done()],
            curation_reply={"keep": [3, 1, 1, 99, 0], "reason": "reorder/dup/out-of-range"},
        )
        result = self._agent(llm).run("go")
        assert result.successful_steps == [("press_element", ["A"]), ("press_element", ["C"])]

    @pytest.mark.parametrize("bad_reply", [
        {"keep": [], "reason": "drop all"},          # empty -> keep all
        {"keep": "nope", "reason": "r"},             # not a list
        {"reason": "no keep key"},                   # missing keep
        {"keep": ["x", 2.5, True], "reason": "r"},   # no valid ints (bool rejected)
        {"keep": [1, 2], "reason": "kept all"},      # kept everything -> no-op
        [1, 2],                                      # non-dict response
        "garbage",                                   # non-dict scalar
    ])
    def test_curation_falls_back_to_all_steps(self, bad_reply):
        llm = CuratingFakeLLM(
            [_kw("press_element", ["A"]), _kw("press_element", ["B"]), _done()],
            curation_reply=bad_reply,
        )
        result = self._agent(llm).run("go")
        assert result.successful_steps == [("press_element", ["A"]), ("press_element", ["B"])]

    def test_curation_llm_error_keeps_all_steps(self):
        class Raising(CuratingFakeLLM):
            def generate_json(self, prompt, schema, images=None, system=None, temperature=None):
                if schema is CURATION_SCHEMA:
                    raise OpticsError(Code.E0801, message="boom")
                return super().generate_json(prompt, schema, images, system, temperature)

        llm = Raising(
            [_kw("press_element", ["A"]), _kw("press_element", ["B"]), _done()],
            curation_reply=None,
        )
        result = self._agent(llm).run("go")
        assert result.status == "done"
        assert result.successful_steps == [("press_element", ["A"]), ("press_element", ["B"])]

    def test_curation_skipped_for_single_step(self):
        llm = CuratingFakeLLM(
            [_kw("press_element", ["A"]), _done()],
            curation_reply={"keep": [1], "reason": "r"},
        )
        result = self._agent(llm).run("go")
        assert llm.curation_calls == 0
        assert result.successful_steps == [("press_element", ["A"])]

    def test_toggle_off_disables_curation(self):
        llm = CuratingFakeLLM(
            [_kw("press_element", ["A"]), _kw("press_element", ["B"]), _done()],
            curation_reply={"keep": [1], "reason": "r"},
        )
        result = self._agent(llm, curate=False).run("go")
        assert llm.curation_calls == 0
        assert len(result.successful_steps) == 2

    def test_failed_steps_are_not_selectable_candidates(self):
        # A failing keyword between two passing ones: it must not appear in successful_steps,
        # and the curation prompt must number only the passing steps as candidates.
        captured = {}

        def executor(raw):
            # "press_element B" fails; A and C pass.
            ok = "B" not in raw
            return ExecResult(ok=ok, strategy="OCR", message=None if ok else "not found")

        class Capturing(CuratingFakeLLM):
            def generate_json(self, prompt, schema, images=None, system=None, temperature=None):
                if schema is CURATION_SCHEMA:
                    captured["prompt"] = prompt
                return super().generate_json(prompt, schema, images, system, temperature)

        llm = Capturing(
            [_kw("press_element", ["A"]), _kw("press_element", ["B"]),
             _kw("press_element", ["C"]), _done()],
            curation_reply={"keep": [1, 2], "reason": "both real steps"},
        )
        agent = NaturalLanguageAgent(llm, _shots, executor, _catalog, curate_on_done=True)
        result = agent.run("go")
        # Only A and C succeeded; curation keeps both (candidates 1 and 2).
        assert result.successful_steps == [("press_element", ["A"]), ("press_element", ["C"])]
        prompt = captured["prompt"]
        assert "1. press_element ['A']" in prompt
        assert "2. press_element ['C']" in prompt
        assert "[FAILED, not selectable] press_element ['B']" in prompt
