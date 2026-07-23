"""Unit tests for the shared LLM step-curation helper.

``curate_steps`` is reused by the NL agent and AI self-heal; it must never lose a working
recording, so every abnormal outcome (too few steps, LLM error, malformed reply, all/none
kept) returns None ("keep all").
"""
import pytest

from optics_framework.common.error import OpticsError, Code
from optics_framework.common.step_curation import (
    CURATION_SCHEMA,
    curate_steps,
)

pytestmark = pytest.mark.white_box


class _LLM:
    def __init__(self, reply):
        self._reply = reply
        self.calls = 0

    def generate_json(self, prompt, response_schema, images=None, system=None, temperature=None):
        self.calls += 1
        assert response_schema is CURATION_SCHEMA
        assert images is None
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def test_no_anyof_in_schema():
    import json
    assert "anyOf" not in json.dumps(CURATION_SCHEMA)


def test_single_step_skips_llm():
    llm = _LLM({"keep": [1], "reason": "r"})
    assert curate_steps(llm, "p", 1) is None
    assert llm.calls == 0


def test_prunes_to_kept_indices_zero_based_sorted():
    llm = _LLM({"keep": [3, 1], "reason": "drop 2"})
    assert curate_steps(llm, "p", 3) == [0, 2]


def test_dedups_and_range_checks():
    llm = _LLM({"keep": [3, 1, 1, 99, 0], "reason": "dup/out-of-range"})
    # 0 and 99 are out of the 1..3 range; 1 is deduped -> {1,3} -> 0-based [0, 2].
    assert curate_steps(llm, "p", 3) == [0, 2]


def test_keep_all_returns_none():
    llm = _LLM({"keep": [1, 2, 3], "reason": "all needed"})
    assert curate_steps(llm, "p", 3) is None


def test_drop_all_returns_none():
    llm = _LLM({"keep": [], "reason": "none"})
    assert curate_steps(llm, "p", 3) is None


@pytest.mark.parametrize("reply", [
    "not a dict",
    {"reason": "no keep key"},
    {"keep": "not a list", "reason": "r"},
    {"keep": [True, False], "reason": "bools are not indices"},
])
def test_malformed_replies_keep_all(reply):
    assert curate_steps(_LLM(reply), "p", 3) is None


def test_llm_error_keeps_all():
    assert curate_steps(_LLM(OpticsError(Code.E0801, message="boom")), "p", 3) is None


def test_unexpected_error_keeps_all():
    assert curate_steps(_LLM(RuntimeError("kaboom")), "p", 3) is None
