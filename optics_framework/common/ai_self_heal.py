"""AI self-heal — last-resort fallback when every locate strategy fails.

When the normal element-location ladder (XPath -> on-screen text -> OCR -> image) exhausts
for a locate-based keyword, :class:`AISelfHealHandler` asks an :class:`LLMInterface` to look at
the current screen (screenshot + ref rows or condensed page source) plus the keyword's intent
and the available keyword catalog, and to emit ONE keyword call at a time (e.g. ``press_element
"Sign in"``, ``scroll "down"``) until the keyword's goal is achieved or a small step budget is hit.

Unlike the old coordinate-guessing approach, this routes through the framework's own keyword
methods — so ``press_element "Sign in"`` uses the full locate ladder (XPath -> text -> OCR -> image)
instead of the LLM blindly tapping pixel percentages.

The handler is decoupled from any controller — it depends only on an ``llm``, a keyword executor
callable, a keyword catalog callable, and screenshot/page-source provider callables — so it is
unit-testable with fakes.
"""

import shlex
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

from optics_framework.common.error import OpticsError
from optics_framework.common.llm_interface import LLMInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.screen_snapshot import (
    REF_RE,
    RefTable,
    Row,
    Snapshot,
    TargetError,
    acting_ladder,
    live_ladder,
    loc_stable,
    render_rows,
    saved_ladder,
)
from optics_framework.common.step_curation import curate_steps

# Provider callables (best-effort; may return None when unavailable).
ScreenshotProvider = Callable[[], Optional[bytes]]
PagesourceProvider = Callable[[], Optional[str]]
SnapshotProvider = Callable[[], list[dict] | None]


@dataclass
class KeywordExecResult:
    """Outcome of running a single keyword line via :data:`KeywordExecutor`.

    Distinct from :class:`HealResult`, which is the terminal outcome of the whole
    heal loop (which `HealAction` finished it, every step taken) — this is only the
    per-call pass/fail that `_dispatch` reads back from the executor.
    """

    ok: bool
    message: str = ""


# Keyword executor: takes a keyword line string (e.g. 'press_element "Login"'),
# returns the outcome of running it.
KeywordExecutor = Callable[[str], KeywordExecResult]

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
    target: str = ""             # a ref from the screen rows, or a raw element
    record_params: Optional[List[str]] = None  # replayable params when they differ from params


@dataclass
class HealResult:
    """Terminal outcome of :meth:`AISelfHealHandler.heal`.

    ``steps_taken`` is every attempted keyword line, in order, for the
    budget-exhausted diagnostic. ``suggested_steps`` is the clean replayable
    recovery: only the steps that PASSED, curated on success.
    """

    ok: bool
    action: Optional[HealAction] = None
    message: str = ""
    steps_taken: List[str] = field(default_factory=list)
    suggested_steps: List[Tuple[str, List[str]]] = field(default_factory=list)


@dataclass
class _DispatchOutcome:
    """Outcome of dispatching one keyword during a heal.

    ``ok`` is True when the keyword executed successfully; ``done`` is True when it
    also completes the original keyword's goal.
    """

    ok: bool
    done: bool


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
        "target": {
            "type": "string",
            "description": (
                "A ref from CURRENT SCREEN (e.g. e7) for the element of press_element or "
                "enter_text; empty otherwise."
            ),
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
}


HEAL_SYSTEM_PROMPT = """\
You are the last-resort self-healing layer of a UI test-automation framework. The normal element \
locators (XPath, on-screen text, OCR, image matching) all failed to find the target of the keyword \
described below. Drive the UI with the listed keywords, one call per turn, until that keyword's \
goal is achieved.

`press_element` is the main tool. When CURRENT SCREEN rows are listed, name its element by ref in \
`target`. Otherwise give it the target's visible text - exact text, content-desc or resource id \
from the condensed hierarchy - and the framework locates it through its full ladder (XPath -> \
text -> OCR -> image), which is more reliable than coordinates.

If the target is visible, act on it and set `completed` to true. If it is not, reveal it first - \
scroll, press a menu, or type into a search field - with `completed` false, then act on it by \
text. For system buttons use `press_keycode` with the Android keycode (HOME=3, BACK=4, \
RECENTS=187, ENTER=66). Use `press_by_percentage` only for a target that has no text and no \
keycode.

The two gesture keywords name directions differently:
- `scroll` names the way you move through the content: "down" reveals content further down.
- `swipe_by_percentage` names the way the finger moves: "up" (finger drags bottom to top) reveals \
content further down.

Reply `done` once the original keyword's goal is achieved, or `give_up` when no recoverable next \
action remains. Keep `reason` short.
"""

_MAX_THOUGHT_CHARS = 160


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


class AISelfHealHandler:
    """Bounded loop that drives the device via keyword calls to land a failed keyword."""

    def __init__(
        self,
        llm: LLMInterface,
        keyword_executor: KeywordExecutor,
        keyword_catalog: KeywordCatalog,
        *,
        max_steps: int = 5,
        curate: bool = False,
    ) -> None:
        """``curate`` is off by default (each curation is an extra LLM call);
        ActionKeyword enables it to keep the surfaced suggested_steps minimal.
        """
        self.llm = llm
        self.keyword_executor = keyword_executor
        self.keyword_catalog = keyword_catalog
        self.max_steps = max_steps
        self.curate = curate

    def _execute_single_step(
        self,
        step: int,
        ctx: HealContext,
        screenshot_provider: ScreenshotProvider,
        pagesource_provider: PagesourceProvider,
        catalog: List[HealKeywordSpec],
        attempted: List[str],
        succeeded: List[Tuple[str, List[str]]],
        heal_log: List[str],
        refs: RefTable,
        snapshot_provider: SnapshotProvider | None = None,
    ) -> Optional[HealResult]:
        """Execute one self-heal iteration; return a terminal HealResult or None to continue.

        Each dispatched line is recorded in ``attempted`` for the diagnostic; only the
        ones that passed are recorded in ``succeeded`` as the clean recovery. Returning
        None means an intermediate or failed step changed the UI, so the loop re-observes.
        """
        png = self._safe_call(screenshot_provider)
        if not png:
            return HealResult(False, message="No screenshot available for self-heal.")
        elements = self._safe_call(snapshot_provider)
        snap = refs.snapshot(elements if isinstance(elements, list) else [])
        page_source = None if snap.rows and not snap.hidden else self._safe_call(pagesource_provider)

        prompt = self._build_prompt(ctx, page_source, catalog, heal_log, snap)
        try:
            raw = self.llm.generate_json(
                prompt, HEAL_ACTION_SCHEMA, images=[png],
                system=HEAL_SYSTEM_PROMPT,
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

        try:
            action = self._bind_target(action, refs, catalog)
        except TargetError as exc:
            line = self._build_line(action.keyword, [action.target, *action.params] if action.target else action.params)
            attempted.append(line)
            reason = str(exc)
            if exc.kind == "no_locator":
                reason = f"{action.target} has no unique locator or text; act on another element."
            heal_log.append(f"{line} -> FAIL: {reason}")
            return None

        try:
            outcome = self._dispatch(action)
        except Exception as exc:  # noqa: BLE001 - a keyword error ends the heal cleanly
            return HealResult(False, action=action, message=f"Keyword failed: {exc}")

        line = self._build_line(action.keyword, action.params)
        attempted.append(line)
        heal_log.append(f"{line} -> {'PASS' if outcome.ok else 'FAIL'}")
        if outcome.ok:
            record = action.record_params if action.record_params is not None else action.params
            succeeded.append((action.keyword, list(record)))
        if outcome.done:
            return HealResult(True, action=action, message=action.reason or "Healed.")
        return None

    def heal(
        self,
        ctx: HealContext,
        screenshot_provider: ScreenshotProvider,
        pagesource_provider: PagesourceProvider,
        snapshot_provider: SnapshotProvider | None = None,
    ) -> HealResult:
        """Attempt to recover the failed keyword. Never raises — returns ok=False on any problem.

        When ``snapshot_provider`` returns screen elements, the prompt lists them as ref
        rows in place of the page source, and the model can name its element by ref.
        """
        catalog = self.keyword_catalog()
        attempted: List[str] = []
        succeeded: List[Tuple[str, List[str]]] = []
        heal_log: List[str] = []
        refs = RefTable()

        for step in range(self.max_steps):
            result = self._execute_single_step(
                step, ctx, screenshot_provider, pagesource_provider, catalog, attempted,
                succeeded, heal_log, refs, snapshot_provider,
            )
            if result is not None:
                result.steps_taken = list(attempted)
                result.suggested_steps = self._finalize_suggested(ctx, result, succeeded)
                return result

        tried = "; ".join(attempted) if attempted else "no actions attempted"
        return HealResult(
            False,
            message=f"Self-heal step budget exhausted. Tried: {tried}",
            steps_taken=list(attempted),
            suggested_steps=list(succeeded),
        )

    def _finalize_suggested(
        self, ctx: HealContext, result: HealResult, succeeded: List[Tuple[str, List[str]]],
    ) -> List[Tuple[str, List[str]]]:
        """The clean recovery sequence to report: the passed steps, curated on success.

        On a failed heal we return the raw passed steps (partial progress, no goal to
        curate against). On success, when curation is enabled and there is more than one
        step, prune to the minimal reproducing subset; a failed/degenerate curation keeps
        all steps (``curate_steps`` returns ``None``), so a working recovery is never lost.
        """
        if not result.ok or not self.curate or len(succeeded) <= 1:
            return list(succeeded)
        prompt = self._build_curation_prompt(ctx, succeeded)
        indices = curate_steps(self.llm, prompt, len(succeeded))
        if indices is None:
            return list(succeeded)
        return [succeeded[i] for i in indices]

    @staticmethod
    def _build_curation_prompt(
        ctx: HealContext, succeeded: List[Tuple[str, List[str]]],
    ) -> str:
        """Text-only curation prompt. Candidate steps are the passed heal steps, 1-based.

        The goal is the original keyword the normal locators failed on; only the
        successful steps are selectable, so no FAILED context section is needed here.
        """
        params = " ".join(str(p) for p in ctx.intent_params)
        goal = f"{ctx.intent_keyword} {params}".rstrip()
        lines = [
            f"INSTRUCTION (goal that was achieved by self-heal): {goal}",
            "",
            "CANDIDATE STEPS (selectable — put these numbers in `keep`):",
        ]
        for idx, (kw, kw_params) in enumerate(succeeded, 1):
            lines.append(f"  {idx}. {kw} {kw_params}")
        lines.append("")
        lines.append(
            "Return `keep` = the 1-based CANDIDATE numbers to run, in any order, that reproduce "
            "the goal. Drop dead-ends, backtracks, overshoot-then-correct, and no-op steps. When "
            "unsure, KEEP."
        )
        return "\n".join(lines)

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

    @staticmethod
    def _bind_target(
        action: HealAction, refs: RefTable, catalog: list[HealKeywordSpec],
    ) -> HealAction:
        """Put the element ``target`` names into the keyword's element param, a ref as its locator.

        A leading param that repeats the target is dropped, and so is one that is close
        to it when the call has more params than the keyword takes. A row with no
        locator is acted on by its label when that names one element. Raises
        ``TargetError`` for a ref that is not on the current screen or a row with neither.
        """
        signature = next((s.signature for s in catalog if s.name == action.keyword), "")
        parts = signature.split()
        if parts[1:2] != ["<element>"]:
            return action
        params = list(action.params)
        target = action.target.strip()
        if not target and params and REF_RE.match(params[0].strip()):
            target = params[0].strip()
        if not target:
            return action
        row = refs.resolve(target)
        snap = refs.latest()
        repeats = {_fold(target), _fold(row.label) if row else ""} - {""}
        if params:
            first = _fold(params[0])
            surplus = len(params) > len(parts[2:])
            if first in repeats or (first and surplus and any(first in r or r in first for r in repeats)):
                params = params[1:]
        if row is None or snap is None:
            return replace(action, target=target, params=[target, *params])
        try:
            element = live_ladder(row, snap)[0]
            record = AISelfHealHandler._record_element(row, snap)
        except TargetError:
            element = record = acting_ladder(row, snap)[0]
        return replace(action, target=target, params=[element, *params], record_params=[record, *params])

    @staticmethod
    def _record_element(row: Row, snap: Snapshot) -> str:
        """The locator a replay should use: a stable rung, else a label unique on the screen."""
        ladder = saved_ladder(row, snap)
        if not loc_stable(ladder[0]) and row.label and sum(r.label == row.label for r in snap.all_rows) == 1:
            return ladder[-1]
        return ladder[0]

    def _dispatch(self, action: HealAction) -> _DispatchOutcome:
        """Execute one keyword via the injected executor.

        Returns ``_DispatchOutcome(ok, done)``: ``ok`` is whether the keyword executed
        successfully, ``done`` whether that success also completes the original goal.
        """
        if not action.keyword:
            return _DispatchOutcome(ok=False, done=False)

        line = self._build_line(action.keyword, action.params)
        result = self.keyword_executor(line)

        if not result.ok:
            internal_logger.debug("AI self-heal: keyword '%s' failed: %s", line, result.message)
            # Don't abort the whole heal on a single keyword failure — the LLM can
            # try a different approach on the next step, so let the UI settle first.
            time.sleep(_SETTLE_SECONDS)
            return _DispatchOutcome(ok=False, done=False)

        # A completing keyword with completed=True means the goal is done — return
        # immediately without waiting, since there's no next screenshot to settle for.
        if action.keyword not in _NON_COMPLETING_KEYWORDS and action.completed:
            return _DispatchOutcome(ok=True, done=True)

        # Intermediate/navigation step: let the UI settle before the next screenshot.
        time.sleep(_SETTLE_SECONDS)
        return _DispatchOutcome(ok=True, done=False)

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
            target=str(raw.get("target") or ""),
        )

    @staticmethod
    def _build_line(keyword: str, params: List[str]) -> str:
        """Build a keyword line string from keyword name and params."""
        if not params:
            return keyword
        return keyword + " " + " ".join(shlex.quote(p) for p in params)

    def _build_prompt(
        self, ctx: HealContext, page_source: Optional[str],
        catalog: List[HealKeywordSpec], heal_log: List[str], snap: Snapshot | None = None,
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

        if snap is not None and snap.rows:
            lines.append("")
            lines.append(
                'CURRENT SCREEN (one row per element: ref, class, "visible text", [actions], '
                "#id; a row without [...] is read-only text):"
            )
            lines.append(render_rows(replace(snap, hidden=0)))
            if snap.hidden:
                where = "the condensed hierarchy below shows the whole screen, and " if page_source else ""
                lines.append(
                    f"… {snap.hidden} more rows not listed; {where}`target` also takes an element's "
                    "exact visible text."
                )
            lines.append(
                "Name the element of press_element or enter_text by putting its ref in "
                "`target` (e.g. e7); leave it out of `params`."
            )
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
        if heal_log:
            lines.append("")
            lines.append("SELF-HEAL STEPS SO FAR (PASS = executed, not goal reached):")
            lines.extend(f"  {idx}. {entry}" for idx, entry in enumerate(heal_log, 1))
        lines.append("")
        lines.append("The attached image is the CURRENT screen. Decide the SINGLE next action as JSON.")
        return "\n".join(lines)
