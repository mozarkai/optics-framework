"""TestRunner on a session with no driver: the keywords are plain functions, the events are recorded."""
import pytest

from optics_framework.common import test_context
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.events import EventStatus
from optics_framework.common.runner.printers import NullResultPrinter
from optics_framework.common.runner.test_runnner import TestRunner as _TestRunner
from tests.units.fakes import FakeSession, RecordingEvents, elements

RUNNING, PASS, FAIL, ERROR, NOT_RUN = (EventStatus.RUNNING, EventStatus.PASS, EventStatus.FAIL, EventStatus.ERROR,
                                       EventStatus.NOT_RUN)
SESSION = {"session_id": "s1"}
TEST = {**SESSION, "kind": "test"}
NOT_A_KEYWORD = {**SESSION, "hint": "Run 'optics list' to see the keywords."}
NOT_FOUND = {**SESSION, "hint": "Make sure that the screen shows the element when this step starts, or correct its locator."}


def record(entity_type, name, status, message="", code=None, extra=SESSION, args=None):
    return {"entity_type": entity_type, "name": name, "status": status, "message": message, "code": code,
            "extra": extra, "args": args}


def failed(keyword, status, message, code, extra=SESSION):
    """The keyword, module M and test case T failing with one message, as the runner reports a failed step."""
    return [
        record("keyword", keyword, status, message, code, extra),
        record("module", "M", status, message, code, extra),
        record("test_case", "T", status, message, code, {**extra, **TEST}),
    ]


def started(*keywords):
    return [record("test_case", "T", RUNNING, extra=TEST), record("module", "M", RUNNING),
            *(record("keyword", k, RUNNING) for k in keywords)]


def setup(test_cases, modules, keyword_map, element_data=None):
    events = RecordingEvents()
    runner = _TestRunner(FakeSession(test_cases, modules, element_data), keyword_map, NullResultPrinter(), events)
    return runner, events


class Locators:
    """A press_element that records each locator it is given, and fails every one with ``code`` when given one."""

    def __init__(self, code=None):
        self.code, self.tried = code, []

    def press_element(self, element):
        self.tried.append(element)
        if self.code:
            raise OpticsError(self.code, message="miss")


def cards(count):
    return elements(card=[f"//v{i}" for i in range(count)])


class TestRunAll:
    async def test_a_keyword_that_passes(self):
        press = Locators()
        runner, events = setup({"T": ["M"]}, {"M": [("Press Element", ["${card}"])]},
                               {"press_element": press.press_element}, cards(2))
        await runner.run_all()
        assert press.tried == ["//v0"]
        assert events.records() == [
            *started("Press Element"), record("keyword", "Press Element", PASS), record("module", "M", PASS),
            record("test_case", "T", PASS, extra=TEST),
        ]

    async def test_every_locator_of_an_element_not_found(self):
        press = Locators(Code.X0201)
        runner, events = setup({"T": ["M"]}, {"M": [("Press Element", ["${card}"])]},
                               {"press_element": press.press_element}, cards(3))
        await runner.run_all()
        assert press.tried == ["//v0", "//v1", "//v2"]
        assert events.records()[-3:] == failed(
            "Press Element", FAIL, "All 3 tries of keyword 'Press Element' failed:\n'//v0': miss\n'//v1': miss\n'//v2': miss",
            "X0201", NOT_FOUND)

    async def test_more_locators_than_the_ladder_tries(self):
        press = Locators(Code.E0201)
        runner, events = setup({"T": ["M"]}, {"M": [("Press Element", ["${card}"])]},
                               {"press_element": press.press_element}, cards(25))
        await runner.run_all()
        assert press.tried == [f"//v{i}" for i in range(20)]
        assert events.records()[-3:] == failed(
            "Press Element", FAIL,
            "All 20 tries of keyword 'Press Element' failed (tried 20 of 25 combinations):\n(15 earlier tries not shown)\n"
            "'//v15': miss\n'//v16': miss\n'//v17': miss\n'//v18': miss\n'//v19': miss", "E0201", NOT_FOUND)

    @pytest.mark.parametrize(("code", "status", "hint"), [
        pytest.param(Code.E0105, FAIL, "Use a different keyword on this platform.",
                     id="a keyword the platform does not support"),
        pytest.param(Code.E0106, ERROR, "The device or the driver server stopped the session. Examine the device, then run again.",
                     id="a driver session that is gone"),
    ])
    async def test_an_error_that_is_not_element_not_found(self, code, status, hint):
        press = Locators(code)
        runner, events = setup({"T": ["M"]}, {"M": [("Press Element", ["${card}"])]},
                               {"press_element": press.press_element}, cards(3))
        await runner.run_all()
        assert press.tried == ["//v0"]
        assert events.records()[-3:] == failed("Press Element", status, "miss", code.value, {**SESSION, "hint": hint})

    async def test_an_unknown_keyword(self):
        runner, events = setup({"T": ["M"]}, {"M": [("Bogus Step", [])]}, {"known": lambda: None})
        await runner.run_all()
        assert events.records() == [*started("Bogus Step"), *failed("Bogus Step", FAIL, "'Bogus Step' is not a keyword.",
                                                                    "E0402", NOT_A_KEYWORD)]

    async def test_an_undefined_variable(self):
        press = Locators()
        runner, events = setup({"T": ["M"]}, {"M": [("Press Element", ["${missing}"])]}, {"press_element": press.press_element})
        await runner.run_all()
        assert press.tried == []
        assert events.records()[-3:] == failed(
            "Press Element", FAIL, "${missing} is not defined in the project's elements", "E0201",
            {**SESSION, "hint": "Add missing to an elements file, or correct the name."})

    async def test_a_step_that_is_not_a_keyword_or_a_module(self):
        runner, events = setup({"T": ["Bogus Step"]}, {}, {"known": lambda: None})
        await runner.run_all()
        assert events.records()[-2:] == [
            record("module", "Bogus Step", FAIL, "'Bogus Step' is not a keyword or a module.", "E0402", NOT_A_KEYWORD),
            record("test_case", "T", FAIL, "'Bogus Step' is not a keyword or a module.", "E0402", {**NOT_A_KEYWORD, **TEST}),
        ]

    async def test_a_popup_that_stops_the_step(self):
        tried = []

        def press_element(element):
            tried.append(element)
            test_context.popup_run.get().events.append({"type": "popup", "verdict": "limit", "rule": "Rate Us", "code": "E0407"})
            raise OpticsError(Code.E0407, message="Popup 'Rate Us' came back too often")

        runner, events = setup({"T": ["M"]}, {"M": [("Launch App", []), ("Press Element", ["${card}"])]},
                               {"press_element": press_element, "launch_app": lambda: None}, cards(2))
        await runner.run_all()
        assert tried == ["//v0"]
        message = "Popup 'Rate Us' came back too often"
        assert events.records()[-5:] == [
            record("keyword", "Press Element", RUNNING),
            record("keyword", "Press Element", FAIL, message, "E0407"),
            record("popup", "Press Element", FAIL, code="E0407", args={
                "type": "popup", "verdict": "limit", "rule": "Rate Us", "code": "E0407", "session_id": "s1", "test_case": "T",
                "module": "M", "step_index": 2}),
            record("module", "M", FAIL, message, "E0407"),
            record("test_case", "T", FAIL, message, "E0407", TEST),
        ]

    async def test_a_popup_handled_in_one_step_and_the_step_after_it(self):
        def launch_app():
            test_context.popup_run.get().events.append({"type": "popup", "verdict": "handled", "rule": "Rate Us"})

        runner, events = setup({"T": ["M"]}, {"M": [("Launch App", []), ("Press Element", ["${card}"])]},
                               {"launch_app": launch_app, "press_element": Locators().press_element}, cards(1))
        await runner.run_all()
        assert [r for r in events.records() if r["entity_type"] == "popup"] == [record("popup", "Launch App", PASS, args={
            "type": "popup", "verdict": "handled", "rule": "Rate Us", "session_id": "s1", "test_case": "T", "module": "M",
            "step_index": 1})]

    async def test_a_popup_that_stops_the_run(self):
        def log_in_elsewhere():
            test_context.popup_run.get().stop = "Another device is trying to log in"  # NOSONAR as the popup guard sets it
            raise OpticsError(Code.E0408, message="Popup 'Login elsewhere' ended the run")

        runner, events = setup({"A": ["Stop"], "B": ["Ok"], "Teardown": ["Ok"], "Suite Teardown": ["Ok"]},
                               {"Stop": [("Log In Elsewhere", [])], "Ok": [("Fine", [])]},
                               {"log_in_elsewhere": log_in_elsewhere, "fine": lambda: None})
        await runner.run_all()
        skipped = "Run stopped by a popup: Another device is trying to log in"
        teardown = {**SESSION, "kind": "teardown"}
        assert [r for r in events.records() if r["entity_type"] == "test_case" and r["status"] != RUNNING] == [
            record("test_case", "A", FAIL, "Popup 'Login elsewhere' ended the run", "E0408", TEST),
            record("test_case", "Teardown (A)", NOT_RUN, skipped, extra=teardown),
            record("test_case", "B", NOT_RUN, skipped, extra=TEST),
            record("test_case", "Teardown (B)", NOT_RUN, skipped, extra=teardown),
            record("test_case", "Suite Teardown", PASS, extra={**SESSION, "kind": "suite_teardown"}),
        ]


class TestDryRunAll:
    async def test_a_step_that_is_not_a_keyword_or_a_module(self):
        runner, events = setup({"T": ["Bogus Step"]}, {}, {})
        await runner.dry_run_all()
        assert events.records() == [
            record("test_case", "T", RUNNING, extra=TEST),
            record("module", "Bogus Step", RUNNING),
            record("module", "Bogus Step", FAIL, "'Bogus Step' is not a keyword or a module.", "E0402", NOT_A_KEYWORD),
            record("test_case", "T", FAIL, "'Bogus Step' is not a keyword or a module.", "E0402", {**NOT_A_KEYWORD, **TEST}),
        ]
