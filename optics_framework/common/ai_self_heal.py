"""AI self-heal — last-resort fallback when every locate strategy fails.

When the normal element-location ladder (XPath -> on-screen text -> OCR -> image) exhausts
for a locate-based keyword, :class:`AISelfHealHandler` asks an :class:`LLMInterface` to look at
the current screen (screenshot + condensed page source) plus the keyword's intent and the
available keyword catalog, and to emit ONE keyword call at a time (e.g. ``press_element "Meesho"``,
``scroll "down"``) until the keyword's goal is achieved or a small step budget is hit.

Unlike the old coordinate-guessing approach, this routes through the framework's own keyword
methods — so ``press_element "Meesho"`` uses the full locate ladder (XPath -> text -> OCR -> image)
instead of the LLM blindly tapping pixel percentages.

The handler is decoupled from any controller — it depends only on an ``llm``, a keyword executor
callable, a keyword catalog callable, and screenshot/page-source provider callables — so it is
unit-testable with fakes.
"""

import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from optics_framework.common.error import OpticsError
from optics_framework.common.llm_interface import LLMInterface
from optics_framework.common.logging_config import internal_logger


# Provider callables (best-effort; may return None when unavailable).
ScreenshotProvider = Callable[[], Optional[bytes]]
PagesourceProvider = Callable[[], Optional[str]]

# Keyword executor: takes a keyword line string (e.g. 'press_element "Login"'),
# returns an ExecResult-like with .ok and .message.
KeywordExecutor = Callable[[str], Any]

# Keyword catalog entry.
@dataclass
class HealKeywordSpec:
    """A keyword the self-healer may call, with a human-readable signature."""
    name: str
    signature: str


# Keyword catalog provider.
KeywordCatalog = Callable[[], List[HealKeywordSpec]]

# Let the UI settle after a layout-changing action before re-screenshotting.
_SETTLE_SECONDS = 1.5

# Keywords that just change what's on screen but don't complete a locate-based goal.
_NON_COMPLETING_KEYWORDS = (
    "scroll", "swipe_by_percentage", "swipe", "press_keycode", "press_by_percentage",
)


@dataclass
class HealContext:
    """Everything the LLM is told about the failed keyword and where the flow is."""

    intent_keyword: str          # e.g. "press_element"
    intent_params: List[str] = field(default_factory=list)
    element: str = ""            # the resolved locator the normal ladder failed to find
    resolved_vars: Dict[str, str] = field(default_factory=dict)
    recent_steps: List[Tuple[str, List[str]]] = field(default_factory=list)
    failed_strategies: List[str] = field(default_factory=list)


@dataclass
class HealAction:
    """A single parsed action the LLM asked for."""

    action: str                  # "keyword" | "done" | "give_up"
    keyword: str = ""            # snake_case keyword name (when action == "keyword")
    params: List[str] = field(default_factory=list)
    reason: str = ""
    completed: bool = True       # False for intermediate navigation steps


@dataclass
class HealResult:
    """Terminal outcome of :meth:`AISelfHealHandler.heal`."""

    ok: bool
    action: Optional[HealAction] = None
    message: str = ""


# Structured-output schema. Mirrors the NL agent's schema pattern (flat, no anyOf).
HEAL_ACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {
            "type": "string",
            "description": "Brief reasoning about the current screen and the chosen action.",
        },
        "action": {
            "type": "string",
            "enum": ["keyword", "done", "give_up"],
            "description": "keyword = run one keyword; done = goal achieved; give_up = blocked.",
        },
        "keyword": {
            "type": "string",
            "description": "snake_case keyword name (only when action == keyword).",
        },
        "params": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Positional parameters in keyword-signature order (strings).",
        },
        "completed": {
            "type": "boolean",
            "description": (
                "Set to true if this keyword call completes the original keyword's goal "
                "(e.g. pressing the final target element). Set to false if this is an "
                "intermediate navigation step (e.g. scrolling to reveal the target, pressing "
                "a menu to navigate, typing in a search bar) and more steps are needed."
            ),
        },
    },
    "required": ["reason", "action"],
    "propertyOrdering": ["reason", "action", "keyword", "params", "completed"],
}


HEAL_SYSTEM_PROMPT = """\
You are the LAST-RESORT self-healing layer of a UI test-automation framework. The normal element \
locators (XPath, on-screen text, OCR, image matching) have ALL failed to find the target for the \
keyword described below. Your job is to look at the current screen and execute framework keywords \
step-by-step until the original keyword's goal is achieved.

YOU MUST FINISH THE JOB YOURSELF by issuing keyword calls. You have access to the same keywords \
the framework uses. The most important one is `press_element` — it takes a visible text label \
as its element parameter and the framework will locate the element using its full strategy ladder \
(XPath → text → OCR → image matching). NAME TARGETS BY THEIR VISIBLE TEXT whenever possible.

WORKFLOW:
1. Look at the screenshot and UI hierarchy to understand the current screen state.
2. If the target element IS visible on screen, call the appropriate keyword to act on it \
(e.g. `press_element` with the element's visible text). Set `completed` to true.
3. If the target element is NOT visible, navigate to reveal it — scroll, swipe, press a menu, \
or type in a search bar. Set `completed` to false for intermediate steps.
4. Use `action: "done"` when you believe the original keyword's goal has been fully achieved.
5. Use `action: "give_up"` only when there is no recoverable next action.

TARGETING POLICY (strict order of preference):
1. Name the target by its VISIBLE TEXT as the element parameter (e.g. press_element ["Meesho"]). \
Use the condensed hierarchy for EXACT text / content-desc / resource id.
2. If the target is not visible, swipe to reveal it, THEN name it by text.
3. For system buttons (home/back/recents), use press_keycode with the Android keycode \
(HOME=3, BACK=4, RECENTS=187, ENTER=66).
4. LAST RESORT: use press_by_percentage with coordinate percentages.
5. Use swipe instead of scroll.

GESTURE DIRECTIONS:
- To reveal content below (swipe down the list to see lower items), you must use direction "up" (finger drags from bottom to top).
- To reveal content above (swipe up the list to see upper items), you must use direction "down" (finger drags from top to bottom).

RULES:
- Emit exactly ONE action per turn as JSON.
- Keep `reason` short.
- Prefer naming elements by text over guessing coordinates.
- Set `completed` to true only when this step achieves the original keyword's goal.
"""

_MAX_THOUGHT_CHARS = 160


class AISelfHealHandler:
    """Bounded loop that drives the device via keyword calls to land a failed keyword."""

    def __init__(
        self,
        llm: LLMInterface,
        keyword_executor: KeywordExecutor,
        keyword_catalog: KeywordCatalog,
        *,
        max_steps: int = 5,
    ) -> None:
        self.llm = llm
        self.keyword_executor = keyword_executor
        self.keyword_catalog = keyword_catalog
        self.max_steps = max_steps

    def _execute_single_step(
        self,
        step: int,
        ctx: HealContext,
        screenshot_provider: ScreenshotProvider,
        pagesource_provider: PagesourceProvider,
        catalog: List[HealKeywordSpec],
    ) -> Optional[HealResult]:
        """Execute a single iteration of self-healing and return terminal result or None to continue."""
        png = self._safe_call(screenshot_provider)
        if not png:
            return HealResult(False, message="No screenshot available for self-heal.")
        page_source = self._safe_call(pagesource_provider)

        prompt = self._build_prompt(ctx, step, page_source, catalog)
        try:
            raw = self.llm.generate_json(
                prompt, HEAL_ACTION_SCHEMA, images=[png],
                system=HEAL_SYSTEM_PROMPT, temperature=0.0,
            )
        except OpticsError as exc:
            return HealResult(False, message=f"LLM error: {exc.message}")
        except Exception as exc:  # noqa: BLE001 - self-heal must never raise a new error type
            return HealResult(False, message=f"LLM error: {exc}")

        action = self._validate(raw)
        internal_logger.info(
            "AI self-heal step %d/%d for '%s': action=%s keyword=%s params=%s reason=%s",
            step + 1, self.max_steps, ctx.intent_keyword, action.action,
            action.keyword, action.params, action.reason[:_MAX_THOUGHT_CHARS],
        )

        if action.action == "give_up":
            return HealResult(False, action=action, message=action.reason or "Model gave up.")
        if action.action == "done":
            return HealResult(True, action=action, message=action.reason or "Goal reached.")

        # action.action == "keyword"
        try:
            done = self._dispatch(action)
        except Exception as exc:  # noqa: BLE001 - a keyword error ends the heal cleanly
            return HealResult(False, action=action, message=f"Keyword failed: {exc}")

        if done:
            return HealResult(True, action=action, message=action.reason or "Healed.")

        return None

    def heal(
        self,
        ctx: HealContext,
        screenshot_provider: ScreenshotProvider,
        pagesource_provider: PagesourceProvider,
    ) -> HealResult:
        """Attempt to recover the failed keyword. Never raises — returns ok=False on any problem."""
        catalog = self.keyword_catalog()

        for step in range(self.max_steps):
            result = self._execute_single_step(
                step, ctx, screenshot_provider, pagesource_provider, catalog
            )
            if result is not None:
                return result
            # Intermediate step: UI changed, loop to re-observe.

        return HealResult(False, message="Self-heal step budget exhausted.")

    # -- internals -------------------------------------------------------------

    @staticmethod
    def _safe_call(provider: Optional[Callable[[], Any]]) -> Any:
        if provider is None:
            return None
        try:
            return provider()
        except Exception as exc:  # noqa: BLE001 - providers are best-effort aids
            internal_logger.debug("AI self-heal: provider unavailable: %s", exc)
            return None

    def _dispatch(self, action: HealAction) -> bool:
        """Execute one keyword via the injected executor. Returns True when the goal is complete."""
        if not action.keyword:
            return False

        line = self._build_line(action.keyword, action.params)
        result = self.keyword_executor(line)

        ok = getattr(result, "ok", False)
        if not ok:
            msg = getattr(result, "message", "keyword failed")
            internal_logger.debug("AI self-heal: keyword '%s' failed: %s", line, msg)
            # Don't abort the whole heal on a single keyword failure — the LLM can
            # try a different approach on the next step, so let the UI settle first.
            time.sleep(_SETTLE_SECONDS)
            return False

        # A completing keyword with completed=True means the goal is done — return
        # immediately without waiting, since there's no next screenshot to settle for.
        if action.keyword not in _NON_COMPLETING_KEYWORDS and action.completed:
            return True

        # Intermediate/navigation step: let the UI settle before the next screenshot.
        time.sleep(_SETTLE_SECONDS)
        return False

    @staticmethod
    def _validate(raw: Dict[str, Any]) -> HealAction:
        if not isinstance(raw, dict):
            raw = {}
        action = raw.get("action")
        if action not in ("keyword", "done", "give_up"):
            action = "give_up"

        params_raw = raw.get("params") or []
        params = [str(p) for p in params_raw] if isinstance(params_raw, list) else []

        completed = raw.get("completed")
        if completed is None:
            # Default: completing keywords complete by default; navigation ones don't.
            keyword = str(raw.get("keyword") or "")
            completed = keyword not in _NON_COMPLETING_KEYWORDS
        else:
            completed = bool(completed)

        return HealAction(
            action=action,
            keyword=str(raw.get("keyword") or ""),
            params=params,
            reason=str(raw.get("reason") or ""),
            completed=completed,
        )

    @staticmethod
    def _build_line(keyword: str, params: List[str]) -> str:
        """Build a keyword line string from keyword name and params."""
        if not params:
            return keyword
        return keyword + " " + " ".join(shlex.quote(p) for p in params)

    def _build_prompt(
        self, ctx: HealContext, step: int, page_source: Optional[str],
        catalog: List[HealKeywordSpec],
    ) -> str:
        params = " ".join(str(p) for p in ctx.intent_params)
        lines = [
            f"FAILED KEYWORD: {ctx.intent_keyword} {params}".rstrip(),
            f"TARGET ELEMENT (not found by normal locators): {ctx.element}",
        ]
        if ctx.resolved_vars:
            pairs = ", ".join(f"{k}={v}" for k, v in ctx.resolved_vars.items())
            lines.append(f"RESOLVED VARIABLES: {pairs}")
        if ctx.failed_strategies:
            lines.append("STRATEGIES ALREADY TRIED (all failed): " + ", ".join(ctx.failed_strategies))

        lines.append("")
        lines.append("AVAILABLE KEYWORDS (name and parameters):")
        for spec in catalog:
            lines.append(f"  {spec.signature}")

        if page_source:
            lines.append("")
            lines.append(
                "CURRENT SCREEN ELEMENTS (condensed UI hierarchy — class, text, desc, "
                "resource id, bounds [x1,y1][x2,y2], state flags):"
            )
            lines.append(page_source)
        if ctx.recent_steps:
            lines.append("")
            lines.append("RECENT SUCCESSFUL STEPS (most recent last):")
            for idx, (kw, kw_params) in enumerate(ctx.recent_steps, 1):
                lines.append(f"  {idx}. {kw} {kw_params}")
        if step > 0:
            lines.append("")
            lines.append(
                f"This is attempt {step + 1}. Your previous keyword changed the screen; "
                "re-read the CURRENT screenshot and complete the goal."
            )
        lines.append("")
        lines.append("The attached image is the CURRENT screen. Decide the SINGLE next action as JSON.")
        return "\n".join(lines)
