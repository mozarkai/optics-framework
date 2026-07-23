"""Shared LLM-driven step curation for the NL agent and AI self-heal.

Both :class:`~optics_framework.common.nl_agent.NaturalLanguageAgent` and
:class:`~optics_framework.common.ai_self_heal.AISelfHealHandler` drive the UI one
keyword at a time and finish with an ordered list of *successful* steps that may
still contain dead-ends, backtracks, overshoot-then-correct gestures, or no-ops.
This module asks the LLM for the minimal ordered subset that reproduces the goal.

The two loops are documented as siblings that must be kept in sync (see CLAUDE.md);
sharing the schema, system prompt, and index-coercion here keeps their curation
identical. Each caller still builds its own user prompt, because the candidate/failed
step context differs between them.
"""

from typing import Any, Dict, List, Optional

from optics_framework.common.error import OpticsError
from optics_framework.common.llm_interface import LLMInterface
from optics_framework.common.logging_config import internal_logger


# Flat (no anyOf) — Gemini's response_schema support for anyOf/discriminated unions
# is unreliable.
CURATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "keep": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "1-based indices of the CANDIDATE steps to keep, in any order.",
        },
        "reason": {
            "type": "string",
            "description": "Brief justification for which steps were dropped.",
        },
    },
    "required": ["keep", "reason"],
    "propertyOrdering": ["keep", "reason"],
}


CURATION_SYSTEM_PROMPT = """\
You are cleaning up a UI-automation recording. An instruction was just fulfilled by running a \
sequence of keywords, one at a time. Some of those keywords were dead-ends, backtracks (e.g. \
navigating somewhere then pressing back), overshoot-then-correct gestures, or no-op actions that \
did not contribute to the final result.

Your job: return the MINIMAL ordered subset of the CANDIDATE steps that a fresh run must execute, \
top to bottom, to reproduce the goal deterministically. Drop steps that do not contribute.

RULES:
- `keep` is a list of the 1-based CANDIDATE step numbers to keep. Only CANDIDATE numbers are valid.
- Never reference the FAILED context steps — they already failed and are excluded.
- Order in `keep` does not matter; it will be re-sorted into execution order.
- When in doubt, KEEP the step. Never drop a step that might be needed — a broken script is far \
worse than a slightly long one. If every step contributed, keep them all.
"""


def curate_steps(llm: LLMInterface, prompt: str, step_count: int) -> Optional[List[int]]:
    """Ask the LLM which of ``step_count`` candidate steps to keep.

    ``prompt`` is the caller-built user prompt; it must number the selectable candidate
    steps 1..``step_count`` (see each caller's prompt builder). Returns the kept indices
    as **0-based, deduplicated, sorted** integers, or ``None`` to mean "keep all".

    Every abnormal outcome returns ``None`` so a working recording is never lost:
    too few steps to prune, an LLM error, a malformed reply, no valid indices, or the
    model keeping (or dropping) everything.
    """
    if step_count <= 1:
        return None  # nothing to prune
    try:
        raw = llm.generate_json(
            prompt, CURATION_SCHEMA, images=None,
            system=CURATION_SYSTEM_PROMPT, temperature=0.0,
        )
    except OpticsError as exc:
        internal_logger.debug("Step curation: LLM error, keeping all steps: %s", exc.message)
        return None
    except Exception as exc:  # noqa: BLE001 - curation must never break a working recording
        internal_logger.debug("Step curation: unexpected error, keeping all steps: %s", exc)
        return None
    return _select_indices(raw, step_count)


def _select_indices(raw: Any, step_count: int) -> Optional[List[int]]:
    """Coerce the model's ``keep`` list to valid 0-based indices, or ``None`` for keep-all."""
    if not isinstance(raw, dict):
        return None
    keep_raw = raw.get("keep")
    if not isinstance(keep_raw, list):
        return None

    # Reject bools/non-ints, range-check, dedup, sort.
    seen: set[int] = set()
    indices: List[int] = []
    for value in keep_raw:
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        zero = value - 1  # prompt uses 1-based candidate numbering
        if 0 <= zero < step_count and zero not in seen:
            seen.add(zero)
            indices.append(zero)
    if not indices or len(indices) == step_count:
        return None  # dropped everything, or kept everything -> keep all
    indices.sort()
    internal_logger.debug(
        "Step curation: kept %d of %d steps (%s)", len(indices), step_count, raw.get("reason", "")
    )
    return indices
