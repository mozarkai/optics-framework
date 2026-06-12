"""AI self-heal — last-resort fallback when every locate strategy fails.

When the normal element-location ladder (XPath -> on-screen text -> OCR -> image) exhausts
for a locate-based keyword, :class:`AISelfHealHandler` asks an :class:`LLMInterface` to look at
the current screen (screenshot + condensed page source) plus the keyword's intent and recent
context, and to take ONE corrective device action at a time (tap / type / swipe / scroll) until
the keyword's goal is achieved or a small step budget is hit.

The handler runs *independently* of :class:`StrategyManager`: it drives the device directly via
the :class:`DriverInterface` primitives, so the LLM must FINISH THE JOB itself (it cannot defer
back to the normal locators). It is decoupled from any controller — it depends only on an ``llm``,
a ``driver``, and screenshot/page-source provider callables — so it is unit-testable with fakes.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from optics_framework.common.error import OpticsError
from optics_framework.common.llm_interface import LLMInterface
from optics_framework.common.logging_config import internal_logger


# Provider callables (best-effort; may return None when unavailable).
ScreenshotProvider = Callable[[], Optional[bytes]]
PagesourceProvider = Callable[[], Optional[str]]

# Actions that complete the keyword's goal (heal succeeds) vs. intermediate ones that
# merely change what is on screen (loop continues to re-observe).
_TERMINAL_ACTIONS = ("tap", "type")
_LAYOUT_ACTIONS = ("tap", "type", "swipe", "scroll")

# Defaults for driver args the LLM schema does not carry.
_DEFAULT_SWIPE_LENGTH_PCT = 50
_DEFAULT_SCROLL_DURATION_MS = 1000
# Let the UI settle after a layout-changing action before re-screenshotting.
_SETTLE_SECONDS = 1.5


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

    action: str                  # "tap" | "type" | "swipe" | "scroll" | "give_up"
    percent_x: Optional[float] = None
    percent_y: Optional[float] = None
    text: str = ""
    direction: str = ""
    reason: str = ""


@dataclass
class HealResult:
    """Terminal outcome of :meth:`AISelfHealHandler.heal`."""

    ok: bool
    action: Optional[HealAction] = None
    message: str = ""


# Flat schema (no anyOf/discriminated unions — Gemini's response_schema support for them is
# unreliable; same discipline as nl_agent.ACTION_SCHEMA). percent_x/percent_y are also used by
# the "type" action so the field can be focused (tapped) before typing.
HEAL_ACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {
            "type": "string",
            "description": "Brief reasoning about the screen and the chosen action.",
        },
        "action": {
            "type": "string",
            "enum": ["tap", "type", "swipe", "scroll", "give_up"],
            "description": "tap/type complete the goal; swipe/scroll reveal it; give_up if blocked.",
        },
        "percent_x": {
            "type": "number",
            "description": "X position 0-100 of screen width (for tap, and the field to tap before type).",
        },
        "percent_y": {
            "type": "number",
            "description": "Y position 0-100 of screen height (for tap, and the field to tap before type).",
        },
        "text": {"type": "string", "description": "Text to type (only when action == type)."},
        "direction": {
            "type": "string",
            "enum": ["up", "down", "left", "right"],
            "description": "Direction for swipe/scroll.",
        },
    },
    "required": ["reason", "action"],
    "propertyOrdering": ["reason", "action", "percent_x", "percent_y", "text", "direction"],
}


HEAL_SYSTEM_PROMPT = """\
You are the LAST-RESORT self-healing layer of a UI test-automation framework. The normal element \
locators (XPath, on-screen text, OCR, image matching) have ALL failed to find the target for the \
keyword described below. Your job is to look at the current screen and take ONE corrective device \
action so the keyword's goal is achieved.

YOU MUST FINISH THE JOB YOURSELF — you cannot hand control back to the normal locators. If the \
target is not on screen, `scroll` or `swipe` to reveal it, and once it is visible complete the \
action yourself (`tap` to press it, `type` to enter text). Only use `give_up` when there is no \
recoverable next action.

COORDINATES: always express positions as PERCENTAGES of the screen (percent_x, percent_y in 0-100). \
Read element bounds from the condensed UI hierarchy (when provided) to compute a precise center, \
or estimate from the screenshot. Percentages are resolution-independent and safer than pixels.

ACTIONS:
- tap: press at (percent_x, percent_y). Completes a press/click goal.
- type: focus the field at (percent_x, percent_y) then enter `text`. Completes a text-entry goal.
- swipe: swipe from (percent_x, percent_y) in `direction`. Intermediate (re-observe afterwards).
- scroll: scroll the screen in `direction`. Intermediate (re-observe afterwards).
- give_up: nothing recoverable remains.

RULES: emit exactly ONE action as JSON. Keep `reason` short. Be conservative: prefer revealing the \
real target and tapping it over blindly tapping a guessed position.
"""

_MAX_THOUGHT_CHARS = 160


class AISelfHealHandler:
    """Bounded loop that drives the device via LLM decisions to land a failed keyword."""

    def __init__(self, llm: LLMInterface, driver: Any, *, max_steps: int = 2) -> None:
        self.llm = llm
        self.driver = driver
        self.max_steps = max_steps

    def heal(
        self,
        ctx: HealContext,
        screenshot_provider: ScreenshotProvider,
        pagesource_provider: PagesourceProvider,
    ) -> HealResult:
        """Attempt to recover the failed keyword. Never raises — returns ok=False on any problem."""
        for step in range(self.max_steps):
            png = self._safe_call(screenshot_provider)
            if not png:
                return HealResult(False, message="No screenshot available for self-heal.")
            page_source = self._safe_call(pagesource_provider)

            prompt = self._build_prompt(ctx, step, page_source)
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
                "AI self-heal step %d/%d for '%s': action=%s reason=%s",
                step + 1, self.max_steps, ctx.intent_keyword, action.action,
                action.reason[:_MAX_THOUGHT_CHARS],
            )

            if action.action == "give_up":
                return HealResult(False, action=action, message=action.reason or "Model gave up.")

            try:
                done = self._dispatch(action)
            except Exception as exc:  # noqa: BLE001 - a driver error ends the heal cleanly
                return HealResult(False, action=action, message=f"Action failed: {exc}")

            if done:
                return HealResult(True, action=action, message=action.reason or "Healed.")
            # Intermediate (scroll/swipe): UI changed, loop to re-observe.

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
        """Execute one action via the driver. Returns True when the keyword goal is complete."""
        px = action.percent_x if action.percent_x is not None else 50.0
        py = action.percent_y if action.percent_y is not None else 50.0

        if action.action == "tap":
            self.driver.press_percentage_coordinates(px, py, 1)
        elif action.action == "type":
            self.driver.press_percentage_coordinates(px, py, 1)
            self.driver.enter_text(action.text)
        elif action.action == "swipe":
            direction = action.direction or "up"
            self.driver.swipe_percentage(int(px), int(py), direction, _DEFAULT_SWIPE_LENGTH_PCT)
        elif action.action == "scroll":
            direction = action.direction or "down"
            self.driver.scroll(direction, _DEFAULT_SCROLL_DURATION_MS)
        else:  # pragma: no cover - _validate guarantees a known action
            return False

        if action.action in _LAYOUT_ACTIONS:
            # Handler bypasses keyword-level settling; let the UI finish animating.
            time.sleep(_SETTLE_SECONDS)
        return action.action in _TERMINAL_ACTIONS

    @staticmethod
    def _validate(raw: Dict[str, Any]) -> HealAction:
        action = raw.get("action")
        if action not in ("tap", "type", "swipe", "scroll", "give_up"):
            action = "give_up"
        return HealAction(
            action=action,
            percent_x=_as_float(raw.get("percent_x")),
            percent_y=_as_float(raw.get("percent_y")),
            text=str(raw.get("text") or ""),
            direction=str(raw.get("direction") or ""),
            reason=str(raw.get("reason") or ""),
        )

    def _build_prompt(self, ctx: HealContext, step: int, page_source: Optional[str]) -> str:
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
                f"This is attempt {step + 1}. Your previous action changed the screen; "
                "re-read the CURRENT screenshot and complete the goal (tap/type)."
            )
        lines.append("")
        lines.append("The attached image is the CURRENT screen. Decide the SINGLE next action as JSON.")
        return "\n".join(lines)


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
