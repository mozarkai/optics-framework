
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.step_curation import (
    CURATION_SCHEMA,
    curate_steps,
)


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


def test_prunes_to_kept_indices_zero_based_sorted():
    llm = _LLM({"keep": [3, 1], "reason": "drop 2"})
    assert curate_steps(llm, "p", 3) == [0, 2]


def test_llm_error_keeps_all():
    assert curate_steps(_LLM(OpticsError(Code.E0801, message="boom")), "p", 3) is None
