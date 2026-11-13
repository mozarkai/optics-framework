"""How the runners carry popups: the events they publish and the run a popup stops."""
import time
from types import SimpleNamespace

from optics_framework.common import test_context
from optics_framework.common.events import EventStatus
from optics_framework.common.models import CaseKind, KeywordNode, ModuleNode
from optics_framework.common.models import TestCaseNode as _TestCaseNode
from optics_framework.common.popup_guard import PopupRun
from optics_framework.common.runner.printers import NullResultPrinter
from optics_framework.common.runner.test_runnner import PytestRunner, SetupGuard, popup_event
from optics_framework.common.runner.test_runnner import TestRunner as _TestRunner
from optics_framework.common.error import Code


def popup(verdict="handled", **over):
    return {"type": "popup", "verdict": verdict, "rule": "Rate Us", "popup_text": "Rate Us", "code": None, **over}


class Events:
    def __init__(self):
        self.events = []

    async def publish_event(self, event):
        self.events.append(event)


def test_a_stopped_run_blocks_every_kind_of_case_but_the_suite_teardown():
    guard = SetupGuard()
    guard.stop("Run stopped by a popup: Another device")
    blocked = {kind for kind in CaseKind if guard.reason(kind)}
    assert blocked == set(CaseKind) - {CaseKind.SUITE_TEARDOWN}
    assert "Another device" in guard.reason(CaseKind.TEST)


def test_a_popup_event_carries_its_fields_in_args_and_the_test_case_from_the_context():
    token = test_context.current_test_case.set("Check balance")
    try:
        event = popup_event(popup(), "s1", "Press Element", "kw-1", "mod-1", module="Open dashboard", step_index=3)
    finally:
        test_context.current_test_case.reset(token)
    assert (event.entity_type, event.entity_id, event.name, event.parent_id) == ("popup", "kw-1", "Press Element", "mod-1")
    assert event.extra == {"session_id": "s1"}
    assert event.args["test_case"] == "Check balance" and event.args["module"] == "Open dashboard"
    assert event.args["step_index"] == 3 and event.args["rule"] == "Rate Us" and event.args["session_id"] == "s1"


def test_a_handled_popup_passes_and_one_that_stopped_the_step_fails():
    assert popup_event(popup("handled"), "s", "k", "1", "m").status is EventStatus.PASS
    assert popup_event(popup("none"), "s", "k", "1", "m").status is EventStatus.PASS
    for verdict in ("limit", "stopped", "unknown"):
        assert popup_event(popup(verdict, code=Code.E0208.value), "s", "k", "1", "m").status is EventStatus.FAIL
    assert popup_event(popup("stopped", code=Code.E0208.value), "s", "k", "1", "m").code == Code.E0208.value


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
    runner._start_run = runner._capture_end_of_run_artifacts = runner._write_failed_steps_elements = lambda: None
    await runner.run_all()
    return executed, skipped


async def test_a_stop_run_popup_skips_the_cases_not_started_and_still_runs_the_suite_teardown():
    executed, skipped = await _run_all(stops_during="a")
    assert executed == ["a", "suite_td"]
    assert [name for name, _ in skipped] == ["b", "td"]
    assert all("Another device is trying to log in" in reason for _, reason in skipped)


async def test_a_run_no_popup_stopped_runs_every_case():
    executed, skipped = await _run_all(stops_during=None)
    assert executed == ["a", "b", "td", "suite_td"] and skipped == []


def test_the_sync_runner_blocks_the_next_cases_once_a_popup_stopped_the_run():
    runner = PytestRunner.__new__(PytestRunner)
    runner._guard, runner.popup_run = SetupGuard(), PopupRun()
    runner._last_failure_reason = runner._last_failure_code = None
    runner._queue_test_case_event = lambda *args, **kwargs: None
    result = SimpleNamespace(status="", elapsed="")
    runner._end_test_case(result, "id", "a", CaseKind.TEST, False, False, time.time())
    assert runner._guard.reason(CaseKind.TEST) is None
    runner.popup_run.stop = "Another device"
    runner._end_test_case(result, "id", "a", CaseKind.TEST, False, False, time.time())
    assert "Another device" in runner._guard.reason(CaseKind.TEST)
    assert runner._guard.reason(CaseKind.SUITE_TEARDOWN) is None
