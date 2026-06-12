"""Natural-language -> keyword orchestrator (ReAct loop).

Given a plain-English instruction, this agent drives a UI test-automation framework one
keyword at a time: it captures a screenshot, asks an :class:`LLMInterface` for the single
next keyword to run, executes it via an injected callback, observes the result, and loops
until the goal is reached, the model gives up, an abort is requested, or a step budget is hit.

The agent is decoupled from any particular controller/UI: it only depends on the injected
``screenshot_provider``, ``keyword_executor`` and ``keyword_catalog`` callables, so it can be
reused outside ``optics live``.
"""

import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from optics_framework.common.llm_interface import LLMInterface
from optics_framework.common.error import OpticsError
from optics_framework.common.logging_config import internal_logger


@dataclass
class KeywordSpec:
    """A keyword the agent may call, with a human-readable signature."""

    name: str
    signature: str


@dataclass
class ExecResult:
    """Outcome of executing a single keyword line."""

    ok: bool
    strategy: Optional[str] = None
    message: Optional[str] = None
    elapsed: float = 0.0


@dataclass
class AgentStep:
    """One decision (and its outcome) in the ReAct loop."""

    thought: str
    action: str  # "keyword" | "done" | "fail"
    keyword: str = ""
    params: List[str] = field(default_factory=list)
    reason: str = ""
    observation: Optional[str] = None  # filled after execution; None during the decision phase
    exec_result: Optional[ExecResult] = None


@dataclass
class AgentResult:
    """Terminal outcome of a full :meth:`NaturalLanguageAgent.run`."""

    status: str  # "done" | "failed" | "exhausted" | "aborted"
    steps: List[AgentStep] = field(default_factory=list)
    message: Optional[str] = None
    successful_steps: List[Tuple[str, List[str]]] = field(default_factory=list)


@dataclass
class _RunState:
    """Mutable bookkeeping threaded through one :meth:`NaturalLanguageAgent.run`."""

    history: List[AgentStep] = field(default_factory=list)
    successful: List[Tuple[str, List[str]]] = field(default_factory=list)
    consecutive_failures: int = 0
    # Anti-flail: the last non-verifying keyword used and how many times in a row.
    # These keywords report PASS even when they achieve nothing (see
    # DEFAULT_NON_VERIFYING_KEYWORDS), so consecutive_failures can't catch the model
    # repeating one forever (e.g. nudging coordinates 50,97 -> 50,95 -> 50,98 ...).
    last_blind_keyword: Optional[str] = None
    blind_repeats: int = 0


# Keywords that perform an action WITHOUT locating or asserting a target element, so a
# PASS does NOT mean the goal was reached:
#   - coordinate / percentage taps and swipes report PASS even when they hit nothing;
#   - scroll / press_keycode / sleep always succeed mechanically;
#   - detect_and_press swallows "not found" and does nothing (still PASS);
#   - *_until_element_appears return PASS on timeout even if the element never appeared;
#   - select_dropdown_option is currently a no-op stub (always PASS).
# Repeating the SAME one many times in a row is the model flailing, not progressing, so
# the agent bounds consecutive repeats of any keyword in this set.
DEFAULT_NON_VERIFYING_KEYWORDS: Tuple[str, ...] = (
    "press_by_percentage",
    "press_by_coordinates",
    "swipe",
    "swipe_by_percentage",
    "scroll",
    "press_keycode",
    "sleep",
    "detect_and_press",
    "scroll_until_element_appears",
    "swipe_until_element_appears",
    "select_dropdown_option",
    "enter_text_direct",
    "enter_text_using_keyboard",
)


# Callable contracts (kept as plain Callables to avoid Protocol import churn).
ScreenshotProvider = Callable[[], bytes]
KeywordExecutor = Callable[[str], ExecResult]
KeywordCatalog = Callable[[], List[KeywordSpec]]
StepCallback = Callable[[AgentStep], None]
AbortCallback = Callable[[], bool]
# Returns a condensed UI hierarchy (stripped page source) or None when unavailable.
PagesourceProvider = Callable[[], Optional[str]]


# Structured-output schema. Only thought/action/reason are required so the model is never
# forced to emit dummy keyword/params on a done/fail turn. anyOf/discriminated unions are
# deliberately avoided (Gemini response_schema support for them is unreliable).
ACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "thought": {
            "type": "string",
            "description": "Brief reasoning about the current screen and the next action.",
        },
        "action": {
            "type": "string",
            "enum": ["keyword", "done", "fail"],
            "description": "keyword = run one keyword; done = goal reached; fail = give up.",
        },
        "keyword": {
            "type": "string",
            "description": "snake_case keyword name (only when action == keyword).",
        },
        "params": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Positional parameters in keyword-signature order.",
        },
        "reason": {
            "type": "string",
            "description": "Why this action, or why done/fail.",
        },
    },
    "required": ["thought", "action", "reason"],
    "propertyOrdering": ["thought", "action", "keyword", "params", "reason"],
}


SYSTEM_PROMPT = """\
You drive a UI test-automation framework ONE keyword at a time to fulfil a natural-language \
instruction. Each turn you are shown a screenshot of the current device screen, a condensed \
UI hierarchy of the on-screen elements (when available), and the list of available keywords; \
you must reply with exactly ONE next action as JSON.

CRITICAL — VERIFY, DON'T FLAIL:
- A `PASS` observation means the keyword EXECUTED, NOT that your goal was achieved. ALWAYS \
re-read the new screenshot to confirm the action had the intended effect.
- If the screen did not change as you expected, that action did NOT work. Do NOT repeat it, \
and do NOT nudge coordinates by a few percent and try again — switch to a fundamentally \
different approach.
- Tapping a coordinate ALWAYS "succeeds" mechanically even if it hits nothing, so never trust \
a coordinate tap's PASS — judge only by the resulting screen.

SYSTEM NAVIGATION — USE KEYCODES (Android), NOT COORDINATES:
For hardware / system buttons (home, back, recents, etc.) call `press_keycode` with the \
keycode. NEVER guess coordinates for these — they are off-screen system keys.
  HOME=3, BACK=4, RECENT APPS / APP SWITCH=187, ENTER=66, BACKSPACE/DEL=67, MENU=82, \
SEARCH=84, VOLUME UP=24, VOLUME DOWN=25, POWER=26, ESCAPE=111, TAB=61, CAMERA=27.
  Examples: "go home" -> press_keycode ["3"]; "press back" -> press_keycode ["4"]; \
"open recent apps" -> press_keycode ["187"]; "submit/enter" -> press_keycode ["66"].

TARGETING POLICY (in strict order of preference):
1. SYSTEM button (home/back/recents/enter/menu/volume/...) -> `press_keycode` with the code above.
2. On-screen control -> name it by its VISIBLE TEXT as the `element` parameter \
(e.g. press_element with params ["Search"]). The framework self-heals element location across \
XPath -> on-screen text -> OCR -> image, so a plain text label usually resolves. Use the \
condensed hierarchy for the EXACT text / content-desc / resource id.
3. Ambiguous text -> prefix the label with `text_only:` to force OCR matching \
(e.g. "text_only:Search").
4. Target not visible -> scroll/swipe to reveal it, THEN target it by text.
5. LAST RESORT only — an icon with no readable text AND no keycode -> read its bounds \
[x1,y1][x2,y2] from the hierarchy to compute a precise center and use `press_by_percentage` \
with params [percent_x, percent_y] (each 0-100). You may attempt coordinates at most TWICE \
for a given target; if that does not work, STOP and use `action: "fail"`.

USING THE UI HIERARCHY:
- When the condensed hierarchy is provided, use it together with the screenshot: it gives the \
exact on-screen text, content-desc, resource ids, element bounds [x1,y1][x2,y2], and flags \
(clickable/scrollable/selected/editable). A target present in the hierarchy should be named by \
its text — not tapped by coordinate.

RULES:
- Emit exactly one action per turn.
- Use `action: "done"` ONLY when the instruction is fully satisfied as visible on screen.
- Use `action: "fail"` when you are blocked with no recoverable next action (including when \
coordinate guessing has already failed).
- If the previous step did not achieve its goal, do NOT repeat it identically: change strategy \
(keycode, a different text label, `text_only:`, or scroll/swipe to reveal the element).
- Keep `thought` short. Put the keyword name in `keyword` and its positional arguments in \
`params` (strings), matching the keyword's signature.
"""

_MAX_THOUGHT_CHARS = 160


class NaturalLanguageAgent:
    """Bounded ReAct loop translating an instruction into keyword executions."""

    def __init__(
        self,
        llm: LLMInterface,
        screenshot_provider: ScreenshotProvider,
        keyword_executor: KeywordExecutor,
        keyword_catalog: KeywordCatalog,
        *,
        element_names: Optional[Callable[[], List[str]]] = None,
        pagesource_provider: Optional[PagesourceProvider] = None,
        max_steps: int = 15,
        max_consecutive_failures: int = 3,
        max_blind_repeats: int = 3,
        non_verifying_keywords: Tuple[str, ...] = DEFAULT_NON_VERIFYING_KEYWORDS,
    ) -> None:
        self.llm = llm
        self.screenshot_provider = screenshot_provider
        self.keyword_executor = keyword_executor
        self.keyword_catalog = keyword_catalog
        self.element_names = element_names
        self.pagesource_provider = pagesource_provider
        self.max_steps = max_steps
        self.max_consecutive_failures = max_consecutive_failures
        self.max_blind_repeats = max_blind_repeats
        self.non_verifying_keywords = non_verifying_keywords

    def run(
        self,
        instruction: str,
        on_step: Optional[StepCallback] = None,
        should_abort: Optional[AbortCallback] = None,
    ) -> AgentResult:
        state = _RunState()
        catalog = self.keyword_catalog()
        catalog_names = {spec.name for spec in catalog}

        for _ in range(self.max_steps):
            if should_abort is not None and should_abort():
                return AgentResult("aborted", state.history, "Aborted by user.", state.successful)

            terminal = self._run_one_step(instruction, catalog, catalog_names, state, on_step)
            if terminal is not None:
                return terminal

        return AgentResult(
            "exhausted", state.history, "Reached the maximum number of steps.", state.successful
        )

    def _run_one_step(
        self,
        instruction: str,
        catalog: List[KeywordSpec],
        catalog_names: set[str],
        state: "_RunState",
        on_step: Optional[StepCallback],
    ) -> Optional[AgentResult]:
        """Run one decision turn. Returns a terminal ``AgentResult`` or ``None`` to continue."""
        try:
            png = self.screenshot_provider()
        except Exception as exc:  # noqa: BLE001 - screenshot failures end the run cleanly
            return AgentResult("failed", state.history, f"Screenshot failed: {exc}", state.successful)

        page_source = self._capture_page_source()
        prompt = self._build_prompt(instruction, catalog, state.history, page_source)
        try:
            raw = self.llm.generate_json(
                prompt, ACTION_SCHEMA, images=[png], system=SYSTEM_PROMPT, temperature=0.0
            )
        except OpticsError as exc:
            return AgentResult("failed", state.history, f"LLM error: {exc.message}", state.successful)

        step = self._validate(raw)
        if on_step is not None:
            on_step(step)  # decision/thinking emission (observation is still None)

        if step.action == "done":
            state.history.append(step)
            return AgentResult("done", state.history, step.reason or "Goal reached.", state.successful)
        if step.action == "fail":
            state.history.append(step)
            return AgentResult("failed", state.history, step.reason or "Model gave up.", state.successful)

        return self._execute_keyword_step(step, catalog_names, state, on_step)

    def _execute_keyword_step(
        self,
        step: AgentStep,
        catalog_names: set[str],
        state: "_RunState",
        on_step: Optional[StepCallback],
    ) -> Optional[AgentResult]:
        """Execute a ``keyword`` action. Returns a terminal ``AgentResult`` or ``None``."""
        if not step.keyword or step.keyword not in catalog_names:
            state.consecutive_failures = self._record_failure(
                step, f"FAIL: unknown keyword '{step.keyword}'", state.history, on_step,
                state.consecutive_failures,
            )
            if state.consecutive_failures >= self.max_consecutive_failures:
                return AgentResult(
                    "failed", state.history, "Too many consecutive failures.", state.successful
                )
            return None

        # Anti-flail: non-verifying keywords (coordinate taps, scroll, keycode,
        # detect_and_press, ...) report PASS even when they achieve nothing, so the
        # consecutive-failure cutoff can never catch the model repeating one forever.
        # Bound consecutive repeats of the SAME such keyword and stop with a clear reason.
        is_blind = step.keyword in self.non_verifying_keywords
        prospective_repeats = (
            state.blind_repeats + 1 if is_blind and step.keyword == state.last_blind_keyword else 1
        )
        if is_blind and prospective_repeats > self.max_blind_repeats:
            observation = (
                f"BLOCKED: '{step.keyword}' was repeated {state.blind_repeats} times "
                "without reaching the goal — it does not verify a target, so repeating it "
                "is not making progress."
            )
            self._record_failure(step, observation, state.history, on_step, 0)
            return AgentResult(
                "failed",
                state.history,
                f"Repeated '{step.keyword}' without reaching the goal. Name the target by its "
                "on-screen text, or use a keycode for system buttons (home=3/back=4/recents=187).",
                state.successful,
            )

        line = self._build_line(step.keyword, step.params)
        result = self.keyword_executor(line)
        step.exec_result = result
        step.observation = (
            f"PASS (strategy={result.strategy})" if result.ok else f"FAIL: {result.message}"
        )
        state.history.append(step)
        if on_step is not None:
            on_step(step)

        # Track the consecutive-same-blind-keyword streak; any other keyword resets it.
        if is_blind:
            state.last_blind_keyword = step.keyword
            state.blind_repeats = prospective_repeats
        else:
            state.last_blind_keyword = None
            state.blind_repeats = 0

        if result.ok:
            state.consecutive_failures = 0
            state.successful.append((step.keyword, step.params))
            return None

        state.consecutive_failures += 1
        if state.consecutive_failures >= self.max_consecutive_failures:
            return AgentResult(
                "failed", state.history, "Too many consecutive keyword failures.", state.successful
            )
        return None

    @staticmethod
    def _record_failure(
        step: AgentStep,
        observation: str,
        history: List[AgentStep],
        on_step: Optional[StepCallback],
        consecutive_failures: int,
    ) -> int:
        step.observation = observation
        step.exec_result = ExecResult(ok=False, message=observation)
        history.append(step)
        if on_step is not None:
            on_step(step)
        internal_logger.debug("NL agent step failed: %s", observation)
        return consecutive_failures + 1

    @staticmethod
    def _validate(raw: Dict[str, Any]) -> AgentStep:
        # generate_json only guarantees decodable JSON, not a JSON object. A
        # valid-but-non-object reply (list/scalar) must degrade to a recoverable
        # "fail" step rather than raise AttributeError and abort the whole run.
        if not isinstance(raw, dict):
            raw = {}
        action = raw.get("action")
        if action not in ("keyword", "done", "fail"):
            action = "fail"
        params_raw = raw.get("params") or []
        params = [str(p) for p in params_raw] if isinstance(params_raw, list) else []
        return AgentStep(
            thought=str(raw.get("thought", "")),
            action=action,
            keyword=str(raw.get("keyword") or ""),
            params=params,
            reason=str(raw.get("reason", "")),
        )

    @staticmethod
    def _build_line(keyword: str, params: List[str]) -> str:
        if not params:
            return keyword
        return keyword + " " + " ".join(shlex.quote(p) for p in params)

    def _capture_page_source(self) -> Optional[str]:
        """Best-effort condensed UI hierarchy; ``None`` when unavailable/unsupported."""
        if self.pagesource_provider is None:
            return None
        try:
            return self.pagesource_provider() or None
        except Exception as exc:  # noqa: BLE001 - page source is an optional aid, never fatal
            internal_logger.debug("NL agent: page source unavailable: %s", exc)
            return None

    def _build_prompt(
        self,
        instruction: str,
        catalog: List[KeywordSpec],
        history: List[AgentStep],
        page_source: Optional[str] = None,
    ) -> str:
        lines = [f"INSTRUCTION: {instruction}", "", "AVAILABLE KEYWORDS (name and parameters):"]
        lines.extend(f"  {spec.signature}" for spec in catalog)

        if self.element_names is not None:
            names = self.element_names() or []
            if names:
                lines.append("")
                lines.append("NAMED ELEMENTS (reference as ${name}):")
                lines.append("  " + ", ".join("${" + n + "}" for n in names))

        if page_source:
            lines.append("")
            lines.append(
                "CURRENT SCREEN ELEMENTS (condensed UI hierarchy — class, text, "
                "desc, resource id, bounds [x1,y1][x2,y2], state flags):"
            )
            lines.append(page_source)

        lines.append("")
        lines.append("STEPS SO FAR:")
        if not history:
            lines.append("  (none yet)")
        else:
            for idx, step in enumerate(history, 1):
                thought = step.thought[:_MAX_THOUGHT_CHARS]
                if step.action == "keyword":
                    lines.append(
                        f"  {idx}. {step.keyword} {step.params} -> "
                        f"{step.observation or 'pending'}  | {thought}"
                    )
                else:
                    lines.append(f"  {idx}. action={step.action}  | {thought}")

        lines.append("")
        lines.append("The attached image is the CURRENT screen. Decide the SINGLE next action as JSON.")
        return "\n".join(lines)
