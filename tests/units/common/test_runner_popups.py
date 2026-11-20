"""How the runners carry popups: the events they publish and the run a popup stops."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from optics_framework.common.models import CaseKind, KeywordNode, ModuleNode
from optics_framework.common.models import TestCaseNode as _TestCaseNode
from optics_framework.common.popup_guard import PopupRun
from optics_framework.common.runner.printers import NullResultPrinter
from optics_framework.common.runner.test_runnner import TestRunner as _TestRunner


def popup(verdict="handled", **over):
    return {"type": "popup", "verdict": verdict, "rule": "Rate Us", "popup_text": "Rate Us", "code": None, **over}


class Events:
    def __init__(self):
        self.events = []

    async def publish_event(self, event):
        self.events.append(event)


async def test_the_runner_publishes_a_keywords_popup_events_with_its_module_and_step_number():
    runner = _TestRunner.__new__(_TestRunner)
    runner.session_id, runner.event_manager, runner.popup_run = "s1", Events(), PopupRun()
    first, second = KeywordNode(id="k1", name="Launch App"), KeywordNode(id="k2", name="Press Element")
    module = ModuleNode(id="m1", name="Open dashboard", keywords_head=first)
    first.next = second
    runner.popup_run.events += [popup("handled"), popup("none")]
    await runner._publish_popup_events(second, module)
    assert [e.args["step_index"] for e in runner.event_manager.events] == [2, 2]
    assert {e.args["module"] for e in runner.event_manager.events} == {"Open dashboard"}
    assert runner.popup_run.events == [], "drained, so the next keyword does not republish them"
    await runner._publish_popup_events(first, module)
    assert len(runner.event_manager.events) == 2


def _suite():
    cases = [("a", CaseKind.TEST), ("b", CaseKind.TEST), ("td", CaseKind.TEARDOWN), ("suite_td", CaseKind.SUITE_TEARDOWN)]
    nodes = [_TestCaseNode(name=name, kind=kind) for name, kind in cases]
    for node, following in zip(nodes, nodes[1:]):
        node.next = following
    return nodes[0]


async def _run_all(stops_during):
    runner = _TestRunner.__new__(_TestRunner)
    runner.test_cases, runner.popup_run = _suite(), PopupRun()
    runner.result_printer, runner._last_failure_reason = NullResultPrinter(), None
    executed, skipped = [], []

    async def execute(name):
        executed.append(name)
        if name == stops_during:
            runner.popup_run.stop = "Another device is trying to log in"
        return SimpleNamespace(status="FAIL" if name == stops_during else "PASS")

    async def skip(node, reason):
        skipped.append((node.name, reason))

    runner.execute_test_case, runner._skip_test_case = execute, skip
    runner._start_run = runner._write_failed_steps_elements = lambda: None
    runner._capture_end_of_run_artifacts = AsyncMock()
    await runner.run_all()
    return executed, skipped


async def test_a_stop_run_popup_skips_the_cases_not_started_and_still_runs_the_suite_teardown():
    executed, skipped = await _run_all(stops_during="a")
    assert executed == ["a", "suite_td"]
    assert [name for name, _ in skipped] == ["b", "td"]
    assert all("Another device is trying to log in" in reason for _, reason in skipped)
