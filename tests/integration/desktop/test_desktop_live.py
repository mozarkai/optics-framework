"""optics live on a desktop app: typed keywords, a natural-language instruction and the saved suite.

A scripted model stands in for the LLM; the screen reads, refs, keywords, driver and save path are the real ones.
"""

import json
from pathlib import Path

import pytest

from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.llm_interface import LLMInterface
from optics_framework.helper.live import LiveController, NLRunStatus
from tests.integration.desktop.support import driver_config, junit, run_optics, write_project

pytestmark = pytest.mark.black_box

NOTE = "typed by an instruction"


class ScriptedModel(LLMInterface):
    """Types the note into the field the SCREEN shows, then says done once the SCREEN shows the note."""

    def __init__(self, desktop):
        self.desktop, self.prompts = desktop, []

    def generate(self, prompt, images=None, system=None, response_schema=None, *, temperature=None):
        self.prompts.append(prompt)
        if NOTE in prompt.split("SCREEN", 1)[-1]:
            return json.dumps({"last_step": "pass", "thought": "The note shows.", "action": "done",
                               "expect_present": [NOTE]})
        return json.dumps({"last_step": "none", "thought": "Type the note.", "action": "type",
                           "target": self.desktop.field_ref(prompt), "text": NOTE, "expect_present": [NOTE]})


def test_an_instruction_types_a_note_and_saves_a_suite_that_runs(tmp_path, host, desktop):
    folder = write_project(tmp_path, driver_config(host, desktop.editor), {})
    controller = LiveController(folder)
    model = ScriptedModel(desktop)
    controller.session.optics.get_llm = lambda: InstanceFallback([model])
    steps = []
    try:
        assert controller.run_keyword("Launch App").status.value == "PASS"
        summary = controller.run_natural_language(f'Type "{NOTE}" into the editor', on_step=steps.append)
        saved = controller.save("Notes", "Write")
        assert controller.run_keyword("Close And Terminate App").status.value == "PASS"
    finally:
        controller.manager.terminate_session(controller.session_id)

    assert summary.status is NLRunStatus.PASS, (summary, steps)
    assert len(model.prompts) == 2
    modules = Path(saved.modules_path).read_text(encoding="utf-8")
    assert "Launch App" in modules, modules
    assert NOTE in modules, modules
    Path(saved.modules_path).write_text(modules + "Write,Close And Terminate App\n", encoding="utf-8")
    ran = run_optics("execute", folder)
    assert ran.returncode == 0, modules + ran.stdout[-3000:] + junit(tmp_path)
