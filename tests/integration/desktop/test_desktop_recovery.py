"""What optics does when a desktop step goes wrong: on-screen error detection and AI self-heal."""

import json

import pytest

from optics_framework import Optics
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.llm_interface import LLMInterface
from tests.integration.desktop.support import driver_config, junit, run_optics, write_project

pytestmark = pytest.mark.black_box


def test_an_error_message_on_the_final_screen_fails_the_run(tmp_path, host, desktop):
    folder = write_project(tmp_path, driver_config(host, desktop.editor), {
        "test_cases/test_cases.csv": "test_case,test_step\nNotes,Write\nSuite Teardown,Close\n",
        "modules/modules.csv": "module_name,module_step,param_1,param_2\n"
                               f"Write,Launch App\nWrite,Enter Text,{desktop.field},Your session expired\n"
                               "Close,Close And Terminate App\n",
        "test_data/error_definitions.csv": "error_code,match_string,description,severity\n"
                                           "ERR_009,Session expired,User session timed out,high\n",
    })

    ran = run_optics("execute", folder)
    assert ran.returncode == 1, ran.stdout[-3000:]
    assert "ERR_009" in junit(tmp_path)
    [found] = (tmp_path / "execution_output").glob("detected_errors_*.json")
    assert "ERR_009" in found.read_text(encoding="utf-8")


class ScriptedHealer(LLMInterface):
    """Types the failed step's value into the field the CURRENT SCREEN shows, then says done."""

    def __init__(self, desktop):
        self.desktop, self.prompts = desktop, []

    def generate(self, prompt, images=None, system=None, response_schema=None, *, temperature=None):
        self.prompts.append(prompt)
        if len(self.prompts) > 1:
            return json.dumps({"reason": "The note shows.", "action": "done"})
        return json.dumps({"reason": "The editor is the field.", "action": "keyword", "keyword": "enter_text",
                           "target": self.desktop.field_ref(prompt), "params": ["<value>"], "completed": True})


def test_self_heal_types_into_the_field_a_broken_locator_meant(host, desktop, tmp_path):
    optics = Optics()
    optics.setup(config={**driver_config(host, desktop.editor), "ai_self_heal": True,
                         "execution_output_path": str(tmp_path)})
    healer = ScriptedHealer(desktop)
    optics.launch_app()
    try:
        optics.action_keyword._llm = InstanceFallback([healer])
        optics.enter_text(f'{desktop.field}[@name="A field that moved"]', "healed note")
        assert optics.get_text(desktop.field) == "healed note"
    finally:
        optics.force_terminate_app(desktop.editor)
        optics.quit()
    assert healer.prompts
