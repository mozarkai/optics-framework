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


# Callable contracts (kept as plain Callables to avoid Protocol import churn).
ScreenshotProvider = Callable[[], bytes]
KeywordExecutor = Callable[[str], ExecResult]
KeywordCatalog = Callable[[], List[KeywordSpec]]
StepCallback = Callable[[AgentStep], None]
AbortCallback = Callable[[], bool]


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
instruction. Each turn you are shown a screenshot of the current device screen and the list \
of available keywords, and you must reply with exactly ONE next action as JSON.

TARGETING POLICY (text-first, coordinate fallback):
- Prefer naming the target by its VISIBLE TEXT label as the `element` parameter \
(e.g. press_element with params ["Search"]). The framework self-heals element location across \
XPath -> on-screen text -> OCR -> image, so a plain text label usually resolves.
- For ambiguous text, prefix the label with `text_only:` to force OCR matching \
(e.g. "text_only:Search").
- ONLY when the target is an icon with no readable text (e.g. a home/back icon) and cannot be \
named, estimate its position from the screenshot and use `press_by_percentage` with \
params [percent_x, percent_y] where each is 0-100 of the screen. Prefer percentages over \
absolute pixel coordinates.

RULES:
- Emit exactly one action per turn.
- Use `action: "done"` ONLY when the instruction is fully satisfied as visible on screen.
- Use `action: "fail"` when you are blocked with no recoverable next action.
- If the previous step FAILED, do NOT repeat it identically: change the locator (try a \
different label, `text_only:`, scroll/swipe to reveal the element, or the coordinate fallback).
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
        max_steps: int = 15,
        max_consecutive_failures: int = 3,
    ) -> None:
        self.llm = llm
        self.screenshot_provider = screenshot_provider
        self.keyword_executor = keyword_executor
        self.keyword_catalog = keyword_catalog
        self.element_names = element_names
        self.max_steps = max_steps
        self.max_consecutive_failures = max_consecutive_failures

    def run(
        self,
        instruction: str,
        on_step: Optional[StepCallback] = None,
        should_abort: Optional[AbortCallback] = None,
    ) -> AgentResult:
        history: List[AgentStep] = []
        successful: List[Tuple[str, List[str]]] = []
        consecutive_failures = 0
        catalog = self.keyword_catalog()
        catalog_names = {spec.name for spec in catalog}

        for _ in range(self.max_steps):
            if should_abort is not None and should_abort():
                return AgentResult("aborted", history, "Aborted by user.", successful)

            try:
                png = self.screenshot_provider()
            except Exception as exc:  # noqa: BLE001 - screenshot failures end the run cleanly
                return AgentResult("failed", history, f"Screenshot failed: {exc}", successful)

            prompt = self._build_prompt(instruction, catalog, history)
            try:
                raw = self.llm.generate_json(
                    prompt, ACTION_SCHEMA, images=[png], system=SYSTEM_PROMPT, temperature=0.0
                )
            except OpticsError as exc:
                return AgentResult("failed", history, f"LLM error: {exc.message}", successful)

            step = self._validate(raw)
            if on_step is not None:
                on_step(step)  # decision/thinking emission (observation is still None)

            if step.action == "done":
                history.append(step)
                return AgentResult("done", history, step.reason or "Goal reached.", successful)
            if step.action == "fail":
                history.append(step)
                return AgentResult("failed", history, step.reason or "Model gave up.", successful)

            # action == "keyword"
            if not step.keyword or step.keyword not in catalog_names:
                consecutive_failures = self._record_failure(
                    step, f"FAIL: unknown keyword '{step.keyword}'", history, on_step,
                    consecutive_failures,
                )
                if consecutive_failures >= self.max_consecutive_failures:
                    return AgentResult("failed", history, "Too many consecutive failures.", successful)
                continue

            line = self._build_line(step.keyword, step.params)
            result = self.keyword_executor(line)
            step.exec_result = result
            step.observation = (
                f"PASS (strategy={result.strategy})" if result.ok else f"FAIL: {result.message}"
            )
            history.append(step)
            if on_step is not None:
                on_step(step)

            if result.ok:
                consecutive_failures = 0
                successful.append((step.keyword, step.params))
            else:
                consecutive_failures += 1
                if consecutive_failures >= self.max_consecutive_failures:
                    return AgentResult(
                        "failed", history, "Too many consecutive keyword failures.", successful
                    )

        return AgentResult("exhausted", history, "Reached the maximum number of steps.", successful)

    # -- helpers ---------------------------------------------------------------

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

    def _build_prompt(
        self, instruction: str, catalog: List[KeywordSpec], history: List[AgentStep]
    ) -> str:
        lines = [f"INSTRUCTION: {instruction}", "", "AVAILABLE KEYWORDS (name and parameters):"]
        lines.extend(f"  {spec.signature}" for spec in catalog)

        if self.element_names is not None:
            names = self.element_names() or []
            if names:
                lines.append("")
                lines.append("NAMED ELEMENTS (reference as ${name}):")
                lines.append("  " + ", ".join("${" + n + "}" for n in names))

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
